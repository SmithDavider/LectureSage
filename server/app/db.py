import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import time

from app.config import settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (UniqueConstraint('username', name='uq_users_username'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    password_salt: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(utc_now().timestamp() * 1000))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="recording")
    screen_enabled: Mapped[bool] = mapped_column(Integer, default=1)
    microphone_enabled: Mapped[bool] = mapped_column(Integer, default=1)
    last_activity_at: Mapped[int] = mapped_column(Integer, default=lambda: int(utc_now().timestamp() * 1000))
    llm_base_url: Mapped[str] = mapped_column(String(512), default="")
    llm_api_key: Mapped[str] = mapped_column(String(512), default="")
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[str] = mapped_column(Text, default="[]")
    mindmap: Mapped[str] = mapped_column(Text, default="[]")


class AudioSegment(Base):
    __tablename__ = "audio_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    offset_ms: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(64), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer)
    draft_text: Mapped[str] = mapped_column(Text, default="")
    draft_confidence: Mapped[int] = mapped_column(Integer, default=0)
    review_state: Mapped[str] = mapped_column(String(32), default="none")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(utc_now().timestamp() * 1000))


class Screenshot(Base):
    __tablename__ = "screenshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    offset_ms: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    image_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    ocr_json: Mapped[str] = mapped_column(Text, default="[]")
    ocr_status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(utc_now().timestamp() * 1000))


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    offset_ms: Mapped[int] = mapped_column(Integer, index=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(utc_now().timestamp() * 1000))
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(utc_now().timestamp() * 1000))


class TrafficLog(Base):
    __tablename__ = "traffic_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="", index=True)
    course_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    direction: Mapped[str] = mapped_column(String(16), default="upload", index=True)
    kind: Mapped[str] = mapped_column(String(32), default="other", index=True)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000), index=True)


