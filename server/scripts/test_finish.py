import asyncio
import base64
import os
import uuid

import httpx


async def main():
    username = os.environ["KEJI_USERNAME"]
    password = os.environ["KEJI_PASSWORD"]
    credential = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {credential}"}
    course_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=30) as client:
        created = await client.post(
            "http://127.0.0.1:8000/api/v1/courses",
            headers=headers,
            json={"course_id": course_id, "title": "FINISH_TEST"},
        )
        created.raise_for_status()

        finished = await client.post(
            f"http://127.0.0.1:8000/api/v1/courses/{course_id}/finish",
            headers=headers,
            json={"duration_ms": 1000},
        )
        print("FINISH_HTTP", finished.status_code)
        print("FINISH_BODY", finished.json())

        await asyncio.sleep(2)
        detail = await client.get(
            f"http://127.0.0.1:8000/api/v1/courses/{course_id}",
            headers=headers,
        )
        detail.raise_for_status()
        print("COURSE_STATUS", detail.json()["status"])

        deleted = await client.delete(
            f"http://127.0.0.1:8000/api/v1/courses/{course_id}",
            headers=headers,
        )
        deleted.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
