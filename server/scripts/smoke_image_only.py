import asyncio
import base64
import io
import os
import sys
import uuid

import httpx
from PIL import Image, ImageDraw


base_url = os.environ.get("KEJI_BASE_URL", "http://127.0.0.1:8000/api/v1")
username = os.environ.get("KEJI_USERNAME", "")
password = os.environ.get("KEJI_PASSWORD", "")
credential = base64.b64encode(f"{username}:{password}".encode()).decode()


async def main():
    if not username or not password:
        raise SystemExit("需要 KEJI_USERNAME 和 KEJI_PASSWORD 环境变量")
    headers = {"Authorization": f"Basic {credential}"}
    course_id = str(uuid.uuid4())

    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 80), "农业气象学 第一期", fill="black")
    draw.text((60, 150), "日照时数与积温", fill="black")
    draw.text((60, 220), "12 / 45", fill="black")
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="JPEG")
    image_buffer.seek(0)

    async with httpx.AsyncClient(timeout=60) as client:
        created = await client.post(
            f"{base_url}/courses",
            headers=headers,
            json={
                "course_id": course_id,
                "title": "SMOKE_IMAGE_ONLY",
                "screen_enabled": True,
                "microphone_enabled": False,
            },
        )
        created.raise_for_status()
        print("CREATE", created.json())

        uploaded = await client.post(
            f"{base_url}/courses/{course_id}/screens",
            headers=headers,
            data={"offset_ms": "1000"},
            files={"file": ("smoke_image_only.jpg", image_buffer, "image/jpeg")},
        )
        uploaded.raise_for_status()
        print("UPLOAD_SCREEN", uploaded.json())

        for _ in range(40):
            await asyncio.sleep(1)
            detail = await client.get(
                f"{base_url}/courses/{course_id}",
                headers=headers,
            )
            detail.raise_for_status()
            payload = detail.json()
            timeline = payload.get("timeline") or []
            if timeline:
                print("TIMELINE", timeline)
                break
        else:
            raise SystemExit("截图 OCR 超时")

        finished = await client.post(
            f"{base_url}/courses/{course_id}/finish",
            headers=headers,
            json={"duration_ms": 5000},
        )
        finished.raise_for_status()
        print("FINISH", finished.json())

        status = "queued"
        for _ in range(60):
            await asyncio.sleep(1)
            detail = await client.get(
                f"{base_url}/courses/{course_id}",
                headers=headers,
            )
            detail.raise_for_status()
            status = detail.json().get("status", "")
            print("status", status)
            if status in {"completed", "generation_failed", "transcription_failed"}:
                break

        detail = (await client.get(f"{base_url}/courses/{course_id}", headers=headers)).json()
        print("DETAIL", detail)
        print("RESULT", status)
        if status != "completed":
            raise SystemExit(f"image-only course failed with status={status}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
