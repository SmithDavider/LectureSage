import asyncio
import sys

from app.services.user_admin import delete_user


async def main(username: str) -> None:
    result = await delete_user(username)
    print(f"DELETE_USER_OK username={result['username']} user_id={result['id']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: python server/scripts/delete_user.py 用户名")
    asyncio.run(main(sys.argv[1]))
