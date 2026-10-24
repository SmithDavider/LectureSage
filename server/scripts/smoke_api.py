import asyncio
import io
import base64
import os
import uuid

import httpx
from PIL import Image, ImageDraw


base_url = os.environ.get("KEJI_BASE_URL", "http://127.0.0.1:8000/api/v1")
username = os.environ.get("KEJI_USERNAME", "")
password = os.environ.get("KEJI_PASSWORD", "")
credential = base64.b64encode(f"{username}:{password}".encode()).decode()


async def main():
    headers = {"Authorization": f"Basic {credential}"}
    course_id = str(uuid.uuid4())

    image = Image.new("RGB", (900, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 80), "12 / 45", fill="black")
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="JPEG")
    image_buffer.seek(0)

    async with httpx.AsyncClient(timeout=30) as client:
        created = await client.post(
            f"{base_url}/courses",
            headers=headers,
            json={"course_id": course_id, "title": "SMOKE_TEST"},
        )
        created.raise_for_status()
        print("CREATE", created.json())

        uploaded = await client.post(
            f"{base_url}/courses/{course_id}/screens",
            headers=headers,
            data={"offset_ms": "1000"},
            files={"file": ("smoke.jpg", image_buffer, "image/jpeg")},
        )
        uploaded.raise_for_status()
        print("UPLOAD", uploaded.json())

        timeline = []
        for _ in range(30):
            await asyncio.sleep(1)
            detail = await client.get(
                f"{base_url}/courses/{course_id}",
                headers=headers,
            )
            detail.raise_for_status()
            timeline = detail.json()["timeline"]
            if timeline:
                break
        print("TIMELINE", timeline)

        deleted = await client.delete(
            f"{base_url}/courses/{course_id}",
            headers=headers,
        )
        deleted.raise_for_status()
        print("DELETE", deleted.json())


if __name__ == "__main__":
    asyncio.run(main())
