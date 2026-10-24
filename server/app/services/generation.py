"""课程总结的分段、汇总和思维导图生成。"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.services.llm import LlmSettings, chat_json, chat_json_with_repair

logger = logging.getLogger("keji.generation")


class RateLimitError(Exception):
    """LLM 限流重试用尽后抛出的错误。"""


def _text(value) -> str:
    return str(value or "").strip()


def _error_text(error: Exception, fallback: str = "") -> str:
    if isinstance(error, RateLimitError):
        return str(error)
    if isinstance(error, httpx.TimeoutException):
        return f"LLM 请求超时：{error}"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 429:
            return "LLM 限流（429）"
        return f"LLM HTTP {status} 错误：{error}"
    return str(error or fallback).strip()


def normalize_transcript(raw: str) -> list[dict]:
    """把课程转写转换为稳定的句子记录，过滤空值和明显重复项。"""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    result = []
    last_key = None
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text"))
        if not text:
            continue
        start_ms = max(0, int(item.get("start_ms") or item.get("offset_ms") or 0))
        end_ms = max(start_ms, int(item.get("end_ms") or start_ms))
        key = (start_ms, text)
        if key == last_key:
            continue
        last_key = key
        result.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "page": item.get("page"),
            "page_title": _text(item.get("page_title")),
            "confirmed": bool(item.get("confirmed")),
        })
    return result


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.findall(r"[^。！？；!?;\n]+[。！？；!?;]?", text) if part.strip()]



def split_text(text: str, target_chars: int = 5000, max_chars: int = 7500) -> list[str]:
    if not text:
        return []
    sentences = _split_sentences(text) or [text]
    chunks: list[str] = []
    current = ''
    for sentence in sentences:
        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = ''
        if not current and len(sentence) > max_chars:
            chunks.append(sentence)
            continue
        current += sentence
        if len(current) >= target_chars:
            chunks.append(current)
            current = ''
    if current:
        chunks.append(current)
    return chunks


def build_chunks(lines: list[dict], target_chars: int = 5000, max_chars: int = 7500, overlap_chars: int = 350) -> list[dict]:
    """按句子边界分块，保留少量句子重叠，避免跨块语义断裂。"""
    chunks = []
    current: list[dict] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        chunks.append(_make_chunk(len(chunks) + 1, current))
        overlap = []
        chars = 0
        for line in reversed(current):
            if chars >= overlap_chars:
                break
            overlap.insert(0, line)
            chars += len(line["text"])
        current = overlap
        current_chars = chars

    for line in lines:
        sentences = split_text(line["text"], target_chars=target_chars, max_chars=max_chars)
        for sentence in sentences:
            item = dict(line)
            item["text"] = sentence
            size = len(sentence)
            if current and current_chars >= target_chars:
                flush()
            if current and current_chars + size > max_chars:
                flush()
            current.append(item)
            current_chars += size
    flush()
    return chunks


def _make_chunk(number: int, lines: list[dict]) -> dict:
    return {
        "chunk_id": f"chunk-{number:04d}",
        "start_ms": min(item["start_ms"] for item in lines),
        "end_ms": max(item["end_ms"] for item in lines),
        "text": "".join(item["text"] for item in lines),
        "lines": lines,
    }


def _screen_context(chunk: dict, screenshots: list[dict]) -> list[dict]:
    selected = []
    for item in screenshots:
        offset = int(item.get("timestamp_ms") or 0)
        if offset <= chunk["end_ms"] and offset >= chunk["start_ms"] - 60_000:
            selected.append(item)
    return selected[-8:]


async def _call(prompt: str, settings: LlmSettings, attempts: int = 3) -> dict:
    normal = 0
    rate = 0
    while True:
        try:
            return await chat_json(prompt, system="你是严谨的中文课程整理助手，只依据输入证据。", llm_settings=settings)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                rate += 1
                if rate >= 10:
                    raise
                await asyncio.sleep(10)
                continue
            normal += 1
            if normal >= attempts:
                raise
            await asyncio.sleep(2 ** normal)
        except (httpx.HTTPError, ValueError):
            normal += 1
            if normal >= attempts:
                raise
            await asyncio.sleep(2 ** normal)


async def _call_with_repair(
    prompt: str,
    settings: LlmSettings,
    repair_prompt: str,
    attempts: int = 3,
    rate_limit_attempts: int = 10,
) -> dict:
    """带超时重试和非法 JSON 修复的总结 LLM 调用。"""
    normal = 0
    rate = 0
    while True:
        try:
            return await chat_json_with_repair(prompt, system="你是严谨的中文课堂整理助手。", llm_settings=settings, repair_prompt=repair_prompt)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                rate += 1
                if rate >= rate_limit_attempts:
                    raise RateLimitError(f"LLM 限流，已间隔重试 {rate_limit_attempts} 次后仍失败") from error
                logger.warning("LLM 429 限流，%d 秒后重试第 %d/%d 次", 10, rate, rate_limit_attempts)
                await asyncio.sleep(10)
                continue
            normal += 1
            if normal >= attempts:
                raise
            await asyncio.sleep(2 ** normal)
        except (httpx.HTTPError, ValueError) as error:
            # 非法 JSON 或超时统一按普通故障重试；如果已修复失败，下一次再试也不会更难。
            normal += 1
            if normal >= attempts:
                raise
            await asyncio.sleep(2 ** normal)


def _map_prompt(chunk: dict, context: list[dict]) -> str:
    payload = {"chunk_id": chunk["chunk_id"], "start_ms": chunk["start_ms"], "end_ms": chunk["end_ms"], "text": chunk["text"], "screen_context": context}
    return f'''请总结这一段中文课程内容，只能使用输入证据，不得补充外部知识。
输出只能是 JSON，不要 Markdown 代码块：
{{"chunk_id":"{chunk["chunk_id"]}","topic":"主题","summary":"学生容易理解的一段话","key_points":["要点"],"terms":["原文中出现的术语"]}}
要求：key_points 最多5条，terms只能使用输入中出现的词，保留原始 chunk_id。
输入：
{json.dumps(payload, ensure_ascii=False)}'''


async def map_chunk(chunk: dict, screenshots: list[dict], settings: LlmSettings) -> dict:
    repair_prompt = "Map 阶段要求返回 JSON，包含 chunk_id、topic、summary、key_points、terms。"
    result = await _call_with_repair(
        _map_prompt(chunk, _screen_context(chunk, screenshots)),
        settings,
        repair_prompt,
    )
    if not isinstance(result, dict) or not _text(result.get("summary")):
        raise ValueError("Map 阶段返回缺少 summary")
    points = result.get("key_points") or []
    terms = result.get("terms") or []
    return {
        "chunk_id": chunk["chunk_id"],
        "start_ms": chunk["start_ms"],
        "end_ms": chunk["end_ms"],
        "topic": _text(result.get("topic")) or "未命名主题",
        "summary": _text(result.get("summary")),
        "key_points": [_text(x) for x in points if _text(x)][:5],
        "terms": [_text(x) for x in terms if _text(x)][:20],
    }


async def reduce_chunks(course_title: str, duration_ms: int, mapped: list[dict], settings: LlmSettings) -> dict:
    payload = {"course_title": course_title, "duration_ms": duration_ms, "chunk_summaries": mapped}
    prompt = f'''请根据分段摘要生成课程全文总结和分节小结。只能使用输入内容，不得编造。
只输出 JSON：
{{"summary":"不含时间戳的一段全文总结","sections":[{{"title":"章节标题","start_ms":0,"end_ms":1000,"overview":"一句话概要","key_points":["要点"]}}]}}
要求：分节数量4到8节但不要强行凑数；时间必须直接使用输入 chunk 的真实范围；每节2到5个要点；全文总结不要写时间戳。
输入：
{json.dumps(payload, ensure_ascii=False)}'''
    repair_prompt = "Reduce 阶段要求返回 JSON，包含 summary 和 sections。"
    result = await _call_with_repair(prompt, settings, repair_prompt)
    if not isinstance(result, dict) or not _text(result.get("summary")):
        try:
            result = await _call_with_repair(
                prompt + "\n特别注意：summary 字段不能为空，如果模型没有给出，请把输入中的分段内容合并成一段完整总结。",
                settings,
                "Reduce 阶段要求返回 JSON，summary 必须是非空字符串，sections 可以自行生成。",
            )
        except Exception:
            result = {}
    if not isinstance(result, dict) or not _text(result.get("summary")):
        fallback_parts = [
            f"{_text(item.get('topic')) or '片段'}：{_text(item.get('summary'))}"
            for item in mapped
            if _text(item.get("summary"))
        ]
        fallback = "；".join(fallback_parts) if fallback_parts else "暂无可用内容"
        result = dict(result or {})
        result["summary"] = fallback
    sections = []
    for section in result.get("sections") or []:
        if not isinstance(section, dict) or not _text(section.get("title")):
            continue
        start = max(0, int(section.get("start_ms") or 0))
        end = max(start, int(section.get("end_ms") or start))
        overview = _text(section.get("overview") or section.get("summary"))
        points = [_text(x) for x in section.get("key_points") or [] if _text(x)][:5]
        sections.append({"title": _text(section["title"]), "start_ms": start, "end_ms": end, "summary": overview + ("\n" + "\n".join(f"- {point}" for point in points) if points else "")})
    return {"summary": _text(result["summary"]), "sections": sections}


async def generate_mindmap(course_title: str, summary: str, sections: list[dict], settings: LlmSettings) -> list[dict]:
    prompt = f'''根据课程总结和分节小结生成一个最多80个节点、最大4层的思维导图。只能使用输入内容。
只输出 JSON：{{"mindmap":[{{"text":"{course_title}","children":[{{"text":"知识点","children":[]}}]}}]}}
输入：
{json.dumps({"course_title": course_title, "summary": summary, "sections": sections}, ensure_ascii=False)}'''
    repair_prompt = "思维导图阶段要求返回 JSON，包含 mindmap 数组。"
    result = await _call_with_repair(prompt, settings, repair_prompt)
    values = result.get("mindmap") if isinstance(result, dict) else []
    return validate_mindmap(values)


def validate_mindmap(values) -> list[dict]:
    count = 0

    def clean(node, depth: int) -> dict | None:
        nonlocal count
        if count >= 80 or depth > 3 or not isinstance(node, dict) or not _text(node.get("text")):
            return None
        count += 1
        children = []
        for child in node.get("children") or []:
            value = clean(child, depth + 1)
            if value is not None:
                children.append(value)
        return {"text": _text(node["text"]), "children": children}

    roots = []
    for value in values if isinstance(values, list) else []:
        cleaned = clean(value, 0)
        if cleaned is not None:
            roots.append(cleaned)
            break
    return roots


def add_ids(values: list[dict], new_id: Callable[[], str]) -> list[dict]:
    def visit(node: dict) -> dict:
        return {"node_id": new_id(), "text": node["text"], "children": [visit(child) for child in node.get("children") or []]}
    return [visit(value) for value in values]
