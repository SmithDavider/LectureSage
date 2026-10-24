import asyncio
import hashlib
import logging
import os
import time
import re
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from app.config import settings


logger = logging.getLogger("keji.ocr")


# 未启用 HPI/OpenVINO 时，兜底走普通 Paddle 引擎，避开 PP-OCRv6 的 oneDNN PIR 转换问题
if not settings.ocr_enable_hpi:
    os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "False"


_ocr = None
_ocr_lock = asyncio.Lock()
_page_pattern = re.compile(r"(?:第\s*(\d{1,4})\s*页)|(?:\b(\d{1,4})\s*[/%]\s*(\d{1,4})\b)")


def _payload(value):
    if isinstance(value, dict):
        return value
    candidate = getattr(value, "json", None)
    if candidate is not None:
        return candidate() if callable(candidate) else candidate
    candidate = getattr(value, "to_dict", None)
    if candidate is not None:
        return candidate() if callable(candidate) else candidate
    return {}


def _sequence(payload: dict, keys: tuple[str, ...]) -> list:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            return list(value)
    return []


def _initialize_ocr():
    global _ocr
    if _ocr is not None:
        return _ocr

    from paddleocr import PaddleOCR

    model_name = settings.ocr_model.lower()
    if model_name not in ("medium", "pp-ocrv6_medium", "v6_medium"):
        raise ValueError("当前 OCR 仅支持 PP-OCRv6_medium，请检查 KEJI_OCR_MODEL")
    prefix = "PP-OCRv6_medium"
    _ocr = PaddleOCR(
        device="cpu",
        enable_hpi=settings.ocr_enable_hpi,
        text_detection_model_name=f"{prefix}_det",
        text_recognition_model_name=f"{prefix}_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        cpu_threads=settings.ocr_cpu_threads,
    )
    return _ocr


def _run_ocr(path: Path) -> dict:
    started = time.monotonic()
    ocr = _initialize_ocr()
    raw = ocr.predict(str(path))
    texts: list[str] = []
    scores: list[float] = []
    boxes: list[list[int]] = []

    for item in raw:
        payload = _payload(item)
        if "res" in payload and isinstance(payload["res"], dict):
            payload = payload["res"]
        texts.extend(_sequence(payload, ("rec_texts", "texts")))
        scores.extend(_sequence(payload, ("rec_scores", "scores")))
        raw_boxes = _sequence(payload, ("rec_boxes", "boxes"))
        for box in raw_boxes:
            if hasattr(box, "tolist"):
                box = box.tolist()
            if isinstance(box, (list, tuple)) and len(box) >= 4:
                boxes.append([int(v) for v in box[:4]])

    lines = [str(text).strip() for text in texts if str(text).strip()]
    page = None
    for line in reversed(lines):
        match = _page_pattern.search(line)
        if match:
            page = int(next(value for value in match.groups() if value is not None))
            break

    title = ""
    if boxes and lines:
        candidates = []
        for index, box in enumerate(boxes[: min(len(boxes), len(lines))]):
            if index >= len(lines):
                break
            left, top, right, bottom = box
            height = max(1, bottom - top)
            if top <= 400:
                candidates.append((height * 2 - top / 20, lines[index]))
        if candidates:
            title = max(candidates, key=lambda item: item[0])[1]
        else:
            title = lines[0]

    body = "\n".join(lines)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    elapsed_ms = (time.monotonic() - started) * 1000
    logger.info(
        "OCR 完成 path=%s elapsed_ms=%.1f lines=%d",
        path,
        elapsed_ms,
        len(lines),
    )
    return {
        "page": page,
        "title": title[:500],
        "content_hash": content_hash,
        "lines": lines,
        "scores": scores,
        "boxes": boxes,
        "body": body,
    }


async def recognize_image(path: Path) -> dict:
    return await run_in_threadpool(_run_ocr, path)


async def warmup_ocr() -> None:
    await run_in_threadpool(_initialize_ocr)
