import asyncio
import io
import base64
import math
import os
import struct
import uuid
import wave

import httpx


base_url = os.environ.get("KEJI_BASE_URL", "http://127.0.0.1:8000/api/v1")
username = os.environ.get("KEJI_USERNAME", "")
password = os.environ.get("KEJI_PASSWORD", "")
credential = base64.b64encode(f"{username}:{password}".encode()).decode()


def make_tone() -> bytes:
    sample_rate = 16000
    duration = 2
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_rate * duration):
            value = int(8000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(struct.pack("<h", value))
        audio.writeframes(bytes(frames))
    buffer.seek(0)
    return buffer.getvalue()


async def main():
    headers = {"Authorization": f"Basic {credential}"}
    course_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=60) as client:
        created = await client.post(
            f"{base_url}/courses",
            headers=headers,
            json={"course_id": course_id, "title": "ASR_SMOKE_TEST"},
        )
        created.raise_for_status()
        print("CREATE", created.json())

        uploaded = await client.post(
            f"{base_url}/courses/{course_id}/audio",
            headers=headers,
            data={"offset_ms": "0"},
            files={"file": ("tone.wav", make_tone(), "audio/wav")},
        )
        uploaded.raise_for_status()
        print("UPLOAD", uploaded.json())

        finished = await client.post(
            f"{base_url}/courses/{course_id}/finish",
            headers=headers,
            json={"duration_ms": 2000},
        )
        finished.raise_for_status()
        print("FINISH", finished.json())

        final_status = ""
        for _ in range(600):
            detail = await client.get(
                f"{base_url}/courses/{course_id}",
                headers=headers,
            )
            detail.raise_for_status()
            data = detail.json()
            final_status = data["status"]
            if final_status not in {"queued", "transcribing", "generating"}:
                break
            await asyncio.sleep(1)
        print("FINAL_STATUS", final_status)
        print("TRANSCRIPT", data["transcript"][:500])

        deleted = await client.delete(
            f"{base_url}/courses/{course_id}",
            headers=headers,
        )
        deleted.raise_for_status()
        print("DELETE", deleted.json())


if __name__ == "__main__":
    asyncio.run(main())
