"""端到端冒烟：上传实时语音分段，等待 WebSocket 回推确认字幕。"""
import asyncio
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import websockets


def _load_env(backend_dir: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = backend_dir / ".env"
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


async def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    env = _load_env(backend_dir)
    username = os.environ.get("KEJI_USERNAME") or env.get("KEJI_USERNAME", "")
    password = os.environ.get("KEJI_PASSWORD") or env.get("KEJI_PASSWORD", "")
    credential = base64.b64encode(f"{username}:{password}".encode()).decode()
    base_url = os.environ.get("KEJI_BASE_URL") or env.get("KEJI_BASE_URL", "http://127.0.0.1:8000")
    wav_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/smoke20.wav")
    if not wav_path.exists():
        raise SystemExit(f"测试音频不存在: {wav_path}")

    course_id = f"smoke-realtime-{uuid.uuid4().hex[:12]}"
    headers = {"Authorization": f"Basic {credential}"}
    ws_uri = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_uri += f"/api/v1/courses/{course_id}/stream?username={username}&password={password}"

    async with httpx.AsyncClient(timeout=90, headers=headers) as client:
        try:
            created = await client.post(
                f"{base_url}/api/v1/courses",
                json={"course_id": course_id, "title": "REALTIME_SMOKE"},
            )
            created.raise_for_status()

            async with websockets.connect(ws_uri) as websocket:
                await asyncio.wait_for(websocket.recv(), timeout=5)
                with wav_path.open("rb") as audio:
                    uploaded = await client.post(
                        f"{base_url}/api/v1/courses/{course_id}/audio",
                        data={"offset_ms": "0", "realtime": "1"},
                        files={"file": (wav_path.name, audio, "audio/wav")},
                    )
                uploaded.raise_for_status()

                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    message = json.loads(
                        await asyncio.wait_for(websocket.recv(), timeout=60)
                    )
                    subtitle = message.get("subtitle")
                    if subtitle and subtitle.get("confirmed"):
                        print("SENSEVOICE_REALTIME_OK")
                        print(json.dumps(subtitle, ensure_ascii=False))
                        return
                raise TimeoutError("60 秒内未收到确认字幕")
        finally:
            await client.delete(f"{base_url}/api/v1/courses/{course_id}")


if __name__ == "__main__":
    asyncio.run(main())
