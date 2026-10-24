import asyncio
import base64
import os
from pathlib import Path

import httpx


async def main():
    env = {}
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()

    username = env.get("KEJI_USERNAME", "")
    password = env.get("KEJI_PASSWORD", "")
    credential = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {credential}"}
    base_url = "http://127.0.0.1:8000/api/v1"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url}/courses", headers=headers)
        response.raise_for_status()
        failed = [course for course in response.json() if course["status"] == "transcription_failed"]
        print("FAILED_COURSES", [course["course_id"] for course in failed])

        for course in failed:
            course_id = course["course_id"]
            retry = await client.post(
                f"{base_url}/courses/{course_id}/retranscribe",
                headers=headers,
                json={},
            )
            print("RETRY", course_id, retry.status_code, retry.json())

        pending = {course["course_id"] for course in failed}
        for _ in range(300):
            await asyncio.sleep(2)
            detail = await client.get(f"{base_url}/courses", headers=headers)
            detail.raise_for_status()
            states = {
                course["course_id"]: course["status"]
                for course in detail.json()
                if course["course_id"] in pending
            }
            print("STATES", states)
            if all(status not in {"queued", "transcribing"} for status in states.values()):
                break


if __name__ == "__main__":
    asyncio.run(main())
