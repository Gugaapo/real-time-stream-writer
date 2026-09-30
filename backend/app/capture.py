"""One shared oMeiaUm capture: ffmpeg, Twitch chat, and the public transcript."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("livrinho.capture")


@dataclass
class Snapshot:
    grid: object | None = None
    chat: list[tuple[str, str]] = field(default_factory=list)
    speech: list[tuple[str, str]] | None = None
    speech_id: int | None = None
    chat_epoch: int = 0
    status: str = "stopped"
    detail: str = ""


class LiveCapture:
    """Starts when the first reader connects and stops when the last one goes idle."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._source = None
        self._chat = None
        self._grid = None
        self._chat_pairs: list[tuple[str, str]] = []
        self._chat_tail: tuple[str, str] | None = None
        self._chat_epoch = 0
        self._speech: list[tuple[str, str]] | None = None
        self._speech_id: int | None = None
        self._speech_known = False
        self._status = "stopped"
        self._detail = ""

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._status = "conectando"
            self._detail = "Abrindo a live."
            self._thread = threading.Thread(target=self._run, name="livrinho-capture", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        source = self._source
        chat = self._chat
        if source is not None:
            try:
                source.close()
            except Exception:
                logger.exception("closing the stream source")
        if chat is not None:
            try:
                chat.stop()
            except Exception:
                logger.exception("closing twitch chat")
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=5)
        with self._lock:
            if self._stop.is_set():
                self._status = "stopped"
                self._detail = ""
                self._grid = None

    def snapshot(self) -> Snapshot:
        with self._lock:
            grid = self._grid.copy() if self._grid is not None and hasattr(self._grid, "copy") else self._grid
            return Snapshot(
                grid=grid,
                chat=list(self._chat_pairs),
                speech=None if not self._speech_known else list(self._speech or []),
                speech_id=self._speech_id if self._speech_known else None,
                chat_epoch=self._chat_epoch,
                status=self._status,
                detail=self._detail,
            )

    def _set_status(self, status: str, detail: str) -> None:
        with self._lock:
            self._status = status
            self._detail = detail

    def _remember_chat(self, pairs: list[tuple[str, str]]) -> None:
        tail = pairs[-1] if pairs else None
        with self._lock:
            self._chat_pairs = list(pairs)
            if tail != self._chat_tail:
                if tail is not None:
                    self._chat_epoch += 1
                self._chat_tail = tail

    def _run(self) -> None:
        speech_thread = threading.Thread(target=self._poll_speech, name="livrinho-speech", daemon=True)
        speech_thread.start()
        while not self._stop.is_set():
            chat = None
            source = None
            try:
                source, chat = self._open()
                self._set_status("live", "Ao vivo.")
                for grid in source.frames():
                    if self._stop.is_set():
                        break
                    with self._lock:
                        self._grid = grid
                        self._status = "live"
                    if chat is not None:
                        self._remember_chat([(m.user, m.text) for m in chat.latest()])
            except Exception as exc:
                logger.warning("capture unavailable: %s", exc)
                self._set_status("offline", "A live parece offline.")
                with self._lock:
                    self._grid = None
                if self._stop.wait(15):
                    break
            finally:
                self._source = None
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        logger.exception("source close")
                if chat is not None:
                    try:
                        chat.stop()
                    except Exception:
                        logger.exception("chat stop")
                    self._chat = None
        self._stop.set()

    def _open(self):
        from streamkit.chat import TwitchChat
        from streamkit.sources import build_source

        source = build_source(
            f"twitch:{self.settings.channel}",
            width=self.settings.frame_width,
            height=self.settings.frame_height,
            fps=self.settings.fps,
        )
        self._source = source
        source.open()
        chat = TwitchChat(self.settings.channel, max_messages=24)
        self._chat = chat
        chat.start()
        return source, chat

    def _poll_speech(self) -> None:
        from stream_in_novel.speech import MeiaUmSpeech

        client = MeiaUmSpeech()
        while not self._stop.is_set():
            lines = client.latest(limit=8)
            status = client.status
            known = not (
                status.startswith("speech HTTP")
                or status.startswith("speech down")
                or status.startswith("speech connecting")
            )
            with self._lock:
                self._speech_known = known
                if known:
                    self._speech = [(line.speaker, line.text) for line in lines]
                    if lines:
                        self._speech_id = lines[-1].id
            self._stop.wait(5.0)
