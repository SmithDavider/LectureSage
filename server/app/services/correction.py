"""OCR 证据驱动的 LLM 转写校正模块。"""

import asyncio
from difflib import SequenceMatcher
import json
import logging
import re

import httpx

from app.services.llm import LlmSettings, chat_json

logger = logging.getLogger("keji.correction")

CORRECTION_SYSTEM = "你是严谨的中文课程转写校对员，只依据明确证据修正，不编造内容。"
MAX_RATE_LIMIT_ATTEMPTS = 3
MAX_NORMAL_ATTEMPTS = 2
_correction_semaphore = asyncio.Semaphore(1)


def _ocr_body(ocr_json: str, limit: int = 900) -> str:
    if not ocr_json:
        return ""
    try:
        data = json.loads(ocr_json)
    except json.JSONDecodeError:
        return ""
    return str(data.get("body") or "").strip()[:limit]


def _ocr_terms(ocr_json: str) -> list[str]:
    if not ocr_json:
        return []
    try:
        data = json.loads(ocr_json)
    except json.JSONDecodeError:
        return []
    raw_values = [str(value) for value in data.get("lines") or [] if str(value).strip()]
    title = str(data.get("title") or "").strip()
    if title:
        raw_values.append(title)
    if not raw_values:
        raw_values = re.split(r"[，。；：、,;:\n]+", str(data.get("body") or ""))
    terms = []
    for raw in raw_values:
        value = re.sub(r"\s+", "", str(raw))
        if 4 <= len(value) <= 18 and not re.fullmatch(r"[0-9.]+", value):
            terms.append(value)
    return list(dict.fromkeys(terms))


def _pick_ocr_evidence(offset_ms: int, screenshots: list) -> dict | None:
    """取 offset_ms <= 字幕起始时间的最新 OCR；课程开头可回退 30 秒。"""
    selected = None
    for screenshot in screenshots or []:
        screenshot_offset = int(getattr(screenshot, "offset_ms", 0) or 0)
        if screenshot_offset <= offset_ms:
            selected = screenshot
        else:
            break
    if selected is None:
        for screenshot in screenshots or []:
            screenshot_offset = int(getattr(screenshot, "offset_ms", 0) or 0)
            if screenshot_offset - offset_ms <= 30_000:
                selected = screenshot
                break
    if selected is None:
        return None

    body = _ocr_body(getattr(selected, "ocr_json", "") or "")
    if not body:
        return None
    return {
        "offset_ms": int(getattr(selected, "offset_ms", 0) or 0),
        "page": getattr(selected, "page", None),
        "title": str(getattr(selected, "title", "") or ""),
        "text": body,
        "terms": _ocr_terms(getattr(selected, "ocr_json", "") or ""),
    }


def _fuzzy_replace(text: str, evidence: dict | None) -> str:
    if not text or not evidence:
        return text
    value = text
    replacements = 0
    for term in evidence.get("terms") or []:
        if term in value or len(term) < 4:
            continue
        best: tuple[float, int] | None = None
        for start in range(0, len(value) - len(term) + 1):
            candidate = value[start:start + len(term)]
            ratio = SequenceMatcher(None, candidate, term).ratio()
            if ratio >= 0.75 and ratio < 1.0 and (best is None or ratio > best[0]):
                best = (ratio, start)
        if best is None:
            continue
        _, start = best
        value = value[:start] + term + value[start + len(term):]
        replacements += 1
        if replacements >= 3:
            break
    return value


def _split_batches(lines: list[dict], max_lines: int = 20, max_chars: int = 3000):
    batch = []
    chars = 0
    for line in lines:
        text = str(line.get("text") or "")
        item_chars = len(text) + 240
        if batch and (len(batch) >= max_lines or chars + item_chars > max_chars):
            yield batch
            batch = []
            chars = 0
        batch.append(line)
        chars += item_chars
    if batch:
        yield batch


def _build_prompt(batch: list[dict], screenshots: list) -> str:
    entries = []
    for index, line in enumerate(batch):
        start_ms = int(line.get("start_ms") or 0)
        end_ms = int(line.get("end_ms") or start_ms)
        text = str(line.get("text") or "").strip()
        entry = f"index={index} 时间={start_ms}~{end_ms}ms 原文：{text}"
        evidence = _pick_ocr_evidence(start_ms, screenshots)
        if evidence:
            entry += (
                f"\nOCR证据：{evidence['text']}"
            )
        entries.append(entry)

    return (
        "请校正下面的中文课堂转写。规则：\n"
        "1. 只能使用每行下方同时间窗的 OCR 证据；OCR 未明确出现的内容不得编造。\n"
        "2. 对转写中与 OCR 术语读音近似或字符相似的错误，替换为 OCR 中的完整原词；"
        "例如 OCR 为‘光合作用、叶绿体’时，应把‘光和作用、夜绿体’分别改为‘光合作用、叶绿体’。\n"
        "3. 只修正同音字、专有名词、数字、公式术语等与 OCR 冲突的错误，保留语气和断句。\n"
        "4. 证据不足时必须原样保留该行。\n"
        "5. 每个输入 index 都必须返回一次，不要复制‘时间’或‘原文’标签。\n"
        "6. 输出只能是 JSON："
        '{"items":[{"index":0,"text":"修正后的文本"}]}。\n\n'
        "输入：\n"
        + "\n".join(entries)
        + "\n\n/no_think"
    )


