from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

AI_CALL_COUNT = 0


@dataclass
class GeminiResult:
    enabled: bool
    decision: str = "NOT_USED"
    score: float = 0.0
    pattern: str = ""
    reason: str = ""
    raw: dict[str, Any] | None = None


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def ai_provider() -> str:
    return os.getenv("AI_PROVIDER", "nvidia").strip().lower()


def fallback_ai_provider(primary: str) -> str:
    configured = os.getenv("AI_FALLBACK_PROVIDER", "").strip().lower()
    if configured:
        return configured
    if primary == "nvidia":
        return "gemini"
    if primary == "gemini":
        return "nvidia"
    return ""


def ai_enabled() -> bool:
    if not env_bool("AI_ENABLED", False):
        return False
    provider = ai_provider()
    if provider == "nvidia":
        return bool(os.getenv("NVIDIA_API_KEY", "").strip())
    if provider == "gemini":
        return bool(os.getenv("GEMINI_API_KEY", "").strip())
    return False


def ai_call_allowed() -> bool:
    global AI_CALL_COUNT
    max_calls = int(env_float("AI_MAX_CALLS_PER_RUN", 25.0))
    if max_calls <= 0:
        return True
    if AI_CALL_COUNT >= max_calls:
        return False
    AI_CALL_COUNT += 1
    return True


def gemini_enabled() -> bool:
    return ai_enabled()


