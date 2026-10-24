import json
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass(frozen=True)
class LlmSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    temperature: float | None = None
    max_tokens: int | None = None


async def chat_json(
    prompt: str,
    system: str = "你是严谨的中文课程整理助手。",
    llm_settings: LlmSettings | None = None,
) -> dict:
    configured = llm_settings or LlmSettings(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    if not configured.base_url or not configured.model:
        raise ValueError("LLM 未配置：请设置 KEJI_LLM_BASE_URL 和 KEJI_LLM_MODEL")

    base_url = configured.base_url.rstrip("/")
    if not base_url.endswith("/chat/completions"):
        base_url = f"{base_url}/chat/completions"

    payload = {
        "model": configured.model,
        "messages": [
            {"role": "user", "content": f"{system}\n\n{prompt}"},
        ],
    }
    if configured.temperature is not None:
        payload["temperature"] = configured.temperature
    if configured.max_tokens is not None:
        payload["max_tokens"] = configured.max_tokens
    headers = {
        "Authorization": f"Bearer {configured.api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=configured.timeout_seconds) as client:
        response = await client.post(base_url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()

    try:
        content = body["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"LLM 响应结构异常：{str(body)[:500]}") from error
    if not str(content).strip():
        raise ValueError(f"LLM 返回内容为空：{str(body)[:500]}")
    try:
        text = content.strip()
        if text.startswith("```"):
            _, _, text = text.partition("\n")
            text = text[:-3] if text.rstrip().endswith("```") else text
            text = text.strip()
        parsed = _parse_json_value(text)
        if isinstance(parsed, list):
            # 部分模型会把总结直接输出成列表，放在 summary 下，避免下游把返回当成非法 JSON。
            return {"summary": parsed, "sections": [], "mindmap": []}
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(f"LLM 返回的不是 JSON 对象：{text[:200]}")
    except json.JSONDecodeError as error:
        raise ValueError(f"LLM 返回的不是有效 JSON：{_error_payload(content)}") from error


def _parse_json_value(text: str):
    stripped = text.strip()
    if not stripped:
        raise json.JSONDecodeError("empty content", text, 0)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start >= 0:
        end = stripped.rfind("}")
        if end > start:
            return json.loads(stripped[start:end + 1])
    start = stripped.find("[")
    if start >= 0:
        end = stripped.rfind("]")
        if end > start:
            return json.loads(stripped[start:end + 1])
    raise ValueError(f"LLM 返回的不是 JSON：{text[:200]}")


async def chat_json_with_repair(
    prompt: str,
    system: str,
    llm_settings: LlmSettings,
    repair_prompt: str,
) -> dict:
    """解析失败时让 LLM 自我修复一次；修复失败则抛出最后一轮原始错误。"""
    try:
        return await chat_json(prompt, system=system, llm_settings=llm_settings)
    except ValueError as first_error:
        msg = str(first_error)
        if "json" not in msg.lower() and "内容为空" not in msg:
            raise ValueError(msg) from first_error
        # 从错误信息里拿原始内容片段做修复输入；空输出没有原文，用修复重试代替。
        raw = _extract_content_from_error(msg)
        repaired_prompt = (
            f"{repair_prompt}\n\n"
            "请严格检查以下内容并输出修复后的 JSON，不要解释，不要 Markdown 代码块。\n"
            + (f"原始输出：\n{raw[:3000]}" if raw else "上一轮 LLM 返回内容为空，请直接重新输出要求的 JSON。")
        )
        try:
            return await chat_json(
                repaired_prompt,
                system="你是严格 JSON 修复器，只能输出合法 JSON。",
                llm_settings=llm_settings,
            )
        except ValueError as repair_error:
            raise ValueError(f"JSON 修复失败：{repair_error}") from repair_error


def _extract_content_from_error(message: str) -> str:
    """从 chat_json 抛出的错误信息中尽量提取原始输出片段。"""
    for marker in ("原始输出：", "LLM 返回的不是有效 JSON：", "LLM 返回的不是 JSON 对象：", "LLM 返回的不是 JSON："):
        index = message.find(marker)
        if index >= 0:
            return message[index + len(marker):].strip()
    return ""


def _error_payload(content: str) -> str:
    return f"原始输出：{content[:5000]}"
