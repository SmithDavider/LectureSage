import asyncio
import hashlib
import json
import logging
import re
import uuid
import traceback
from datetime import datetime, timezone
from pathlib import Path
import httpx
import time

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AudioSegment, Course, Job, Screenshot, SessionLocal, TimelineEvent, ensure_user_database, dumps, loads
from app.db import User
from app.dependencies import get_user_by_credentials, hash_password, require_auth, require_auth_websocket, verify_password
from app.services.llm import chat_json
from app.services.llm import LlmSettings
from app.services.correction import correct_transcript
from app.services.ocr import recognize_image
from app.services.sensevoice import sensevoice
from app.services.generation import _error_text, add_ids, build_chunks, generate_mindmap, map_chunk, normalize_transcript, reduce_chunks
from app.services.traffic import record_traffic
from fastapi.responses import FileResponse


logger = logging.getLogger("keji.api")
logger.setLevel(logging.INFO)
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])
websocket_router = APIRouter(prefix="/api/v1")
connections: dict[str, set[WebSocket]] = {}
_transcription_tasks: set[asyncio.Task] = set()


async def get_session(user: User = Depends(require_auth)) -> AsyncSession:
    factory = await ensure_user_database(user.id, user.username)
    async with factory() as session:
        yield session


async def broadcast(course_id: str, payload: dict) -> None:
    sockets = list(connections.get(course_id, set()))
    if not sockets:
        return
    message = json.dumps(payload, ensure_ascii=False)
    for socket in sockets:
        try:
            await socket.send_text(message)
        except Exception:
            connections.get(course_id, set()).discard(socket)


def _validate_course(course: Course | None) -> Course:
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return course


async def _owned_course(
    session: AsyncSession,
    user: User,
    course_id: str,
) -> Course:
    course = await session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    if course.owner_id != user.id:
        raise HTTPException(status_code=403, detail="no access to course")
    return course


def _normalize_sections(value) -> list[dict]:
    normalized = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        section = dict(item)
        summary = section.get("summary")
        if isinstance(summary, list):
            summary = "\n".join(
                str(part).strip() for part in summary if str(part).strip()
            )
        section["summary"] = str(summary or "")
        normalized.append(section)
    return normalized


def _ensure_result_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        merged: dict = {}
        for item in value:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    return {}


def _summary_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "")


def _llm_error(error: Exception) -> Exception:
    response = getattr(error, "response", None)
    if response is None:
        return error
    body = ""
    try:
        body = response.text[:500]
    except Exception:
        pass
    if body:
        return RuntimeError(f"{error}\nLLM response: {body}")
    return error


def _build_canvas(mindmap: list) -> dict:
    nodes = []
    edges = []
    cursor_y = 0

    id_cache: dict[int, str] = {}

    def node_id(value: dict) -> str:
        key = id(value)
        if key not in id_cache:
            id_cache[key] = str(value.get("node_id") or uuid.uuid4().hex)
        return id_cache[key]

    def place(values: list, depth: int) -> list:
        nonlocal cursor_y
        placed = []
        for value in values:
            if not isinstance(value, dict):
                continue
            text = str(value.get("text") or "")
            children = [item for item in value.get("children") or [] if isinstance(item, dict)]
            width = min(260, max(120, 28 + len(text) * 7))
            height = 44
            if children:
                child_placed = []
                for child in children:
                    child_placed.extend(place([child], depth + 1))
                centers = [item["y"] + item["height"] / 2 for item in child_placed]
                center_y = (min(centers) + max(centers)) / 2
            else:
                center_y = cursor_y + height / 2
                cursor_y += 56
                child_placed = []
            current = {
                "id": node_id(value),
                "type": "text",
                "text": text,
                "x": 40 + depth * 280,
                "y": round(center_y - height / 2, 1),
                "width": width,
                "height": height,
                "color": str(1 + depth % 6),
            }
            nodes.append(current)
            for child in children:
                parent_id = current["id"]
                child_id = node_id(child)
                edges.append({
                    "id": f"{parent_id}-{child_id}",
                    "fromNode": parent_id,
                    "fromSide": "right",
                    "toNode": child_id,
                    "toSide": "left",
                    "toEnd": "arrow",
                })
            placed.append(current)
            placed.extend(child_placed)
        return placed

    place(mindmap or [], 0)
    return {"nodes": nodes, "edges": edges}


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _append_opml_outline(lines: list[str], value: dict, depth: int) -> None:
    indent = "    " * (depth + 1)
    lines.append(f'{indent}<outline text="{_xml_escape(value.get("text") or "")}" />')
    for child in value.get("children") or []:
        if isinstance(child, dict):
            _append_opml_outline(lines, child, depth + 1)


async def _save_upload(course: Course, kind: str, offset_ms: int, file: UploadFile, session: AsyncSession) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if kind == "audio":
        if suffix not in {".wav", ".opus", ".ogg", ".m4a", ".aac", ".mp4", ".mp3"}:
            suffix = ".bin"
        count = await session.scalar(
            select(func.count(AudioSegment.id)).where(AudioSegment.course_id == course.id)
        )
        filename = f"audio_{count + 1:05d}_{offset_ms:010d}{suffix}"
    else:
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        count = await session.scalar(
            select(func.count(Screenshot.id)).where(Screenshot.course_id == course.id)
        )
        filename = f"screen_{count + 1:05d}_{offset_ms:010d}{suffix}"

    directory = settings.data_dir / kind / course.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    size = 0
    with path.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            output.write(chunk)
    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="empty upload")
    return path, size


def _ocr_body(ocr_json: str, limit_chars: int = 2000) -> str:
    data = loads(ocr_json, {})
    return str(data.get("body") or "").strip()[:limit_chars]


def _ocr_preview(ocr_json: str, max_lines: int = 8) -> str:
    data = loads(ocr_json, {})
    lines = data.get("lines") or []
    preview = " / ".join(str(line).strip() for line in lines[:max_lines] if str(line).strip())
    return preview[:500]


