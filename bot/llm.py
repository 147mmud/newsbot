"""
Tiny client for FREE-tier, OpenAI-compatible chat APIs (Groq, Gemini, HuggingFace).
Falls through providers on rate-limit / errors. No SDKs needed.
"""
from __future__ import annotations

import logging
import os
import time

import requests

import config

log = logging.getLogger("llm")

_disabled: set[str] = set()
_last_call = 0.0


def _providers():
    for p in config.LLM_PROVIDERS:
        if os.getenv(p["key_env"]) and p["name"] not in _disabled:
            yield p


def available() -> bool:
    return any(True for _ in _providers())


def active_provider_name() -> str:
    return next((p["name"] for p in _providers()), "none")


def _payload(model: str, system: str, user: str, max_tokens: int) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    if "gpt-oss" in model:          # reasoning models: keep thinking short & cheap
        body["reasoning_effort"] = "low"
    return body


def chat(system: str, user: str, max_tokens: int = 2500) -> str | None:
    global _last_call
    for p in list(_providers()):
        for attempt in range(3):
            wait = config.LLM_SECONDS_BETWEEN_CALLS - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.time()
            try:
                r = requests.post(
                    p["url"],
                    headers={"Authorization": f"Bearer {os.environ[p['key_env']]}",
                             "Content-Type": "application/json"},
                    json=_payload(p["model"], system, user, max_tokens),
                    timeout=60,
                )
            except requests.RequestException as exc:
                log.warning("%s network error: %s", p["name"], exc)
                time.sleep(3)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                delay = min(int(r.headers.get("retry-after", 0) or 0) or 10 * (attempt + 1), 60)
                log.warning("%s HTTP %s, retrying in %ss", p["name"], r.status_code, delay)
                time.sleep(delay)
                continue
            if r.status_code != 200:
                log.warning("%s HTTP %s: %s - disabling for this run", p["name"], r.status_code, r.text[:200])
                break
            try:
                return r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError):
                log.warning("%s returned unexpected payload", p["name"])
                break
        _disabled.add(p["name"])  # move on to the next provider
    return None
