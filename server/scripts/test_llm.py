import json
import os
from pathlib import Path

import httpx


env = {}
for line in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

base_url = env["KEJI_LLM_BASE_URL"].rstrip("/")
if not base_url.endswith("/chat/completions"):
    base_url = f"{base_url}/chat/completions"

payload = {
    "model": env["KEJI_LLM_MODEL"],
    "messages": [
        {"role": "user", "content": "只回复 JSON：{\"ok\":true}"},
    ],
}
headers = {
    "Authorization": f"Bearer {env['KEJI_LLM_API_KEY']}",
    "Content-Type": "application/json",
}

with httpx.Client(timeout=30) as client:
    response = client.post(base_url, json=payload, headers=headers)
    print("HTTP_STATUS", response.status_code)
    print("RESPONSE_BODY", response.text[:1000])
