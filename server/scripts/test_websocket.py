import asyncio
from urllib.parse import quote
from pathlib import Path

import websockets


async def main():
    env = {}
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()

    username = env.get("KEJI_USERNAME", "")
    password = env.get("KEJI_PASSWORD", "")
    uri = f"ws://127.0.0.1:8000/api/v1/courses/smoke-test/stream?username={quote(username)}&password={quote(password)}"
    async with websockets.connect(uri) as websocket:
        message = await asyncio.wait_for(websocket.recv(), timeout=3)
        print("WEBSOCKET_OK", message)


if __name__ == "__main__":
    asyncio.run(main())
