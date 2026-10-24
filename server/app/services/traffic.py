"""上传/下载流量记录，供后台管理界面监控。"""

import time

from app.db import SessionLocal, TrafficLog


async def record_traffic(
    *,
    user_id: int | None = None,
    username: str = "",
    course_id: str = "",
    direction: str = "upload",
    kind: str = "other",
    size_bytes: int = 0,
) -> None:
    async with SessionLocal() as session:
        session.add(
            TrafficLog(
                user_id=user_id,
                username=username,
                course_id=course_id,
                direction=direction,
                kind=kind,
                bytes=max(0, int(size_bytes)),
                created_at=int(time.time() * 1000),
            )
        )
        await session.commit()
