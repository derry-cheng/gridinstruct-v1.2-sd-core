"""Small helper for calling the configured LLM API.

This client supports either the official OpenAI endpoint (`OPENAI_API_KEY`) or
the existing SenseNova-compatible endpoint (`SENSENOVA_API_KEY`). Do not
hard-code keys in this file or in generated data scripts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

from openai import OpenAI


OPENAI_BASE_URL = "https://api.openai.com/v1"
SENSENOVA_BASE_URL = "https://token.sensenova.cn/v1"
SENSENOVA_MODEL_FALLBACKS = (
    "deepseek-v4-flash",
    "sensenova-u1-fast",
    "sensenova-6.7-flash-lite",
)


def normalize_proxy_env() -> None:
    """Drop unsupported SOCKS-only env if socksio is unavailable.

    The local desktop environment may export both HTTP(S)_PROXY and ALL_PROXY.
    OpenAI/httpx will try ALL_PROXY first; if it is a SOCKS proxy but `socksio`
    is not installed, client construction fails before any request is sent.
    """
    all_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy")
    if not all_proxy or not all_proxy.lower().startswith("socks"):
        return
    try:
        import socksio  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001 - best-effort environment normalization.
        os.environ.pop("ALL_PROXY", None)
        os.environ.pop("all_proxy", None)


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def configured_models() -> Sequence[str]:
    override = os.getenv("LLM_MODELS", "").strip()
    if override:
        models = tuple(part.strip() for part in override.split(",") if part.strip())
        if models:
            return models
    if os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is set but LLM_MODELS is empty. Set LLM_MODELS to the OpenAI model ids to evaluate.")
    return SENSENOVA_MODEL_FALLBACKS


def build_client() -> OpenAI:
    load_local_env()
    normalize_proxy_env()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key:
        base_url = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL)
        return OpenAI(base_url=base_url, api_key=openai_api_key)

    sensenova_api_key = os.getenv("SENSENOVA_API_KEY")
    if sensenova_api_key:
        base_url = os.getenv("SENSENOVA_BASE_URL", SENSENOVA_BASE_URL)
        return OpenAI(base_url=base_url, api_key=sensenova_api_key)

    raise RuntimeError(
        "Missing API credentials. Set OPENAI_API_KEY for official OpenAI runs or SENSENOVA_API_KEY for the SenseNova-compatible endpoint."
    )


def chat_completion(
    messages: list[dict[str, str]],
    models: Iterable[str] | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> tuple[str, str]:
    """Return (content, model_used), trying configured models in order."""
    client = build_client()
    model_list = tuple(models) if models is not None else tuple(configured_models())
    last_error: Exception | None = None

    for model in model_list:
        try:
            request_client = client.with_options(timeout=timeout) if timeout is not None else client
            kwargs: dict[str, object] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            resp = request_client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            return content, model
        except Exception as exc:  # noqa: BLE001 - fallback should catch provider errors.
            last_error = exc

    raise RuntimeError("All configured LLM models failed.") from last_error


if __name__ == "__main__":
    text, used_model = chat_completion(
        [{"role": "user", "content": "Hello!"}],
        temperature=0,
    )
    print(f"[{used_model}] {text}")