def provider_has_key(provider: str) -> bool:
    if provider == "nvidia":
        return bool(os.getenv("NVIDIA_API_KEY", "").strip())
    if provider == "gemini":
        return bool(os.getenv("GEMINI_API_KEY", "").strip())
    return False


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(str(part.get("text", "")) for part in parts)


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def call_gemini_json(system_instruction: str, prompt: str) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    timeout = env_float("GEMINI_TIMEOUT_SECONDS", 20.0)
    temperature = env_float("GEMINI_TEMPERATURE", 0.1)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "response_mime_type": "application/json",
        },
    }
    response = requests.post(url, headers={"x-goog-api-key": api_key}, json=body, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    text = _extract_text(payload)
    return _json_from_text(text)


def call_nvidia_json(system_instruction: str, prompt: str) -> dict[str, Any]:
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    model = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct").strip()
    base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    timeout = env_float("NVIDIA_TIMEOUT_SECONDS", env_float("GEMINI_TIMEOUT_SECONDS", 20.0))
    temperature = env_float("NVIDIA_TEMPERATURE", env_float("GEMINI_TEMPERATURE", 0.1))
    body = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _json_from_text(text)


def call_ai_json(system_instruction: str, prompt: str) -> dict[str, Any]:
    if not ai_call_allowed():
        raise RuntimeError("AI_MAX_CALLS_PER_RUN reached")
    provider = ai_provider()
    errors: list[str] = []

    providers = [provider]
    fallback_provider = fallback_ai_provider(provider)
    if (
        env_bool("AI_ENABLE_FALLBACK", True)
        and fallback_provider
        and fallback_provider not in providers
        and provider_has_key(fallback_provider)
    ):
        providers.append(fallback_provider)

    for item in providers:
        try:
            if item == "nvidia":
                return call_nvidia_json(system_instruction, prompt)
            if item == "gemini":
                return call_gemini_json(system_instruction, prompt)
            raise ValueError(f"Unsupported AI_PROVIDER={item}")
        except Exception as exc:
            errors.append(f"{item}: {exc}")

    raise RuntimeError("AI providers failed: " + " | ".join(errors))


def evaluate_candle_pattern(context: dict[str, Any]) -> GeminiResult:
    if not ai_enabled():
        return GeminiResult(enabled=False)

    system = (
        "You are a conservative trading-pattern classifier for a scalping funnel. "
        "You do not give financial advice. You only classify the supplied candle data. "
        "Return strict JSON only. Never invent missing data. Prefer WAIT when uncertain."
    )
    prompt = json.dumps(
        {
            "task": "Classify pullback candle confirmation and candidate quality.",
            "rules": {
                "BUY": "Look for bullish confirmation after a pullback: hammer, bullish engulfing, bullish pin bar, or clean rejection from EMA/Fibonacci/breakout retest.",
                "SELL": "Look for bearish confirmation after a pullback: shooting star, bearish engulfing, bearish pin bar, or clean rejection from EMA/Fibonacci/breakout retest.",
                "safety": "If candles are mixed, noisy, low quality, or contradictory, decision must be WAIT.",
            },
            "required_json_schema": {
                "decision": "CONFIRM or WAIT or REJECT",
                "score": "0 to 100",
                "pattern": "short pattern name or none",
                "reason": "short reason under 240 chars",
            },
            "context": context,
        },
        separators=(",", ":"),
    )
    try:
        data = call_ai_json(system, prompt)
    except Exception as exc:
        return GeminiResult(enabled=True, decision="ERROR", reason=f"{ai_provider()} AI error: {exc}")

    decision = str(data.get("decision", "WAIT")).upper()
    try:
        score = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return GeminiResult(
        enabled=True,
        decision=decision,
        score=max(0.0, min(100.0, score)),
        pattern=str(data.get("pattern", "")),
        reason=str(data.get("reason", "")),
        raw=data,
    )


def summarize_funnel_status(context: dict[str, Any]) -> GeminiResult:
    if not ai_enabled():
        return GeminiResult(enabled=False)

    system = (
        "You are a trading-system diagnostic assistant. Explain what the funnel is doing "
        "from supplied logs/counters only. Do not promise profit. Return strict JSON only."
    )
    prompt = json.dumps(
        {
            "task": "Summarize current funnel status, bottlenecks, and next safest action.",
            "required_json_schema": {
                "decision": "OK or WARNING or BLOCKED",
                "score": "0 to 100 system health score",
                "pattern": "main bottleneck label",
                "reason": "concise diagnostic summary under 400 chars",
            },
            "context": context,
        },
        separators=(",", ":"),
    )
    try:
        data = call_ai_json(system, prompt)
    except Exception as exc:
        return GeminiResult(enabled=True, decision="ERROR", reason=f"{ai_provider()} AI error: {exc}")

    try:
        score = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return GeminiResult(
        enabled=True,
        decision=str(data.get("decision", "WARNING")).upper(),
        score=max(0.0, min(100.0, score)),
        pattern=str(data.get("pattern", "")),
        reason=str(data.get("reason", "")),
        raw=data,
    )


def evaluate_fundamental_bias(context: dict[str, Any]) -> GeminiResult:
    if not ai_enabled():
        return GeminiResult(enabled=False)

    system = (
        "You are a conservative macro/fundamental FX bias classifier. "
        "Use only the provided evidence from APIs. Do not invent rates, GDP, COT, or sentiment. "
        "Return strict JSON only. If evidence is weak or mixed, choose NEUTRAL."
    )
    prompt = json.dumps(
        {
            "task": "Classify currency-pair fundamental bias for a trading filter.",
            "rules": {
                "BULLISH": "Base currency evidence is stronger than quote currency evidence.",
                "BEARISH": "Quote currency evidence is stronger than base currency evidence.",
                "NEUTRAL": "Evidence is missing, mixed, stale, or not decisive.",
                "safety": "This layer filters trades. It must not force entries.",
            },
            "required_json_schema": {
                "decision": "BULLISH or BEARISH or NEUTRAL",
                "score": "0 to 100, where 50 is neutral",
                "pattern": "main fundamental driver",
                "reason": "short reason under 300 chars",
            },
            "context": context,
        },
        separators=(",", ":"),
    )
    try:
        data = call_ai_json(system, prompt)
    except Exception as exc:
        return GeminiResult(enabled=True, decision="ERROR", reason=f"{ai_provider()} AI error: {exc}")

    try:
        score = float(data.get("score", 50.0))
    except (TypeError, ValueError):
        score = 50.0
    return GeminiResult(
        enabled=True,
        decision=str(data.get("decision", "NEUTRAL")).upper(),
        score=max(0.0, min(100.0, score)),
        pattern=str(data.get("pattern", "")),
        reason=str(data.get("reason", "")),
        raw=data,
    )


def evaluate_news_risk(context: dict[str, Any]) -> GeminiResult:
    if not ai_enabled():
        return GeminiResult(enabled=False)

    system = (
        "You are a conservative FX news-risk classifier. Use only supplied calendar/news evidence. "
        "High-impact scheduled events near now must be BLOCKED. If evidence is weak, choose NEUTRAL. "
        "Return strict JSON only."
    )
    prompt = json.dumps(
        {
            "task": "Classify news risk and directional news bias for a currency pair.",
            "required_json_schema": {
                "decision": "BLOCKED or CLEAR or AGAINST_TRADE or BIASED or NEUTRAL",
                "score": "0 to 100, where 0 is blocked/high risk and 50 is neutral",
                "pattern": "main news driver",
                "reason": "short reason under 300 chars",
            },
            "context": context,
        },
        separators=(",", ":"),
    )
    try:
        data = call_ai_json(system, prompt)
    except Exception as exc:
        return GeminiResult(enabled=True, decision="ERROR", reason=f"{ai_provider()} AI error: {exc}")
    try:
        score = float(data.get("score", 50.0))
    except (TypeError, ValueError):
        score = 50.0
    return GeminiResult(
        enabled=True,
        decision=str(data.get("decision", "NEUTRAL")).upper(),
        score=max(0.0, min(100.0, score)),
        pattern=str(data.get("pattern", "")),
        reason=str(data.get("reason", "")),
        raw=data,
    )