async def _chat_json_with_retry(prompt: str, llm_settings: LlmSettings) -> dict:
    rate_limit_attempts = 0
    normal_attempts = 0
    while True:
        try:
            async with _correction_semaphore:
                return await chat_json(
                    prompt,
                    system=CORRECTION_SYSTEM,
                    llm_settings=llm_settings,
                )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                rate_limit_attempts += 1
                if rate_limit_attempts > MAX_RATE_LIMIT_ATTEMPTS:
                    raise
                logger.warning("LLM 校正 429，%d 秒后重试", 10)
                await asyncio.sleep(10)
                continue
            normal_attempts += 1
            if normal_attempts >= MAX_NORMAL_ATTEMPTS:
                raise
            delay_seconds = 2 ** normal_attempts
            logger.warning("LLM 校正 HTTP 失败，%s 秒后重试: %s", delay_seconds, error)
            await asyncio.sleep(delay_seconds)
        except (httpx.HTTPError, ValueError) as error:
            normal_attempts += 1
            if normal_attempts >= MAX_NORMAL_ATTEMPTS:
                raise
            delay_seconds = 2 ** normal_attempts
            logger.warning("LLM 校正失败，%s 秒后重试: %s", delay_seconds, error)
            await asyncio.sleep(delay_seconds)


def _parse_items(payload: object) -> dict[int, str]:
    items = payload.get("items") if isinstance(payload, dict) else payload
    mapping = {}
    for item in items or []:
        if not isinstance(item, dict) or item.get("index") is None:
            continue
        try:
            index = int(item["index"])
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            mapping[index] = text
    return mapping


def _is_safe_correction(original: str, corrected: str) -> bool:
    if not original or not corrected:
        return False
    length_ratio = len(corrected) / max(len(original), 1)
    if not 0.65 <= length_ratio <= 1.35:
        return False
    return SequenceMatcher(None, original, corrected).ratio() >= 0.55


async def correct_transcript(
    lines: list[dict],
    screenshots: list,
    llm_settings: LlmSettings,
) -> list[dict]:
    """校正转写；无可校正内容或调用失败时保留 original，不抛异常。"""
    if not lines or not screenshots:
        return lines
    if not any(_ocr_body(getattr(item, "ocr_json", "") or "") for item in screenshots):
        return lines

    corrected = []
    for batch in _split_batches(lines):
        prepared = []
        baselines = []
        evidences = []
        prepared_positions = []
        batch_results = [None] * len(batch)
        for position, line in enumerate(batch):
            evidence = _pick_ocr_evidence(int(line.get("start_ms") or 0), screenshots)
            original = str(line.get("text") or "").strip()
            baseline = _fuzzy_replace(original, evidence)
            if evidence is None or baseline == original:
                item = dict(line)
                if evidence is not None:
                    item["page"] = evidence.get("page") or item.get("page")
                    item["page_title"] = evidence.get("title") or item.get("page_title") or ""
                    item["ocr_evidence_offset_ms"] = evidence["offset_ms"]
                batch_results[position] = item
                continue
            prepared_line = dict(line)
            prepared_line["text"] = baseline
            prepared.append(prepared_line)
            baselines.append(baseline)
            evidences.append(evidence)
            prepared_positions.append(position)
        if not prepared:
            corrected.extend(item for item in batch_results if item is not None)
            continue
        prompt = _build_prompt(prepared, screenshots)
        try:
            payload = await _chat_json_with_retry(prompt, llm_settings)
        except Exception as error:
            logger.warning("LLM 校正失败，本批保留原文: %s", error)
            for position, line, baseline, evidence in zip(prepared_positions, prepared, baselines, evidences):
                item = dict(line)
                original = str(line.get("text") or "").strip()
                if evidence is not None and baseline != original:
                    item["original_text"] = original
                    item["text"] = baseline
                    item["page"] = evidence.get("page") or item.get("page")
                    item["page_title"] = evidence.get("title") or item.get("page_title") or ""
                    item["confirmed"] = True
                    item["review_state"] = "confirmed_by_ocr_rules"
                    item["ocr_evidence_offset_ms"] = evidence["offset_ms"]
                batch_results[position] = item
            continue

        mapping = _parse_items(payload)
        for index, line in enumerate(prepared):
            item = dict(line)
            fixed_text = mapping.get(index) or baselines[index]
            original_text = str(line.get("text") or "").strip()
            evidence = evidences[index]
            if evidence is None or fixed_text is None:
                batch_results[prepared_positions[index]] = item
                continue
            if "转写：" in fixed_text:
                fixed_text = fixed_text.split("转写：", 1)[-1].strip()
            if fixed_text != original_text and not _is_safe_correction(original_text, fixed_text):
                logger.warning("拒绝改动过大的校正 index=%s", index)
                batch_results[prepared_positions[index]] = item
                continue
            item["original_text"] = str(line.get("original_text") or original_text)
            item["text"] = fixed_text
            item["page"] = evidence.get("page") or item.get("page")
            item["page_title"] = evidence.get("title") or item.get("page_title") or ""
            item["confirmed"] = True
            item["review_state"] = "confirmed_by_ocr_llm" if index in mapping else "confirmed_by_ocr_rules"
            if index in mapping:
                item["correction_model"] = llm_settings.model
            item["ocr_evidence_offset_ms"] = evidence["offset_ms"]
            if fixed_text != original_text:
                item["original_text"] = original_text
                item["confidence"] = min(float(item.get("confidence") or 0.65), 0.6)
            batch_results[prepared_positions[index]] = item
        corrected.extend(item for item in batch_results if item is not None)
    return corrected
