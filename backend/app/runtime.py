"""Per-browser story workers. Model calls run only while a reader is connected."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

from streamkit.describe import DescribeError

from app.budget import Budget
from app.capture import Snapshot
from app.store import Store

logger = logging.getLogger("livrinho.runtime")

LANG = "pt-BR"
CAP_DETAIL = "O livrinho chegou no limite de US$ 10 deste mês. A escrita volta no próximo mês."


def default_describer(settings):
    from streamkit.describe import DEFAULT_BASE_URL, DEFAULT_MODEL, resolve_api_key

    from app.metered import MeteredDescriber

    key = (settings.deepseek_api_key or "").strip() or resolve_api_key(None)
    if not key:
        return None
    return MeteredDescriber(
        base_url=settings.novel_base_url or DEFAULT_BASE_URL,
        model=settings.novel_model or DEFAULT_MODEL,
        api_key=key,
    )


def default_encode_png(grid) -> bytes:
    from streamkit.render.png import png_bytes

    return png_bytes(grid.rgb_bytes, grid.width, grid.height)


@dataclass
class SessionRun:
    id: str
    style: str
    state: object
    custom_prompt: str = ""
    listeners: int = 0
    grace_until: float = 0.0
    busy: bool = False
    next_due: float = 0.0
    last_grid: object | None = None
    last_chat_count: int = 0
    last_speech_id: int | None = None
    revision: int = 0
    streaming: str = ""
    status: str = "pronto"
    detail: str = ""
    queues: list[asyncio.Queue] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class BookRuntime:
    def __init__(
        self,
        store: Store,
        settings,
        capture,
        describer=None,
        *,
        encode_png=None,
        sync_describe: bool = False,
        clock=None,
        budget: Budget | None = None,
    ) -> None:
        from stream_in_novel.novel import StoryState, should_describe

        from app.prompt import build_prompt

        self.store = store
        self.settings = settings
        self.capture = capture
        self.describer = describer
        self.encode_png = encode_png or default_encode_png
        self.sync_describe = sync_describe
        self.clock = clock or time.monotonic
        self.StoryState = StoryState
        self.build_prompt = build_prompt
        self.should_describe = should_describe
        self.budget = budget if budget is not None else Budget(
            store, cap_usd=getattr(settings, "monthly_cap_usd", 10.0)
        )
        self.runs: dict[str, SessionRun] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def acquire(self, session_id: str) -> bool | None:
        """True when this connection may spend tokens. None if the session does not exist. False if full."""
        with self._lock:
            run = self._load(session_id)
            if run is None:
                return None
            already = self._is_hot(run)
            if not already and self._hot_count() >= self.settings.max_live_sessions:
                return False
            run.listeners += 1
            run.grace_until = 0.0
            if run.status == "pronto" and not run.detail:
                run.detail = "Página aberta. A escrita começa neste capítulo."
            self.store.touch(session_id)
            return True

    def release(self, session_id: str, queue: asyncio.Queue | None) -> None:
        with self._lock:
            run = self.runs.get(session_id)
            if run is None:
                return
            if queue is not None and queue in run.queues:
                run.queues.remove(queue)
            run.listeners = max(0, run.listeners - 1)
            if run.listeners == 0:
                run.grace_until = self.clock() + self.settings.grace_seconds

    def public_state(self, session_id: str) -> dict | None:
        row = self.store.get_session(session_id)
        if row is None:
            return None
        with self._lock:
            run = self.runs.get(session_id)
            status = run.status if run else "pronto"
            detail = run.detail if run else ""
            draft = run.streaming if run else ""
        row["status"] = status
        row["detail"] = detail
        row["draft"] = draft
        return row

    def clear_text(self, session_id: str) -> dict | None:
        """Drop saved paragraphs. An in-flight model reply for this chapter is discarded."""
        if not self.store.clear_paragraphs(session_id):
            return None
        with self._lock:
            run = self.runs.get(session_id)
            if run is not None:
                run.revision += 1
                run.state = self.StoryState()
                run.streaming = ""
                run.last_grid = None
                run.last_chat_count = 0
                run.last_speech_id = None
                run.next_due = 0.0
                self._set_status(run, "pronto", "Texto limpo. O capítulo recomeça da cena atual.")
                self._emit(run, "cleared", {"paragraphs": []})
        return self.public_state(session_id)

    async def iter_events(self, session_id: str, request):
        run = self.runs[session_id]
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            run.queues.append(queue)
        try:
            hello = self.public_state(session_id)
            yield _sse("hello", hello)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse("ping", {})
                    continue
                yield _sse(event, data)
        finally:
            self.release(session_id, queue)

    async def loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("narration tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                continue

    def tick(self) -> None:
        now = self.clock()
        due: list[tuple[SessionRun, Snapshot]] = []
        with self._lock:
            self._evict_cold(now)
            hot = [run for run in self.runs.values() if self._is_hot(run)]
            if not hot:
                self.capture.stop()
                return
            self.capture.start()
            snap = self.capture.snapshot()
            for run in hot:
                if run.busy or now < run.next_due:
                    continue
                if snap.grid is None:
                    status = "offline" if snap.status == "offline" else "conectando"
                    self._set_status(run, status, snap.detail or "Aguardando um quadro da live.")
                    continue
                if not self._gate_open(run, snap):
                    run.next_due = now + self.settings.interval_seconds
                    self._set_status(run, "quieto", "A cena não mudou.")
                    continue
                if self.describer is None:
                    self._set_status(run, "sem_chave", "Falta a chave do modelo. Nada está sendo escrito.")
                    run.next_due = now + self.settings.interval_seconds
                    continue
                if not self.budget.can_afford():
                    run.next_due = now + self.settings.interval_seconds
                    self._set_status(run, "orcamento", CAP_DETAIL)
                    continue
                run.busy = True
                run.next_due = now + self.settings.interval_seconds
                self._set_status(run, "escrevendo", "Escrevendo o próximo trecho.")
                due.append((run, snap))
        for run, snap in due:
            if self.sync_describe:
                self._describe_once(run, snap)
            else:
                threading.Thread(
                    target=self._describe_once,
                    args=(run, snap),
                    name=f"livrinho-{run.id[:8]}",
                    daemon=True,
                ).start()

    def _gate_open(self, run: SessionRun, snap: Snapshot) -> bool:
        lines = [("", "")] * int(snap.chat_epoch)
        try:
            return self.should_describe(
                run.last_grid,
                snap.grid,
                lines,
                run.last_chat_count,
                self.settings.change_threshold,
                speech_id=snap.speech_id,
                last_speech_id=run.last_speech_id,
            )
        except ValueError:
            return True

    def _describe_once(self, run: SessionRun, snap: Snapshot) -> None:
        reserved = self.budget.try_reserve()
        if reserved is None:
            with self._lock:
                run.next_due = self.clock() + self.settings.interval_seconds
                self._set_status(run, "orcamento", CAP_DETAIL)
            with run.lock:
                run.busy = False
            return
        charged = False
        try:
            system, user = self.build_prompt(
                run.state,
                snap.chat,
                run.style,
                LANG,
                self.settings.channel,
                speech_lines=snap.speech,
                custom_prompt=run.custom_prompt,
            )
            png = self.encode_png(snap.grid)
            with self._lock:
                revision = run.revision

            def on_token(piece: str) -> None:
                with self._lock:
                    if run.revision != revision:
                        return
                    with run.lock:
                        run.streaming += piece
                        draft = run.streaming
                self._emit(run, "delta", {"text": draft})

            with self._lock:
                if run.revision == revision:
                    with run.lock:
                        run.streaming = ""
            text = self.describer.describe(system=system, user=user, image_png=png, on_token=on_token)
            charged = True
            text = " ".join((text or "").split())
            frozen = snap.grid.copy() if hasattr(snap.grid, "copy") else snap.grid
            with self._lock:
                if run.revision != revision:
                    return
                run.last_grid = frozen
                run.last_chat_count = int(snap.chat_epoch)
                run.last_speech_id = snap.speech_id
                if text:
                    run.state.add(text)
                    run.streaming = ""
                self._set_status(
                    run,
                    "pronto",
                    "Trecho novo no capítulo." if text else "O modelo não escreveu nada neste instante.",
                )
            if text:
                paragraph = self.store.add_paragraph(run.id, text)
                self._emit(run, "paragraph", paragraph)
        except DescribeError as exc:
            cooldown = 60.0 if getattr(exc, "status", None) == 429 else self.settings.interval_seconds
            with self._lock:
                run.next_due = self.clock() + cooldown
                if getattr(exc, "status", None) == 429:
                    self._set_status(run, "limite", "Limite da API. Pausa de um minuto.")
                else:
                    self._set_status(run, "erro", "A escrita pausou por um erro.")
            logger.warning("describe failed for %s: %s", run.id, exc)
        except Exception:
            logger.exception("describe crashed for %s", run.id)
            with self._lock:
                run.next_due = self.clock() + self.settings.interval_seconds
                self._set_status(run, "erro", "A escrita pausou por um erro.")
        finally:
            if charged:
                self.budget.commit(reserved, getattr(self.describer, "last_usage", None))
            else:
                self.budget.release(reserved)
            with run.lock:
                run.busy = False

    def _load(self, session_id: str) -> SessionRun | None:
        run = self.runs.get(session_id)
        if run is not None:
            return run
        row = self.store.get_session(session_id)
        if row is None:
            return None
        state = self.StoryState()
        for paragraph in row["paragraphs"]:
            state.add(paragraph["text"])
        run = SessionRun(
            id=row["id"],
            style=row["style"],
            state=state,
            custom_prompt=row.get("custom_prompt") or "",
        )
        self.runs[session_id] = run
        return run

    def _is_hot(self, run: SessionRun) -> bool:
        return run.listeners > 0 or run.grace_until > self.clock()

    def _hot_count(self) -> int:
        return sum(1 for run in self.runs.values() if self._is_hot(run))

    def _evict_cold(self, now: float) -> None:
        cold = [
            session_id
            for session_id, run in self.runs.items()
            if run.listeners == 0 and run.grace_until <= now and not run.busy and not run.queues
        ]
        for session_id in cold:
            del self.runs[session_id]

    def _set_status(self, run: SessionRun, status: str, detail: str) -> None:
        if run.status == status and run.detail == detail:
            return
        run.status = status
        run.detail = detail
        self._emit(run, "status", {"status": status, "detail": detail})

    def _emit(self, run: SessionRun, event: str, data: dict) -> None:
        payload = (event, data)
        for queue in list(run.queues):
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            else:
                queue.put_nowait(payload)


def _sse(event: str, data: dict | None) -> str:
    import json

    body = json.dumps(data or {}, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n"
