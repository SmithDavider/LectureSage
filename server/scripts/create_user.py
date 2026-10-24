import asyncio
import sys

from app.services.user_admin import create_user


async def main(username: str, password: str) -> None:
    result = await create_user(username, password)
    print(f"CREATE_USER_OK username={result['username']} user_id={result['id']}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法: python server/scripts/create_user.py 用户名 密码")
    asyncio.run(main(sys.argv[1], sys.argv[2]))
