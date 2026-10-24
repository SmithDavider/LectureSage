"""用户管理后台要复用的创建/删除用户逻辑，与 scripts/create_user.py 和 scripts/delete_user.py 保持一致。"""

from sqlalchemy import delete, select

from app.db import (
    AudioSegment,
    Course,
    Job,
    Screenshot,
    SessionLocal,
    TimelineEvent,
    User,
    delete_user_database,
    ensure_user_database,
)
from app.dependencies import hash_password


async def create_user(username: str, password: str) -> dict:
    username = str(username or "").strip()
    password = str(password or "")
    if not username:
        raise ValueError("用户名不能为空")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")

    async with SessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("用户已存在")
        salt_b64, password_hash = hash_password(password)
        user = User(
            username=username,
            password_hash=password_hash,
            password_salt=salt_b64,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    await ensure_user_database(user_id, username)
    return {"id": user_id, "username": username}


async def delete_user(username: str) -> dict:
    username = str(username or "").strip()
    if username == "admin":
        raise ValueError("不能删除 admin 账号")

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("用户不存在")
        user_id = user.id

        course_ids = [
            row[0]
            for row in (
                await session.execute(select(Course.id).where(Course.owner_id == user_id))
            ).all()
        ]
        if course_ids:
            await session.execute(
                delete(AudioSegment).where(AudioSegment.course_id.in_(course_ids))
            )
            await session.execute(
                delete(Screenshot).where(Screenshot.course_id.in_(course_ids))
            )
            await session.execute(
                delete(TimelineEvent).where(TimelineEvent.course_id.in_(course_ids))
            )
            await session.execute(delete(Job).where(Job.course_id.in_(course_ids)))
            await session.execute(delete(Course).where(Course.owner_id == user_id))
        await session.delete(user)
        await session.commit()

    await delete_user_database(user_id)
    return {"id": user_id, "username": username}
