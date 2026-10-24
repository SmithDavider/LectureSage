"""服务端后台管理 API，供管理员在 Web 界面创建/删除用户并查看课程。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.db import AudioSegment, Course, Job, Screenshot, SessionLocal, TrafficLog, User, ensure_user_database
from app.dependencies import require_auth
from app.services.user_admin import create_user, delete_user


router = APIRouter(prefix="/admin")
_INDEX = Path(__file__).resolve().parent.parent / "admin" / "index.html"


def _error_text(error: Exception) -> str:
    return str(error) or "未知错误"


async def _admin_user(request: Request) -> User:
    user = await require_auth(request)
    if user.username != "admin":
        raise HTTPException(status_code=403, detail="当前账号不是管理员")
    return user


async def _list_user_courses(user: User) -> list[dict]:
    factory = await ensure_user_database(user.id, user.username)
    async with factory() as session:
        rows = (
            await session.execute(select(Course).order_by(Course.created_at.desc()))
        ).scalars().all()
        return [_shared_course(item) for item in rows]


async def _get_user_course_detail(user: User, course_id: str) -> dict:
    factory = await ensure_user_database(user.id, user.username)
    async with factory() as session:
        course = await session.get(Course, course_id)
        if course is None or course.owner_id != user.id:
            raise HTTPException(status_code=404, detail="课程不存在")
        audio = (
            await session.execute(
                select(AudioSegment)
                .where(AudioSegment.course_id == course_id)
                .order_by(AudioSegment.offset_ms)
            )
        ).scalars().all()
        screenshots = (
            await session.execute(
                select(Screenshot)
                .where(Screenshot.course_id == course_id)
                .order_by(Screenshot.offset_ms)
            )
        ).scalars().all()
        jobs = (
            await session.execute(
                select(Job)
                .where(Job.course_id == course_id)
                .order_by(Job.updated_at.desc())
            )
        ).scalars().all()
        return {
            **_shared_course(course),
            "summary": course.summary,
            "transcript": course.transcript,
            "audio_segments": [
                {
                    "id": item.id,
                    "sequence": item.sequence,
                    "offset_ms": item.offset_ms,
                    "filename": item.filename,
                    "size_bytes": item.size_bytes,
                    "draft_text": item.draft_text,
                    "review_state": item.review_state,
                }
                for item in audio
            ],
            "screenshots": [
                {
                    "id": item.id,
                    "offset_ms": item.offset_ms,
                    "filename": item.filename,
                    "page": item.page,
                    "title": item.title,
                    "ocr_status": item.ocr_status,
                }
                for item in screenshots
            ],
            "jobs": [
                {
                    "id": item.id,
                    "task_type": item.task_type,
                    "status": item.status,
                    "message": item.message,
                    "result": item.result,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in jobs
            ],
        }


def _shared_course(item: Course) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "created_at": item.created_at,
        "duration_ms": item.duration_ms,
        "status": item.status,
        "last_activity_at": item.last_activity_at,
        "has_summary": bool(item.summary),
    }


@router.get("/", include_in_schema=False)
async def admin_page():
    return FileResponse(_INDEX)


@router.get("/api/me")
async def admin_me(user: User = Depends(_admin_user)):
    return {"id": user.id, "username": user.username}


@router.get("/api/traffic")
async def admin_traffic(user: User = Depends(_admin_user)):
    async with SessionLocal() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(TrafficLog.bytes), 0))
        )
        total_requests = await session.scalar(
            select(func.count(TrafficLog.id))
        )
        rows = (
            await session.execute(
                select(
                    TrafficLog.user_id,
                    TrafficLog.username,
                    TrafficLog.direction,
                    func.coalesce(func.sum(TrafficLog.bytes), 0),
                    func.count(TrafficLog.id),
                    func.max(TrafficLog.created_at),
                )
                .group_by(TrafficLog.user_id, TrafficLog.username, TrafficLog.direction)
                .order_by(func.max(TrafficLog.created_at).desc())
            )
        ).all()
        recent = (
            await session.execute(
                select(TrafficLog).order_by(TrafficLog.id.desc()).limit(50)
            )
        ).scalars().all()

    by_user: dict[int, dict] = {}
    for user_id, username, direction, traffic_bytes, request_count, last_at in rows:
        key = int(user_id or 0)
        item = by_user.setdefault(
            key,
            {
                "user_id": key,
                "username": username or "",
                "upload_bytes": 0,
                "download_bytes": 0,
                "requests": 0,
                "last_activity_at": last_at or 0,
            },
        )
        item["requests"] += request_count
        item["last_activity_at"] = max(item["last_activity_at"], last_at or 0)
        if direction == "download":
            item["download_bytes"] += traffic_bytes
        else:
            item["upload_bytes"] += traffic_bytes

    return {
        "total_bytes": int(total or 0),
        "total_requests": int(total_requests or 0),
        "users": list(by_user.values()),
        "recent": [
            {
                "id": item.id,
                "user_id": item.user_id,
                "username": item.username,
                "course_id": item.course_id,
                "direction": item.direction,
                "kind": item.kind,
                "bytes": item.bytes,
                "created_at": item.created_at,
            }
            for item in recent
        ],
    }


@router.get("/api/users")
async def admin_list_users(user: User = Depends(_admin_user)):
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(User).order_by(User.created_at.desc()))
        ).scalars().all()
        return [
            {
                "id": item.id,
                "username": item.username,
                "created_at": item.created_at,
            }
            for item in rows
        ]


@router.post("/api/users", status_code=201)
async def admin_create_user(body: dict, user: User = Depends(_admin_user)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    try:
        result = await create_user(username, password)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_text(error)) from error
    return result


@router.delete("/api/users/{username}")
async def admin_delete_user(username: str, user: User = Depends(_admin_user)):
    try:
        return await delete_user(username)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_text(error)) from error


@router.get("/api/users/{user_id}/courses")
async def admin_user_courses(user_id: int, user: User = Depends(_admin_user)):
    async with SessionLocal() as session:
        target = await session.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
    return await _list_user_courses(target)


@router.get("/api/users/{user_id}/courses/{course_id}")
async def admin_user_course_detail(
    user_id: int,
    course_id: str,
    user: User = Depends(_admin_user),
):
    async with SessionLocal() as session:
        target = await session.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
    return await _get_user_course_detail(target, course_id)
