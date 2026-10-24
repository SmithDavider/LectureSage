#!/usr/bin/env python3
"""SenseVoice 课后整段转写冒烟：上传真实音频、触发 finish、轮询转录结果。"""
import asyncio
import base64
import sys
import uuid
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


async def main():
    input_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/keji_90s.wav")
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()

    username = env.get("KEJI_USERNAME", "")
    password = env.get("KEJI_PASSWORD", "")
    credential = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {credential}"}
    base_url = "http://127.0.0.1:8000/api/v1"
    course_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=120) as client:
        created = await client.post(
            f"{base_url}/courses",
            headers=headers,
            json={"course_id": course_id, "title": "SENSE_MERGED_SMOKE"},
        )
        created.raise_for_status()

        with input_path.open("rb") as audio:
            uploaded = await client.post(
                f"{base_url}/courses/{course_id}/audio",
                headers=headers,
                data={"offset_ms": "0"},
                files={"file": (input_path.name, audio, "audio/wav")},
            )
        uploaded.raise_for_status()
        print("UPLOAD", uploaded.json())

        finished = await client.post(
            f"{base_url}/courses/{course_id}/finish",
            headers=headers,
            json={"duration_ms": 90000},
        )
        finished.raise_for_status()
        print("FINISH", finished.json())

        final_status = ""
        for _ in range(300):
            detail = await client.get(
                f"{base_url}/courses/{course_id}",
                headers=headers,
            )
            detail.raise_for_status()
            data = detail.json()
            final_status = data["status"]
            if final_status not in {"queued", "transcribing"}:
                break
            await asyncio.sleep(2)

        print("FINAL_STATUS", final_status)
        transcript = data.get("transcript") or ""
        print("TRANSCRIPT_PREVIEW", transcript[:800])
        print("TRANSCRIPT_CHARS", len(transcript))
        assert final_status == "transcribed", final_status
        assert len(transcript) > 50, transcript

        deleted = await client.delete(
            f"{base_url}/courses/{course_id}",
            headers=headers,
        )
        deleted.raise_for_status()
        print("DELETE", deleted.json())


if __name__ == "__main__":
    asyncio.run(main())
