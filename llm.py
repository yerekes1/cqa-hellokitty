"""Единая функция вызова модели: Gemini (бесплатный тариф) или локальная Ollama."""
import json
import os
import re
import time

import httpx

TIMEOUT = 150
MAX_RATE_RETRIES = 4


class LLMError(Exception):
    pass


def _retry_delay(resp, attempt):
    """Сколько ждать после 429/503: подсказка сервера, иначе растущая пауза."""
    m = re.search(r'"retryDelay":\s*"(\d+)', resp.text)
    return int(m.group(1)) + 1 if m else min(15 * 2 ** attempt, 120)


def call_gemini(prompt, schema, model):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("Не задан GEMINI_API_KEY в .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    if model.startswith("gemma"):  # Gemma не принимает схему: просим JSON текстом
        prompt += "\n\nОтветь только JSON по этой схеме, без пояснений:\n" + json.dumps(schema, ensure_ascii=False)
        config = {}
    else:
        config = {"responseMimeType": "application/json", "responseJsonSchema": schema}
        level = os.environ.get("THINKING_LEVEL", "").strip()
        if level:
            config["thinkingConfig"] = {"thinkingLevel": level}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
    for attempt in range(MAX_RATE_RETRIES):
        try:
            r = httpx.post(url, headers={"x-goog-api-key": key}, json=body, timeout=TIMEOUT)
        except httpx.HTTPError as e:  # зависшая модель не должна останавливать весь прогон
            raise LLMError(f"Gemini {model}: нет ответа ({type(e).__name__})") from e
        if r.status_code == 429 and "PerDay" in r.text:
            raise LLMError(f"Gemini {model}: исчерпан дневной лимит")
        if r.status_code in (429, 500, 503):
            if attempt == MAX_RATE_RETRIES - 1:
                raise LLMError(f"Gemini {r.status_code}: {r.text[:300]}")
            time.sleep(_retry_delay(r, attempt))
            continue
        if r.status_code != 200:
            raise LLMError(f"Gemini {r.status_code}: {r.text[:300]}")
        d = r.json()
        try:
            parts = d["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Пустой ответ Gemini: {json.dumps(d, ensure_ascii=False)[:300]}") from e
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        u = d.get("usageMetadata", {})
        return text, {"input": u.get("promptTokenCount"), "output": u.get("candidatesTokenCount"),
                      "thoughts": u.get("thoughtsTokenCount")}
    raise LLMError("Gemini: попытки исчерпаны")


def call_ollama(prompt, schema, model):
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    body = {"model": model, "stream": False, "format": schema, "think": False,
            "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "user", "content": prompt}]}
    try:
        r = httpx.post(url, json=body, timeout=900)
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama недоступна: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")
    d = r.json()
    return d["message"]["content"], {"input": d.get("prompt_eval_count"), "output": d.get("eval_count")}


def route(channel):
    """Какая модель оценивает этот канал. Звонки это реальные данные, они идут отдельно."""
    if channel == "voice call":
        return os.environ.get("VOICE_PROVIDER", "ollama"), os.environ.get("VOICE_MODEL", "qwen3:8b")
    return os.environ.get("PROVIDER", "gemini"), os.environ.get("MODEL", "gemini-3.5-flash-lite")


def call_model(prompt, schema, provider, model):
    """usage["model"] называет модель, которая реально ответила."""
    if provider == "ollama":
        text, usage = call_ollama(prompt, schema, model)
        return text, {**usage, "model": model}
    if provider != "gemini":
        raise LLMError(f"Неизвестный поставщик: {provider}")
    fallbacks = [m.strip() for m in os.environ.get("FALLBACK_MODELS", "").split(",") if m.strip()]
    errors = []
    for m in [model] + [f for f in fallbacks if f != model]:
        try:
            text, usage = call_gemini(prompt, schema, m)
            return text, {**usage, "model": m}
        except LLMError as e:
            errors.append(str(e))
    raise LLMError("; ".join(errors))