async def _latest_screenshot_context(session: AsyncSession, course_id: str, offset_ms: int) -> dict | None:
    result = await session.execute(
        select(Screenshot)
        .where(
            Screenshot.course_id == course_id,
            Screenshot.offset_ms <= offset_ms,
            Screenshot.ocr_status == "ok",
            Screenshot.ocr_json.isnot(None),
        )
        .order_by(Screenshot.offset_ms.desc(), Screenshot.id.desc())
        .limit(1)
    )
    item = result.scalars().first()
    if item is None:
        return None
    return {
        "page": item.page,
        "title": item.title,
        "text": _ocr_body(item.ocr_json),
        "preview": _ocr_preview(item.ocr_json),
        "ocr_json": item.ocr_json,
    }


def _correction_llm_settings() -> LlmSettings | None:
    if not settings.correction_llm_enabled:
        return None
    if not settings.correction_llm_base_url or not settings.correction_llm_model:
        return None
    return LlmSettings(
        base_url=settings.correction_llm_base_url,
        api_key=settings.correction_llm_api_key,
        model=settings.correction_llm_model,
        timeout_seconds=settings.correction_llm_timeout_seconds,
        temperature=0.0,
        max_tokens=512,
    )


async def _successful_screenshots(session: AsyncSession, course_id: str) -> list[Screenshot]:
    return list(
        (
            await session.execute(
                select(Screenshot)
                .where(
                    Screenshot.course_id == course_id,
                    Screenshot.ocr_status == "ok",
                )
                .order_by(Screenshot.offset_ms, Screenshot.id)
            )
        ).scalars().all()
    )


async def _confirm_transcript_with_ocr(
    session: AsyncSession,
    course_id: str,
    screenshot_offset_ms: int,
) -> list[dict]:
    """使用刚完成的 OCR 证据校正并确认该页面时间区间内的初稿。"""
    course = await session.get(Course, course_id)
    llm_settings = _correction_llm_settings()
    if course is None or not course.screen_enabled or llm_settings is None:
        return []
    transcript = loads(course.transcript, [])
    if isinstance(transcript, str):
        transcript = loads(transcript, [])

    next_screenshot_offset = await session.scalar(
        select(func.min(Screenshot.offset_ms)).where(
            Screenshot.course_id == course_id,
            Screenshot.offset_ms > screenshot_offset_ms,
            Screenshot.ocr_status == "ok",
        )
    )
    candidate_indexes = []
    candidates = []
    for index, line in enumerate(transcript):
        if not isinstance(line, dict):
            continue
        if line.get("confirmed"):
            continue
        start_ms = int(line.get("start_ms") or line.get("offset_ms") or 0)
        if start_ms < screenshot_offset_ms:
            continue
        if next_screenshot_offset is not None and start_ms >= next_screenshot_offset:
            continue
        candidate_indexes.append(index)
        candidates.append(dict(line))
    if not candidates:
        return []

    screenshots = await _successful_screenshots(session, course_id)
    corrected = await correct_transcript(candidates, screenshots, llm_settings)
    confirmed = []
    for transcript_index, item in zip(candidate_indexes, corrected):
        if not item.get("confirmed"):
            continue
        transcript[transcript_index] = item
        confirmed.append(item)
    if not confirmed:
        return []
    course.transcript = dumps(transcript)
    await session.commit()
    return confirmed


@router.get("/health")
async def health():
    return {"status": "ok", "service": "keji-backend"}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.get("/updates/check")
async def check_update():
    apk_path = settings.data_dir / "updates" / settings.update_apk_name
    if not settings.update_apk_version or not apk_path.exists():
        return {
            "has_update": False,
            "version_code": None,
            "version_name": "",
            "download_url": "",
            "notes": "",
            "size_bytes": 0,
            "sha256": "",
        }
    apk_size = apk_path.stat().st_size
    return {
        "has_update": True,
        "version_code": int(settings.update_apk_version.split("_", 1)[0] or 0),
        "version_name": settings.update_apk_version.split("_", 1)[1] if "_" in settings.update_apk_version else settings.update_apk_version,
        "download_url": "/api/v1/updates/download",
        "notes": settings.update_apk_notes,
        "size_bytes": apk_size,
        "sha256": _file_sha256(apk_path),
    }


