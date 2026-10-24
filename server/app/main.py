from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.routes import router, websocket_router, auto_finish_stale_recording_courses
from app.db import Base, engine, ensure_course_feature_columns
from app.services.ocr import warmup_ocr
from app.services.sensevoice import sensevoice


async def auto_finish_loop() -> None:
    while True:
        try:
            await auto_finish_stale_recording_courses()
        except Exception:
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_course_feature_columns(engine)
    warmup_task = asyncio.create_task(warmup_ocr())
    auto_finish_task = asyncio.create_task(auto_finish_loop())
    try:
        yield
    finally:
        await sensevoice.close()
        warmup_task.cancel()
        auto_finish_task.cancel()


app = FastAPI(
    title="课迹服务端",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(websocket_router)
app.include_router(admin_router)
