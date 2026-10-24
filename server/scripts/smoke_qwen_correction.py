import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services.correction import correct_transcript
from app.services.llm import LlmSettings


async def main() -> None:
    llm_settings = LlmSettings(
        base_url=settings.correction_llm_base_url,
        api_key=settings.correction_llm_api_key,
        model=settings.correction_llm_model,
        timeout_seconds=settings.correction_llm_timeout_seconds,
        temperature=0.0,
        max_tokens=512,
    )
    lines = [
        {
            "id": "smoke-1",
            "start_ms": 5_000,
            "end_ms": 9_000,
            "text": "植物的光和作用主要发生在夜绿体中。",
            "confirmed": False,
            "review_state": "draft",
        }
    ]
    screenshots = [
        SimpleNamespace(
            offset_ms=0,
            page=1,
            title="光合作用",
            ocr_json=json.dumps(
                {
                    "body": "第三章 光合作用。叶绿体的结构与功能。",
                    "lines": ["光合作用", "叶绿体"],
                },
                ensure_ascii=False,
            ),
        )
    ]
    started = time.perf_counter()
    result = await correct_transcript(lines, screenshots, llm_settings)
    elapsed = time.perf_counter() - started
    print(json.dumps({"elapsed_seconds": elapsed, "result": result}, ensure_ascii=False, indent=2))
    text = result[0].get("text", "")
    if text != "植物的光合作用主要发生在叶绿体中。":
        raise SystemExit("校正结果未达到预期")
    if result[0].get("review_state") != "confirmed_by_ocr_llm":
        raise SystemExit("校正状态未达到预期")


if __name__ == "__main__":
    asyncio.run(main())