engine = create_async_engine(
    settings.database_url,
    connect_args={"timeout": 30},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

_user_db_locks: dict[int, asyncio.Lock] = {}
_user_engines: dict[int, AsyncEngine] = {}
_user_sessions: dict[int, async_sessionmaker] = {}


def _user_db_path(user_id: int) -> Path:
    return settings.data_dir / "users" / f"u{user_id}.db"


async def ensure_user_database(user_id: int, username: str) -> async_sessionmaker:
    if user_id in _user_sessions:
        return _user_sessions[user_id]
    lock = _user_db_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if user_id in _user_sessions:
            return _user_sessions[user_id]

        path = _user_db_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        user_engine = create_async_engine(
            f"sqlite+aiosqlite:///{path}",
            connect_args={"timeout": 30},
        )
        async with user_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        user_session_maker = async_sessionmaker(user_engine, expire_on_commit=False)

        await ensure_course_feature_columns(user_engine)

        if is_new:
            async with user_session_maker() as user_session:
                user_session.add(
                    User(
                        id=user_id,
                        username=username,
                        password_hash="",
                        password_salt="",
                    )
                )
                await user_session.commit()
            await _migrate_legacy_user_data(user_id, user_session_maker)

        _user_engines[user_id] = user_engine
        _user_sessions[user_id] = user_session_maker
        return user_session_maker


async def ensure_course_feature_columns(user_engine: AsyncEngine) -> None:
    """兼容旧课程表，补齐采集配置字段。"""
    columns = set()
    async with user_engine.connect() as connection:
        result = await connection.exec_driver_sql("PRAGMA table_info(courses)")
        rows = result.fetchall()
        columns = {row[1] for row in rows}

    if "screen_enabled" not in columns:
        async with user_engine.begin() as connection:
            await connection.exec_driver_sql(
                "ALTER TABLE courses ADD COLUMN screen_enabled INTEGER NOT NULL DEFAULT 1"
            )
    if "microphone_enabled" not in columns:
        async with user_engine.begin() as connection:
            await connection.exec_driver_sql(
                "ALTER TABLE courses ADD COLUMN microphone_enabled INTEGER NOT NULL DEFAULT 1"
            )

    async with user_engine.connect() as connection:
        result = await connection.exec_driver_sql("PRAGMA table_info(screenshots)")
        screenshot_columns = {row[1] for row in result.fetchall()}
    if "image_hash" not in screenshot_columns:
        async with user_engine.begin() as connection:
            await connection.exec_driver_sql(
                "ALTER TABLE screenshots ADD COLUMN image_hash VARCHAR(64) NOT NULL DEFAULT ''"
            )


async def delete_user_database(user_id: int) -> None:
    """删除用户时同步清理对应的独立数据库和内存缓存。"""
    lock = _user_db_locks.get(user_id)
    if lock is not None:
        async with lock:
            await _close_user_db(user_id)
    else:
        await _close_user_db(user_id)

    path = _user_db_path(user_id)
    if path.exists():
        path.unlink()
    _user_db_locks.pop(user_id, None)


async def _close_user_db(user_id: int) -> None:
    engine = _user_engines.pop(user_id, None)
    if engine is not None:
        await engine.dispose()
    _user_sessions.pop(user_id, None)


async def _migrate_legacy_user_data(user_id: int, user_session_maker: async_sessionmaker) -> None:
    """从兼容阶段全局库迁移旧数据到该用户的独立数据库。"""
    async with SessionLocal() as source:
        courses = (await source.execute(select(Course).where(Course.owner_id == user_id))).scalars().all()
        course_ids = {item.id for item in courses}
        if not course_ids:
            return
        audio = (
            await source.execute(select(AudioSegment).where(AudioSegment.course_id.in_(course_ids)))
        ).scalars().all()
        screens = (
            await source.execute(select(Screenshot).where(Screenshot.course_id.in_(course_ids)))
        ).scalars().all()
        timeline = (
            await source.execute(select(TimelineEvent).where(TimelineEvent.course_id.in_(course_ids)))
        ).scalars().all()
        jobs = (
            await source.execute(select(Job).where(Job.course_id.in_(course_ids)))
        ).scalars().all()

        courses_data = [
            {
                "id": item.id,
                "owner_id": user_id,
                "title": item.title,
                "created_at": item.created_at,
                "duration_ms": item.duration_ms,
                "status": item.status,
                "last_activity_at": item.last_activity_at,
                "llm_base_url": item.llm_base_url,
                "llm_api_key": item.llm_api_key,
                "llm_model": item.llm_model,
                "summary": item.summary,
                "transcript": item.transcript,
                "sections": item.sections,
                "mindmap": item.mindmap,
            }
            for item in courses
        ]
        audio_data = [
            {
                "course_id": item.course_id,
                "sequence": item.sequence,
                "offset_ms": item.offset_ms,
                "filename": item.filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "draft_text": item.draft_text,
                "draft_confidence": item.draft_confidence,
                "review_state": item.review_state,
                "created_at": item.created_at,
            }
            for item in audio
        ]
        screen_data = [
            {
                "course_id": item.course_id,
                "offset_ms": item.offset_ms,
                "filename": item.filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "page": item.page,
                "title": item.title,
                "content_hash": item.content_hash,
                "ocr_json": item.ocr_json,
                "ocr_status": item.ocr_status,
                "created_at": item.created_at,
            }
            for item in screens
        ]
        timeline_data = [
            {
                "course_id": item.course_id,
                "offset_ms": item.offset_ms,
                "page": item.page,
                "title": item.title,
                "content_hash": item.content_hash,
            }
            for item in timeline
        ]
        job_data = [
            {
                "course_id": item.course_id,
                "task_type": item.task_type,
                "status": item.status,
                "message": item.message,
                "result": item.result,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in jobs
        ]

    async with user_session_maker() as target:
        for row in courses_data:
            target.add(Course(**row))
        for row in audio_data:
            target.add(AudioSegment(**row))
        for row in screen_data:
            target.add(Screenshot(**row))
        for row in timeline_data:
            target.add(TimelineEvent(**row))
        for row in job_data:
            target.add(Job(**row))
        await target.commit()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default