@router.get("/updates/download")
async def download_update_apk(user: User = Depends(require_auth)):
    apk_path = settings.data_dir / "updates" / settings.update_apk_name
    if not apk_path.exists():
        raise HTTPException(status_code=404, detail="update apk not found")
    apk_size = apk_path.stat().st_size
    apk_sha256 = _file_sha256(apk_path)

    async def stream_apk():
        start = time.perf_counter()
        sent = 0
        try:
            with open(apk_path, "rb") as apk:
                while True:
                    chunk = apk.read(1024 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    yield chunk
            elapsed = time.perf_counter() - start
            speed = sent / elapsed if elapsed > 0 else 0
            logger.warning(
                "download apk finished path=%s bytes=%s elapsed=%.3fs speed=%.0f B/s",
                apk_path,
                sent,
                elapsed,
                speed,
            )
            try:
                await record_traffic(
                    user_id=user.id,
                    username=user.username,
                    direction="download",
                    kind="apk",
                    size_bytes=sent,
                )
            except Exception as log_error:
                logger.warning("traffic log apk failed user=%s error=%s", user.id, log_error)
        except Exception as error:
            elapsed = time.perf_counter() - start
            logger.error("download apk failed path=%s bytes=%s elapsed=%.3fs error=%s", apk_path, sent, elapsed, error)
            raise

    return StreamingResponse(
        stream_apk(),
        media_type="application/vnd.android.package-archive",
        headers={
            "Content-Disposition": 'attachment; filename="keji-debug.apk"',
            "Content-Length": str(apk_size),
            "X-APK-SHA256": apk_sha256,
            "Cache-Control": "no-store",
        },
    )


@router.post("/me/password")
async def change_password(
    body: dict,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    old_password = str(body.get("old_password") or "")
    new_password = str(body.get("new_password") or "")
    if not old_password:
        raise HTTPException(status_code=400, detail="invalid old_password")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="new password too short")
    if old_password == new_password:
        raise HTTPException(status_code=400, detail="new password cannot be the same as old password")
    if not verify_password(old_password, user.password_salt, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    updated = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one()
    salt_b64, password_hash = hash_password(new_password)
    updated.password_salt = salt_b64
    updated.password_hash = password_hash
    await session.commit()
    return {"status": "changed"}


@router.post("/courses")
async def create_course(
    body: dict,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course_id = str(body.get("course_id") or uuid.uuid4())
    course = Course(
        id=course_id,
        owner_id=user.id,
        title=str(body.get("title") or "未命名课程"),
        status="recording",
        screen_enabled=1 if body.get("screen_enabled", True) else 0,
        microphone_enabled=1 if body.get("microphone_enabled", True) else 0,
        last_activity_at=int(datetime.now(timezone.utc).timestamp() * 1000),
        llm_base_url=str(body.get("llm_base_url") or ""),
        llm_api_key=str(body.get("llm_api_key") or ""),
        llm_model=str(body.get("llm_model") or ""),
    )
    session.add(course)
    await session.commit()
    return {"course_id": course.id, "status": course.status}


@router.get("/courses")
async def list_courses(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Course)
        .where(Course.owner_id == user.id)
        .order_by(Course.created_at.desc())
    )
    courses = result.scalars().all()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_courses = []
    for course in courses:
        if course.status != "recording":
            continue
        last_audio = await session.scalar(
            select(func.max(AudioSegment.created_at))
            .where(AudioSegment.course_id == course.id)
        )
        last_screen = await session.scalar(
            select(func.max(Screenshot.created_at))
            .where(Screenshot.course_id == course.id)
        )
        last_activity = max(course.created_at, last_audio or 0, last_screen or 0)
        if now_ms - last_activity > 180_000:
            await submit_course_finish(session=session, course=course)
            stale_courses.append(course)
    if stale_courses:
        await session.commit()
    return [
        {
            "course_id": course.id,
            "title": course.title,
            "created_at": course.created_at,
            "duration_ms": course.duration_ms,
            "status": course.status,
            "screen_enabled": bool(course.screen_enabled),
            "microphone_enabled": bool(course.microphone_enabled),
        }
        for course in courses
    ]


@router.get("/courses/{course_id}")
async def get_course(
    course_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    screenshots = (
        await session.execute(
            select(Screenshot)
            .where(Screenshot.course_id == course_id)
            .order_by(Screenshot.offset_ms)
        )
    ).scalars().all()
    sections = _normalize_sections(loads(course.sections, []))
    return {
        "course_id": course.id,
        "title": course.title,
        "status": course.status,
        "created_at": course.created_at,
        "duration_ms": course.duration_ms,
        "screen_enabled": bool(course.screen_enabled),
        "microphone_enabled": bool(course.microphone_enabled),
        "summary": course.summary,
        "transcript": course.transcript,
        "timeline": [
            {"timestamp_ms": item.offset_ms, "page": item.page, "title": item.title, "hash": item.content_hash}
            for item in screenshots
            if item.title or item.page is not None or item.content_hash
        ],
        "sections": sections,
        "mindmap": loads(course.mindmap, []),
        "canvas": _build_canvas(loads(course.mindmap, [])),
    }


@router.post("/courses/{course_id}/audio")
async def upload_audio(
    course_id: str,
    background: BackgroundTasks,
    user: User = Depends(require_auth),
    offset_ms: int = Form(0),
    draft_json: str = Form(""),
    realtime: int = Form(0),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    path, size = await _save_upload(course, "audio", offset_ms, file, session)
    item = AudioSegment(
        course_id=course.id,
        sequence=0,
        offset_ms=offset_ms,
        filename=path.name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
    )
    item.sequence = int(re.search(r"audio_(\d+)_", path.name).group(1))
    draft = loads(draft_json, [])
    confidences = [
        float(value.get("confidence", 0))
        for value in draft
        if isinstance(value, dict) and value.get("confidence") is not None
    ]
    item.draft_text = dumps(draft)
    item.draft_confidence = int(sum(confidences) / len(confidences) * 1000) if confidences else 0
    item.review_state = "pending" if draft else "none"
    session.add(item)
    course.last_activity_at = int(datetime.now(timezone.utc).timestamp() * 1000)
    await session.commit()
    await record_traffic(
        user_id=user.id,
        username=user.username,
        course_id=course.id,
        direction="upload",
        kind="audio",
        size_bytes=size,
    )
    if realtime:
        background.add_task(run_sensevoice_subtitle, item.id, user.id)
    return {"audio_id": item.id, "filename": path.name, "offset_ms": offset_ms}


async def run_sensevoice_subtitle(segment_id: int, user_id: int) -> None:
    factory = await ensure_user_database(user_id, "")
    async with factory() as session:
        item = await session.get(AudioSegment, segment_id)
        if item is None:
            return
        path = settings.data_dir / "audio" / item.course_id / item.filename
        if not path.exists() or path.suffix.lower() == ".bin" or path.stat().st_size < 500:
            return
        try:
            line = await sensevoice.transcribe_segment(path, item.offset_ms, segment_id)
        except Exception as error:
            logger.warning("SenseVoice 确认字幕失败 segment=%s: %s", segment_id, error)
            return
        if not line:
            return

        course = await session.get(Course, item.course_id)
        if course is not None:
            if course.screen_enabled:
                context = await _latest_screenshot_context(session, item.course_id, item.offset_ms)
                llm_settings = _correction_llm_settings()
                if context and llm_settings is not None:
                    screenshots = await _successful_screenshots(session, item.course_id)
                    corrected = await correct_transcript([line], screenshots, llm_settings)
                    line = corrected[0] if corrected else line
                if not line.get("confirmed"):
                    line["confirmed"] = False
                    line["review_state"] = "draft"
                    original_text = line.get("original_text") or line.get("text")
                    line["original_text"] = original_text
            else:
                line["confirmed"] = False
                line["review_state"] = "draft"
                original_text = line.get("original_text") or line.get("text")
                line["original_text"] = original_text

            transcript = loads(course.transcript, [])
            if isinstance(transcript, str):
                transcript = loads(transcript, [])
            line_id = str(line.get("id") or "")
            if line_id:
                transcript = [entry for entry in transcript if entry.get("id") != line_id]
            transcript.append(line)
            transcript.sort(key=lambda entry: (int(entry.get("start_ms") or 0), int(entry.get("end_ms") or 0)))
            course.transcript = dumps(transcript)
            await session.commit()
        await broadcast(item.course_id, {"type": "subtitle", "subtitle": line})


@router.post("/courses/{course_id}/screens")
async def upload_screen(
    course_id: str,
    background: BackgroundTasks,
    user: User = Depends(require_auth),
    offset_ms: int = Form(0),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    path, size = await _save_upload(course, "screens", offset_ms, file, session)
    screenshot = Screenshot(
        course_id=course.id,
        offset_ms=offset_ms,
        filename=path.name,
        content_type=file.content_type or "image/jpeg",
        size_bytes=size,
        image_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    session.add(screenshot)
    course.last_activity_at = int(datetime.now(timezone.utc).timestamp() * 1000)
    await session.commit()
    await record_traffic(
        user_id=user.id,
        username=user.username,
        course_id=course.id,
        direction="upload",
        kind="screenshot",
        size_bytes=size,
    )
    background.add_task(process_screenshot, screenshot.id, user.id)
    return {"screen_id": screenshot.id, "filename": path.name, "offset_ms": offset_ms}


async def process_screenshot(screen_id: int, user_id: int):
    factory = await ensure_user_database(user_id, "")
    async with factory() as session:
        screenshot = await session.get(Screenshot, screen_id)
        if screenshot is None:
            return
        try:
            path = settings.data_dir / "screens" / screenshot.course_id / screenshot.filename
            previous = (
                await session.execute(
                    select(Screenshot)
                    .where(
                        Screenshot.course_id == screenshot.course_id,
                        Screenshot.image_hash == screenshot.image_hash,
                        Screenshot.ocr_status == "ok",
                        Screenshot.id != screenshot.id,
                    )
                    .order_by(Screenshot.id.desc())
                    .limit(1)
                )
            ).scalars().first()
            if previous is not None:
                screenshot.page = previous.page
                screenshot.title = previous.title
                screenshot.content_hash = previous.content_hash
                screenshot.ocr_json = previous.ocr_json
                screenshot.ocr_status = "ok"
                logger.info("复用相同截图 OCR screen=%s previous=%s", screenshot.id, previous.id)
            else:
                result = await recognize_image(path)
                screenshot.page = result["page"]
                screenshot.title = result["title"]
                screenshot.content_hash = result["content_hash"]
                screenshot.ocr_json = dumps(result)
                screenshot.ocr_status = "ok"

            existing = await session.execute(
                select(TimelineEvent).where(
                    TimelineEvent.course_id == screenshot.course_id,
                    TimelineEvent.offset_ms == screenshot.offset_ms,
                )
            )
            event = existing.scalar_one_or_none()
            if event is None:
                event = TimelineEvent(
                    course_id=screenshot.course_id,
                    offset_ms=screenshot.offset_ms,
                    page=screenshot.page,
                    title=screenshot.title,
                    content_hash=screenshot.content_hash,
                )
                session.add(event)
            else:
                event.page = screenshot.page
                event.title = screenshot.title
                event.content_hash = screenshot.content_hash
            await session.commit()

            confirmed_lines = await _confirm_transcript_with_ocr(
                session,
                screenshot.course_id,
                screenshot.offset_ms,
            )
            for line in confirmed_lines:
                await broadcast(screenshot.course_id, {"type": "subtitle", "subtitle": line})
            await broadcast(
                screenshot.course_id,
                {
                    "type": "timeline",
                    "timeline": {
                        "timestamp_ms": screenshot.offset_ms,
                        "page": screenshot.page,
                        "title": screenshot.title,
                        "hash": screenshot.content_hash,
                    },
                },
            )
        except Exception as error:
            screenshot.ocr_status = "failed"
            screenshot.ocr_json = dumps({"error": str(error)})
            await session.commit()


@router.post("/courses/{course_id}/finish")
async def finish_course(
    course_id: str,
    background: BackgroundTasks,
    body: dict,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    job = await submit_course_finish(
        session=session,
        course=course,
        duration_ms=body.get("duration_ms"),
        background=background,
    )
    return {"task_id": job.id, "status": job.status}


async def submit_course_finish(
    session: AsyncSession,
    course: Course,
    duration_ms: int | None = None,
    background: BackgroundTasks | None = None,
) -> Job:
    """把 recording 课程提交进入课后总结任务，供 API 和后台超时扫描共用。"""
    if course.status != "recording":
        existing = await session.scalar(
            select(Job)
            .where(Job.course_id == course.id, Job.task_type == "generate")
            .order_by(Job.id.desc())
            .limit(1)
        )
        if existing is None:
            raise ValueError("course is not recording and has no prior generate job")
        return existing
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if duration_ms is not None:
        course.duration_ms = max(0, int(duration_ms))
    elif course.duration_ms <= 0:
        course.duration_ms = max(0, now_ms - course.created_at)
    course.status = "queued"
    job = Job(course_id=course.id, task_type="generate", status="queued")
    session.add(job)
    await session.commit()
    if background is not None:
        background.add_task(run_generation, course.id, job.id, course.owner_id)
    else:
        asyncio.create_task(run_generation(course.id, job.id, course.owner_id))
    return job


async def _course_last_activity(session: AsyncSession, course: Course) -> int:
    last_audio = await session.scalar(
        select(func.max(AudioSegment.created_at))
        .where(AudioSegment.course_id == course.id)
    )
    last_screen = await session.scalar(
        select(func.max(Screenshot.created_at))
        .where(Screenshot.course_id == course.id)
    )
    return max(course.created_at, course.last_activity_at or 0, last_audio or 0, last_screen or 0)


async def auto_finish_stale_recording_courses() -> None:
    """扫描所有用户独立库，自动把长时间没有活动的 recording 课程提交课后总结。"""
    async with SessionLocal() as auth_session:
        users = (await auth_session.execute(select(User).order_by(User.id))).scalars().all()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for user in users:
        try:
            factory = await ensure_user_database(user.id, user.username)
        except Exception as error:
            logger.warning("自动结束失败：用户 db 打开异常 user=%s: %s", user.id, error)
            continue
        async with factory() as session:
            courses = (
                await session.execute(
                    select(Course).where(Course.status == "recording")
                )
            ).scalars().all()
            for course in courses:
                try:
                    last_activity = await _course_last_activity(session, course)
                    if now_ms - last_activity <= 180_000:
                        continue
                    await submit_course_finish(session=session, course=course)
                    logger.info("自动结束超时课程 course=%s activity_past_ms=%s", course.id, now_ms - last_activity)
                except Exception as error:
                    logger.warning("自动结束失败 course=%s: %s", course.id, error)


async def run_retranscription(course_id: str, job_id: int, user_id: int):
    factory = await ensure_user_database(user_id, "")
    async with factory() as session:
        course = await session.get(Course, course_id)
        job = await session.get(Job, job_id)
        if course is None or job is None:
            return
        course.status = "transcribing"
        job.status = "running"
        job.message = "正在启动 SenseVoice 课后转写"
        await session.commit()
        await broadcast(course_id, {"type": "status", "status": course.status, "message": job.message})

        try:
            audio_items = (
                await session.execute(
                    select(AudioSegment)
                    .where(AudioSegment.course_id == course_id)
                    .order_by(AudioSegment.sequence)
                )
            ).scalars().all()
            segments_input: list[tuple[int, Path]] = []
            for item in audio_items:
                path = settings.data_dir / "audio" / course_id / item.filename
                if not path.exists() or path.suffix.lower() == ".bin" or item.size_bytes < 500:
                    continue
                segments_input.append((item.offset_ms, path))
            if not segments_input:
                screenshots = (
                    await session.execute(
                        select(Screenshot)
                        .where(Screenshot.course_id == course_id)
                        .order_by(Screenshot.offset_ms)
                    )
                ).scalars().all()
                image_lines = [
                    {
                        "id": f"screen-{item.id}",
                        "start_ms": item.offset_ms,
                        "end_ms": item.offset_ms + 1000,
                        "text": _ocr_body(item.ocr_json),
                        "page": item.page,
                        "page_title": item.title or "",
                        "confirmed": True,
                        "review_state": "confirmed_by_ocr",
                    }
                    for item in screenshots
                    if item.ocr_status == "ok" and _ocr_body(item.ocr_json)
                ]
                if image_lines:
                    course.transcript = dumps(image_lines)
                    course.status = "transcribed"
                    job.status = "success"
                    job.message = "未检测到音频，已根据截图 OCR 生成图像转写"
                    await session.commit()
                    await broadcast(course_id, {"type": "status", "status": course.status, "message": job.message})
                    return
                raise ValueError("没有可转写的音频分段，也没有可用的截图 OCR 内容")

            course.transcript = ""
            course.status = "transcribing"
            job.message = f"正在合并 {len(segments_input)} 段音频"
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": "transcribing", "message": job.message})

            transcript_parts = await sensevoice.transcribe_merged(segments_input)
            course.transcript = dumps(transcript_parts)
            job.message = f"已完成 {len(transcript_parts)} 句"

            course.status = "transcribed"
            job.status = "success"
            job.message = "SenseVoice 转写完成"
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": course.status, "message": job.message})
        except Exception as error:
            course.status = "transcription_failed"
            job.status = "failed"
            job.message = f"{error}\n\n{traceback.format_exc()}"
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": course.status, "message": str(error)})


@router.post("/courses/{course_id}/generate")
async def generate_course(
    course_id: str,
    background: BackgroundTasks,
    body: dict | None = None,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    job = Job(course_id=course.id, task_type="generate", status="queued")
    session.add(job)
    await session.commit()
    background.add_task(run_generation, course.id, job.id, user.id)
    return {"task_id": job.id, "status": job.status}


@router.post("/courses/{course_id}/retranscribe")
async def retranscribe_course(
    course_id: str,
    background: BackgroundTasks,
    body: dict | None = None,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    job = Job(course_id=course.id, task_type="retranscribe", status="queued")
    session.add(job)
    await session.commit()
    background.add_task(run_retranscription, course.id, job.id, user.id)
    return {"task_id": job.id, "status": job.status}


@router.post("/courses/{course_id}/interrupt")
async def interrupt_course(
    course_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    course.status = "interrupted"
    await session.commit()
    await broadcast(course_id, {"type": "status", "status": "interrupted"})
    return {"course_id": course.id, "status": course.status}


async def _save_generation_state(job, session, course_id: str, stage: str, message: str, **extra) -> None:
    state = {"stage": stage, **extra}
    job.result = dumps(state)
    job.message = message
    job.updated_at = int(datetime.now(timezone.utc).timestamp() * 1000)
    await session.commit()
    await broadcast(course_id, {"type": "status", "status": "generating", "message": message})


def _generation_llm_settings(course: Course) -> LlmSettings:
    return LlmSettings(
        base_url=course.llm_base_url or settings.llm_base_url,
        api_key=course.llm_api_key or settings.llm_api_key,
        model=course.llm_model or settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=0.0,
        max_tokens=1800,
    )


def _section_key_points(summary: str) -> list[str]:
    return [line[2:].strip() for line in str(summary or "").splitlines() if line.strip().startswith("-")][:5]


async def run_generation(course_id: str, job_id: int, user_id: int):
    factory = await ensure_user_database(user_id, "")
    async with factory() as session:
        course = await session.get(Course, course_id)
        job = await session.get(Job, job_id)
        if course is None or job is None:
            return
        course.status = "generating"
        job.status = "running"
        await session.commit()
        await broadcast(course_id, {"type": "status", "status": "generating", "message": "正在准备总结"})

        try:
            screenshots_rows = (
                await session.execute(
                    select(Screenshot)
                    .where(Screenshot.course_id == course_id, Screenshot.ocr_status == "ok")
                    .order_by(Screenshot.offset_ms)
                )
            ).scalars().all()
            screenshots = [
                {
                    "timestamp_ms": item.offset_ms,
                    "page": item.page,
                    "title": item.title,
                    "text": _ocr_body(item.ocr_json, 1200),
                }
                for item in screenshots_rows
            ]
            transcript = normalize_transcript(course.transcript)
            if not transcript and screenshots:
                logger.warning("transcript empty, fallback to OCR screenshots course=%s count=%s", course_id, len(screenshots))
                transcript = [
                    {
                        "start_ms": int(item.get("timestamp_ms") or 0),
                        "end_ms": int(item.get("timestamp_ms") or 0) + 1000,
                        "text": item.get("text") or "",
                        "page": item.get("page"),
                        "page_title": item.get("title") or "",
                        "confirmed": True,
                    }
                    for item in screenshots
                    if (item.get("text") or "").strip()
                ]
            if not transcript:
                raise ValueError("没有可用于生成总结的转写或截图 OCR 内容")
            chunks = build_chunks(transcript)
            state = loads(job.result, {})
            mapped = state.get("mapped") if isinstance(state, dict) else []
            mapped = mapped if isinstance(mapped, list) else []
            mapped_ids = {item.get("chunk_id") for item in mapped if isinstance(item, dict)}
            await _save_generation_state(
                job, session, course_id, "mapping",
                f"正在生成分段摘要：{len(mapped_ids)}/{len(chunks)}",
                total_chunks=len(chunks), completed_chunks=len(mapped_ids), mapped=mapped,
            )
            llm_settings = _generation_llm_settings(course)
            failed_chunks = []
            pending_chunks = [chunk for chunk in chunks if chunk["chunk_id"] not in mapped_ids]
            map_semaphore = asyncio.Semaphore(2)

            async def map_one(chunk):
                async with map_semaphore:
                    try:
                        return chunk, await map_chunk(chunk, screenshots, llm_settings), None
                    except Exception as error:
                        return chunk, None, error

            for chunk, item, error in await asyncio.gather(*(map_one(chunk) for chunk in pending_chunks)):
                if error is not None:
                    failed_chunks.append({"chunk_id": chunk["chunk_id"], "error": _error_text(error)[:500]})
                    logger.warning("Map chunk failed course=%s chunk=%s: %s", course_id, chunk["chunk_id"], error)
                    continue
                mapped.append(item)
                mapped_ids.add(chunk["chunk_id"])
                await _save_generation_state(
                    job, session, course_id, "mapping",
                    f"正在生成分段摘要：{len(mapped_ids)}/{len(chunks)}",
                    total_chunks=len(chunks), completed_chunks=len(mapped_ids), mapped=mapped,
                )
            retry_rounds = 0
            while failed_chunks and retry_rounds < 2:
                retry_rounds += 1
                if retry_rounds > 1:
                    failed_chunks = [
                        item for item in failed_chunks
                        if "内容为空" in item.get("error", "")
                        or "JSON 修复失败" in item.get("error", "")
                        or "缺少 summary" in item.get("error", "")
                    ]
                if not failed_chunks:
                    break
                retry_failed_ids = {item["chunk_id"] for item in failed_chunks}
                retry_chunks = [chunk for chunk in chunks if chunk["chunk_id"] in retry_failed_ids]
                retry_semaphore = asyncio.Semaphore(1)

                async def map_retry(chunk):
                    async with retry_semaphore:
                        try:
                            return chunk, await map_chunk(chunk, screenshots, llm_settings), None
                        except Exception as error:
                            return chunk, None, error

                retry_failed = []
                for chunk, item, error in await asyncio.gather(*(map_retry(chunk) for chunk in retry_chunks)):
                    if error is not None:
                        retry_failed.append({"chunk_id": chunk["chunk_id"], "error": _error_text(error)[:500]})
                        logger.warning("Map retry round=%s chunk failed course=%s chunk=%s: %s", retry_rounds, course_id, chunk["chunk_id"], error)
                        continue
                    mapped.append(item)
                    mapped_ids.add(chunk["chunk_id"])
                failed_chunks = retry_failed
            if failed_chunks:
                await _save_generation_state(
                    job, session, course_id, "mapping",
                    f"正在生成分段摘要：{len(mapped_ids)}/{len(chunks)}（已多次兜底重试）",
                    total_chunks=len(chunks), completed_chunks=len(mapped_ids), failed_chunks=failed_chunks, mapped=mapped,
                )
            if not mapped:
                raise ValueError("所有分段摘要均生成失败")

            mapped.sort(key=lambda item: int(item.get("start_ms") or 0))
            await _save_generation_state(
                job, session, course_id, "reducing", "正在汇总全文和分节小结",
                total_chunks=len(chunks), completed_chunks=len(mapped), failed_chunks=failed_chunks, mapped=mapped,
            )
            reduced = await reduce_chunks(course.title, course.duration_ms, mapped, llm_settings)
            course.summary = reduced["summary"]
            course.sections = dumps(_normalize_sections(reduced["sections"]))
            await session.commit()
            reduce_sections = [
                {**section, "key_points": _section_key_points(section.get("summary", ""))}
                for section in reduced["sections"]
            ]

            await _save_generation_state(
                job, session, course_id, "mindmap", "正在生成思维导图",
                total_chunks=len(chunks), completed_chunks=len(mapped), failed_chunks=failed_chunks, mapped=mapped,
                reduced={**reduced, "sections": reduce_sections},
            )
            mindmap_error = ""
            try:
                raw_mindmap = await generate_mindmap(course.title, course.summary, reduce_sections, llm_settings)
                course.mindmap = dumps(add_ids(raw_mindmap, lambda: uuid.uuid4().hex))
            except Exception as error:
                mindmap_error = str(error)[:500]
                course.mindmap = dumps([])
                logger.warning("Mindmap generation failed course=%s: %s", course_id, error)
            partial = bool(failed_chunks or mindmap_error)
            course.status = "partial_success" if partial else "completed"
            job.status = "success"
            job.message = "总结生成完成，但部分阶段未完成" if partial else "生成完成"
            job.result = dumps({
                "stage": "partial_success" if partial else "success",
                "total_chunks": len(chunks),
                "completed_chunks": len(mapped),
                "failed_chunks": failed_chunks,
                "mindmap_error": mindmap_error,
            })
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": course.status, "message": job.message})
        except Exception as error:
            course.status = "generation_failed"
            job.status = "failed"
            job.message = str(error)
            previous = loads(job.result, {})
            if not isinstance(previous, dict):
                previous = {}
            job.result = dumps({
                "stage": "failed",
                "error": str(error)[:1000],
                "mapped": previous.get("mapped", []),
                "failed_chunks": previous.get("failed_chunks", []),
                "total_chunks": previous.get("total_chunks", 0),
                "completed_chunks": previous.get("completed_chunks", 0),
            })
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": course.status, "message": str(error)})


async def run_generation_legacy(course_id: str, job_id: int, user_id: int):
    factory = await ensure_user_database(user_id, "")
    async with factory() as session:
        course = await session.get(Course, course_id)
        job = await session.get(Job, job_id)
        if course is None or job is None:
            return
        course.status = "generating"
        job.status = "running"
        await session.commit()
        await broadcast(course_id, {"type": "status", "status": "generating"})

        try:
            audio_items = (
                await session.execute(
                    select(AudioSegment)
                    .where(AudioSegment.course_id == course_id)
                    .order_by(AudioSegment.sequence)
                )
            ).scalars().all()
            screenshots = (
                await session.execute(
                    select(Screenshot)
                    .where(Screenshot.course_id == course_id)
                    .order_by(Screenshot.offset_ms)
                )
            ).scalars().all()

            # 未开启麦克风时仍允许只录截图生成转写：把 OCR 内容按截图时间整理成文本，
            # 避免服务端因没有音频分段而失败或一直等待语音转写。
            if not audio_items and screenshots:
                existing_transcript = loads(course.transcript, [])
                if isinstance(existing_transcript, str):
                    existing_transcript = loads(existing_transcript, [])
                if not existing_transcript:
                    image_lines = []
                    for item in screenshots:
                        if item.ocr_status != "ok":
                            continue
                        ocr_text = _ocr_body(item.ocr_json)
                        if not ocr_text:
                            continue
                        image_lines.append(
                            {
                                "id": f"screen-{item.id}",
                                "start_ms": item.offset_ms,
                                "end_ms": item.offset_ms + 1000,
                                "text": ocr_text,
                                "page": item.page,
                                "page_title": item.title or "",
                                "confirmed": True,
                                "review_state": "confirmed_by_ocr",
                            }
                        )
                    if image_lines:
                        course.transcript = dumps(image_lines)
                        await session.commit()

            if course.llm_base_url:
                llm_settings = LlmSettings(
                    base_url=course.llm_base_url,
                    api_key=course.llm_api_key,
                    model=course.llm_model,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
            else:
                llm_settings = LlmSettings(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
            transcript_text = str(course.transcript or "")
            if len(transcript_text) > 180_000:
                transcript_text = transcript_text[:180_000] + "\n[转写内容过长已截断]"
            phone_drafts = [
                {
                    "offset_ms": item.offset_ms,
                    "draft": loads(item.draft_text, []),
                    "confidence": item.draft_confidence / 1000.0,
                    "review_state": item.review_state,
                }
                for item in audio_items
            ]
            ocr_items = [
                {
                    "timestamp_ms": item.offset_ms,
                    "page": item.page,
                    "title": item.title,
                    "text": loads(item.ocr_json, {}).get("body", ""),
                }
                for item in screenshots
            ]
            screen_context = [
                {
                    "timestamp_ms": item.offset_ms,
                    "page": item.page,
                    "title": item.title,
                    "preview": _ocr_preview(item.ocr_json),
                    "text": _ocr_body(item.ocr_json, 1200),
                }
                for item in screenshots
            ]
            if len(phone_drafts) > 300:
                phone_drafts = phone_drafts[:300]
            if len(ocr_items) > 80:
                ocr_items = ocr_items[:80]
            if len(screen_context) > 80:
                screen_context = screen_context[:80]
            evidence = {
                "course_title": course.title,
                "duration_ms": course.duration_ms,
                "transcript": transcript_text,
                "phone_drafts": phone_drafts,
                "ocr": ocr_items,
                "screen_context": screen_context,
                "sections": loads(course.sections, []),
            }
            prompt = f"""请基于 JSON 证据整理课程内容。只能使用证据中的内容，不要编造。
输出必须是 JSON 对象，结构如下：
{{
  "summary": "完整 Markdown 总结，不包含时间戳和页码标记",
  "sections": [{{"section_id":"uuid","title":"标题","start_ms":0,"end_ms":0,"page":null,"summary":"3到5条要点"}}],
  "mindmap": [{{"node_id":"uuid","text":"课程名","children":[{{"node_id":"uuid","text":"知识点","children":[]}}]}}]
}}

处理规则：
- transcript 是最终确认字幕；不要对这份字幕做任何改写或重新转写，只用于生成总结。
- phone_drafts 是手机端草稿，只作补漏和校对参考。
- screen_context/ocr 是手机截图 OCR 结果，用于校准课程页面标题、页码、术语和板书内容。
- summary 是纯内容总结，禁止写入时间戳或页码标记。
- sections 必须保留真实的时间范围 start_ms/end_ms，并尽量用截图标题/页码为分节位置，但不要编造截图没有显示的信息。
- 思维导图必须体现课程主线，并尽量吸收截图中的关键术语、实验步骤、公式或板书要点。

证据：
{json.dumps(evidence, ensure_ascii=False)}"""
            result = None
            last_error: Exception | None = None
            rate_limit_attempts = 0
            normal_attempts = 0
            while True:
                try:
                    result = await chat_json(prompt, llm_settings=llm_settings)
                    break
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 429:
                        normal_attempts += 1
                        last_error = _llm_error(error)
                        if normal_attempts >= 3:
                            raise
                        delay_seconds = 2 ** (normal_attempts - 1) * 5
                        job.message = f"生成失败，{delay_seconds} 秒后服务器自动重试（第 {normal_attempts}/3 次）"
                        await session.commit()
                        await broadcast(
                            course_id,
                            {"type": "status", "status": "generating", "message": job.message},
                        )
                        await asyncio.sleep(delay_seconds)
                        continue
                    rate_limit_attempts += 1
                    last_error = _llm_error(error)
                    if rate_limit_attempts >= 10:
                        raise
                    job.message = f"大模型接口限流（429），10 秒后自动重试（第 {rate_limit_attempts}/10 次）"
                    await session.commit()
                    await broadcast(
                        course_id,
                        {"type": "status", "status": "generating", "message": job.message},
                    )
                    await asyncio.sleep(10)
                except (httpx.HTTPError, ValueError) as error:
                    normal_attempts += 1
                    last_error = error
                    if normal_attempts >= 3:
                        raise
                    delay_seconds = 2 ** (normal_attempts - 1) * 5
                    job.message = f"生成失败，{delay_seconds} 秒后服务器自动重试（第 {normal_attempts}/3 次）"
                    await session.commit()
                    await broadcast(course_id, {"type": "status", "status": "generating", "message": job.message})
                    await asyncio.sleep(delay_seconds)
            if result is None:
                raise last_error or RuntimeError("LLM 没有返回结果")
            result_obj = _ensure_result_object(result)
            course.summary = _summary_text(result_obj.get("summary") or result)
            course.sections = dumps(_normalize_sections(result_obj.get("sections") or []))
            raw_mindmap = result_obj.get("mindmap") or []
            if isinstance(raw_mindmap, dict):
                raw_mindmap = raw_mindmap.get("nodes") or [raw_mindmap]
            if isinstance(raw_mindmap, str):
                raw_mindmap = loads(raw_mindmap, [])
            course.mindmap = dumps(raw_mindmap if isinstance(raw_mindmap, list) else [])
            course.status = "completed"
            job.status = "success"
            job.result = "生成完成"
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": "completed", "message": "生成完成"})
        except Exception as error:
            course.status = "generation_failed"
            job.status = "failed"
            job.message = str(error)
            await session.commit()
            await broadcast(course_id, {"type": "status", "status": course.status, "message": str(error)})


@router.put("/courses/{course_id}/content")
async def update_course(
    course_id: str,
    body: dict,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    course.title = str(body.get("title") or course.title)
    course.summary = str(body.get("summary") or "")
    course.sections = dumps(_normalize_sections(body.get("sections") or []))
    course.mindmap = dumps(body.get("mindmap") or [])
    await session.commit()
    return await get_course(course_id, user, session)


@router.get("/courses/{course_id}/export", response_class=PlainTextResponse)
async def export_course(
    course_id: str,
    format: str = "markdown",
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    if format not in {"markdown", "opml"}:
        raise HTTPException(status_code=400, detail="format must be markdown or opml")

    if format == "markdown":
        lines = [f"# {course.title}", "", "## 全文详细总结", "", course.summary, ""]
        for section in _normalize_sections(loads(course.sections, [])):
            lines.extend([
                f"### {section.get('title', '未命名')}",
                "",
                str(section.get("summary") or ""),
                "",
            ])
        canvas = _build_canvas(loads(course.mindmap, []))
        if canvas["nodes"]:
            lines.extend([
                "## 思维导图",
                "",
                "```canvas",
                json.dumps(canvas, ensure_ascii=False, indent=2),
                "```",
                "",
            ])
        return "\n".join(lines)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        f"  <head><title>{_xml_escape(course.title)}</title></head>",
        "  <body>",
        f'    <outline text="{_xml_escape("全文详细总结")}" _note="{_xml_escape(course.summary)}" />',
    ]
    for section in _normalize_sections(loads(course.sections, [])):
        lines.append(
            f'    <outline text="{_xml_escape(section.get("title", ""))}" _note="{_xml_escape(section.get("summary", ""))}" />'
        )
    mindmap = loads(course.mindmap, [])
    if mindmap:
        lines.append('    <outline text="思维导图">')
        for value in mindmap:
            if isinstance(value, dict):
                _append_opml_outline(lines, value, 1)
        lines.append("    </outline>")
    lines.extend(["  </body>", "</opml>"])
    return "\n".join(lines)


@router.delete("/courses/{course_id}")
async def delete_course(
    course_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    course = await _owned_course(session, user, course_id)
    await session.execute(delete(AudioSegment).where(AudioSegment.course_id == course_id))
    await session.execute(delete(Screenshot).where(Screenshot.course_id == course_id))
    await session.execute(delete(TimelineEvent).where(TimelineEvent.course_id == course_id))
    await session.execute(delete(Job).where(Job.course_id == course_id))
    await session.delete(course)
    await session.commit()
    return {"deleted": True}


@websocket_router.websocket("/courses/{course_id}/stream")
async def course_stream(websocket: WebSocket, course_id: str):
    if not await require_auth_websocket(websocket):
        await websocket.close(code=4401)
        return
    username = websocket.query_params.get("username") or ""
    password = websocket.query_params.get("password") or ""
    async with SessionLocal() as session:
        user = await get_user_by_credentials(session, username, password)
    if user is None:
        await websocket.close(code=4403)
        return
    user_factory = await ensure_user_database(user.id, user.username)
    async with user_factory() as session:
        course = await session.get(Course, course_id)
    if course is None or course.owner_id != user.id:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    connections.setdefault(course_id, set()).add(websocket)
    try:
        await websocket.send_json({"type": "status", "status": "connected"})
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        connections.get(course_id, set()).discard(websocket)
