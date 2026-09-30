"""Livrinho ao vivo."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.capture import LiveCapture
from app.config import Settings, get_settings
from app.routers.sessions import router
from app.runtime import BookRuntime, default_describer
from app.store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("livrinho")


def create_app(
    settings: Settings | None = None,
    capture=None,
    describer=None,
    *,
    sync_describe: bool = False,
    start_loop: bool = True,
    clock=None,
    encode_png=None,
) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.database_path)
    if capture is None:
        capture = LiveCapture(settings)
    if describer is None and encode_png is None and capture.__class__.__name__ == "LiveCapture":
        describer = default_describer(settings)
    runtime = BookRuntime(
        store,
        settings,
        capture,
        describer,
        encode_png=encode_png,
        sync_describe=sync_describe,
        clock=clock,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.runtime = runtime
        app.state.settings = settings
        stop = asyncio.Event()
        task = None
        runtime.bind_loop(asyncio.get_running_loop())
        if start_loop:
            task = asyncio.create_task(runtime.loop(stop), name="livrinho-tick")
            logger.info("narration loop started")
        yield
        stop.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        capture.stop()
        store.close()

    app = FastAPI(title="Livrinho ao vivo", version="0.1.0", lifespan=lifespan)
    origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["https://tossemideia.cloud"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """Build the production app on first use, so tests can import create_app alone."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
