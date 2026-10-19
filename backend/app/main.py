"""FastAPI application: API, static web UI, Telegram bot and the follow-up scheduler."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, telegram
from .api import auth, routes
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("peygir")


async def scheduler_loop() -> None:
    from .agent import proactive

    interval = get_settings().scheduler_interval_seconds
    await asyncio.sleep(3)
    while True:
        try:
            await proactive.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    st = get_settings()
    db.init_engine()
    Path(st.upload_dir).mkdir(parents=True, exist_ok=True)
    if not st.gapgpt_api_key:
        log.warning("GAPGPT_API_KEY is not set — the assistant cannot reply until you add it to .env")
    background: list[asyncio.Task] = []
    tg_task = telegram.start()
    if tg_task:
        background.append(tg_task)
    if st.scheduler_enabled:
        background.append(asyncio.create_task(scheduler_loop()))
    yield
    for t in background:
        t.cancel()
    await telegram.stop()


def create_app() -> FastAPI:
    st = get_settings()
    app = FastAPI(title="Peygir — personal assistant", lifespan=lifespan)
    app.include_router(routes.public)
    app.include_router(routes.router)

    uploads = Path(st.upload_dir)
    uploads.mkdir(parents=True, exist_ok=True)

    @app.get("/uploads/{name}", include_in_schema=False, dependencies=[Depends(auth.require_auth)])
    async def upload_file(name: str):
        f = (uploads / name).resolve()
        if f.parent != uploads.resolve() or not f.is_file():
            return JSONResponse({"detail": "not found"}, status_code=404)
        return FileResponse(f)

    dist = Path(st.frontend_dist)
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def no_ui():
            return JSONResponse(
                {"message": "Web UI is not built. Run `npm install && npm run build` in frontend/ (or use Docker)."}
            )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    st = get_settings()
    uvicorn.run("app.main:app", host=st.host, port=st.port, proxy_headers=True)


if __name__ == "__main__":
    run()
