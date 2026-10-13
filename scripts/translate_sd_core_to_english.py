#!/usr/bin/env python3
"""Translate GridInstruct natural-language fields into an English derived view.

The script keeps record identifiers, task labels, rule identifiers, scenario
identifiers, tool names, numeric values, and schema keys unchanged. Only string
values containing CJK characters are sent to the configured LLM provider pool.
It is designed to be resumable through a translation cache.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI
from tqdm.auto import tqdm

ROOT = Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def split_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


@dataclass(frozen=True)
class Endpoint:
    provider: str
    base_url: str
    api_key: str
    key_index: int
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}:key{self.key_index + 1}"


_RATE_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0


def build_endpoint_pool() -> list[Endpoint]:
    provider_order = split_env("TRANSLATION_PROVIDER_ORDER") or ["sensenova", "agnes"]
    skip_models = {item.lower() for item in split_env("TRANSLATION_SKIP_MODELS")}
    allow_models = {item.lower() for item in split_env("TRANSLATION_ALLOW_MODELS")}
    endpoints: list[Endpoint] = []
    for provider in provider_order:
        prefix = provider.upper()
        base_urls = split_env(f"{prefix}_BASE_URLS")
        base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
        if not base_urls and base_url:
            base_urls = [base_url]
        keys = split_env(f"{prefix}_API_KEYS")
        models = split_env(f"{prefix}_MODEL_POOL")
        for base_url in base_urls:
            for model in models:
                model_l = model.lower()
                if model_l in skip_models:
                    continue
                if allow_models and model_l not in allow_models:
                    continue
                for key_index, api_key in enumerate(keys):
                    if base_url and api_key and model:
                        endpoints.append(Endpoint(provider, base_url, api_key, key_index, model))
    if not endpoints:
        raise RuntimeError("No translation endpoints configured. Check .env.")
    return endpoints


def wait_for_rate_limit() -> None:
    """Serialize outbound calls to stay under shared RPM quotas."""
    global _NEXT_REQUEST_AT
    min_interval = float(os.environ.get("TRANSLATION_MIN_INTERVAL_SEC", "0.55") or "0.55")
    with _RATE_LOCK:
        now = time.monotonic()
        delay = max(0.0, _NEXT_REQUEST_AT - now)
        _NEXT_REQUEST_AT = max(_NEXT_REQUEST_AT, now) + min_interval
    if delay > 0:
        time.sleep(delay)


def translation_proxy_url() -> str | None:
    for name in ("TRANSLATION_HTTP_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def thinking_extra_body() -> dict[str, Any]:
    flag = (os.environ.get("TRANSLATION_DISABLE_THINKING", "1") or "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return {}
    # SenseNova 6.7 / Agnes put tokens into reasoning unless thinking is disabled.
    return {"thinking": {"type": "disabled"}}


def make_openai_client(endpoint: Endpoint, timeout: float) -> OpenAI:
    proxy = translation_proxy_url()
    kwargs: dict[str, Any] = {
        "base_url": endpoint.base_url,
        "api_key": endpoint.api_key,
        "timeout": timeout,
    }
    if proxy:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("httpx is required for TRANSLATION_HTTP_PROXY") from exc
        kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=timeout)
    return OpenAI(**kwargs)


def message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    # Some gateways only populate reasoning when thinking is left on.
    for attr in ("reasoning", "reasoning_content"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return content or ""


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def contains_cjk(text: Any) -> bool:
    return isinstance(text, str) and bool(CJK_RE.search(text))


def iter_cjk_strings(obj: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    rows: list[tuple[tuple[str, ...], str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            rows.extend(iter_cjk_strings(value, path + (str(key),)))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            rows.extend(iter_cjk_strings(value, path + (str(idx),)))
    elif contains_cjk(obj):
        rows.append((path, obj))
    return rows


def set_path(obj: Any, path: tuple[str, ...], value: str) -> None:
    cur = obj
    for part in path[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    last = path[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(text[start : end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("translation response did not contain a JSON object")


def make_prompt(batch: dict[str, str]) -> list[dict[str, str]]:
    system = (
        "You are a professional translator for power-system dispatch datasets. "
        "Translate Chinese operational text into concise academic English. "
        "Preserve all identifiers exactly, including IEEE14, IEEE 14, scenario IDs, "
        "rule IDs, JSON-like field names, tool names, labels, numbers, percentages, "
        "p.u., and units. Do not add explanations. Return only valid JSON."
    )
    user = (
        "Translate each value in this JSON object. Keep the same keys and return a "
        "JSON object with English string values only:\n"
        + json.dumps(batch, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def make_plain_prompt(text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": (
                "Translate the following power-system dispatch dataset text into concise academic English. "
                "Preserve identifiers, labels, numbers, units, and JSON-like field names exactly. "
                "Return only the English translation, with no explanation.\n\n"
                f"{text}"
            ),
        }
    ]


def clean_plain_translation(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^```(?:text)?", "", value).strip()
    value = re.sub(r"```$", "", value).strip()
    value = value.strip("\"' \n\t")
    if not value:
        raise ValueError("empty plain translation")
    if contains_cjk(value):
        raise ValueError("plain translation still contains CJK characters")
    return value


def call_endpoint_plain_single(endpoint: Endpoint, key: str, text: str, timeout: float) -> dict[str, str]:
    client = make_openai_client(endpoint, timeout)
    create_kwargs: dict[str, Any] = {
        "model": endpoint.model,
        "messages": make_plain_prompt(text),
        "temperature": 0,
        "max_tokens": 2048,
    }
    extra = thinking_extra_body()
    if extra:
        create_kwargs["extra_body"] = extra
    response = client.chat.completions.create(**create_kwargs)
    content = message_text(response.choices[0].message)
    return {key: clean_plain_translation(content)}


def parse_translated_payload(batch: dict[str, str], text: str) -> dict[str, str]:
    parsed = extract_json_object(text)
    out: dict[str, str] = {}
    for key in batch:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing or empty translation for {key}")
        value = value.strip()
        if contains_cjk(value):
            raise ValueError(f"translation still contains CJK characters for {key}")
        out[key] = value
    return out


def call_vllm_endpoint(endpoint: Endpoint, batch: dict[str, str], timeout: float) -> dict[str, str]:
    response = requests.post(
        endpoint.base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {endpoint.api_key}", "Content-Type": "application/json"},
        json={
            "model": endpoint.model,
            "messages": make_prompt(batch),
            "temperature": 0,
            "max_tokens": max(512, min(4096, 96 * len(batch))),
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=(5, timeout),
    )
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"].get("content") or ""
    return parse_translated_payload(batch, text)


def call_vllm_plain_single(endpoint: Endpoint, key: str, text: str, timeout: float) -> dict[str, str]:
    response = requests.post(
        endpoint.base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {endpoint.api_key}", "Content-Type": "application/json"},
        json={
            "model": endpoint.model,
            "messages": make_plain_prompt(text),
            "temperature": 0,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=(5, timeout),
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"].get("content") or ""
    return {key: clean_plain_translation(content)}


def call_endpoint(endpoint: Endpoint, batch: dict[str, str], timeout: float, max_retries: int) -> dict[str, str]:
    wait_for_rate_limit()
    if len(batch) == 1 and endpoint.provider == "agnes":
        key, text = next(iter(batch.items()))
        return call_endpoint_plain_single(endpoint, key, text, timeout)
    if len(batch) == 1 and endpoint.provider == "vllm":
        key, text = next(iter(batch.items()))
        return call_vllm_plain_single(endpoint, key, text, timeout)
    if endpoint.provider == "vllm":
        return call_vllm_endpoint(endpoint, batch, timeout)
    client = make_openai_client(endpoint, timeout)
    last_error: Exception | None = None
    extra = thinking_extra_body()
    for attempt in range(max_retries + 1):
        try:
            create_kwargs: dict[str, Any] = {
                "model": endpoint.model,
                "messages": make_prompt(batch),
                "temperature": 0,
                "max_tokens": max(512, min(4096, 160 * len(batch))),
            }
            if extra:
                create_kwargs["extra_body"] = extra
            response = client.chat.completions.create(**create_kwargs)
            text = message_text(response.choices[0].message)
            return parse_translated_payload(batch, text)
        except Exception as exc:  # noqa: BLE001 - endpoint fallback needs compact errors.
            last_error = exc
            message = str(exc).lower()
            # Do not burn retries on permanent model/route errors.
            if any(token in message for token in ("model is not found", "not_found", "404")):
                break
            # On RPM/quota, fail this endpoint quickly so the pool can rotate.
            if any(token in message for token in ("rpm", "quota", "429")):
                break
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                wait_for_rate_limit()
    raise RuntimeError(f"{endpoint.label} failed: {last_error}")


def translate_batch(
    batch_items: list[tuple[str, str]],
    endpoints: list[Endpoint],
    timeout: float,
    max_retries: int,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    payload = {key: text for key, text in batch_items}
    errors: list[str] = []
    # Keep MODEL_POOL preference order. Only rotate among keys of the same model.
    if endpoints:
        by_model: dict[tuple[str, str], list[Endpoint]] = {}
        model_order: list[tuple[str, str]] = []
        for endpoint in endpoints:
            key = (endpoint.provider, endpoint.model)
            if key not in by_model:
                by_model[key] = []
                model_order.append(key)
            by_model[key].append(endpoint)
        seed = int(batch_items[0][0], 16)
        ordered_endpoints: list[Endpoint] = []
        for model_key in model_order:
            group = by_model[model_key]
            offset = seed % len(group)
            ordered_endpoints.extend(group[offset:] + group[:offset])
    else:
        ordered_endpoints = []
    for endpoint in ordered_endpoints:
        try:
            translated = call_endpoint(endpoint, payload, timeout, max_retries)
            rows = {
                key: {
                    "source": payload[key],
                    "translation": translated[key],
                    "provider": endpoint.provider,
                    "model": endpoint.model,
                    "key_index": str(endpoint.key_index + 1),
                    "translated_at": datetime.now(timezone.utc).isoformat(),
                }
                for key in payload
            }
            return rows, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{endpoint.label}: {type(exc).__name__}: {str(exc)[:240]}")
    if len(batch_items) == 1:
        key, _text = batch_items[0]
        raise RuntimeError(f"all endpoints failed for {key}: {' | '.join(errors)}")
    # Prefer single-item fallback under quota pressure instead of deep recursion.
    if len(batch_items) <= 4:
        merged: dict[str, dict[str, str]] = {}
        split_errors = list(errors)
        for item in batch_items:
            try:
                part, part_errors = translate_batch([item], endpoints, timeout, max_retries)
                merged.update(part)
                split_errors.extend(part_errors)
            except Exception as exc:  # noqa: BLE001
                split_errors.append(f"item {item[0]}: {type(exc).__name__}: {str(exc)[:240]}")
        if merged:
            return merged, split_errors
        raise RuntimeError(f"all endpoints failed for batch: {' | '.join(split_errors[:8])}")
    midpoint = len(batch_items) // 2
    left, left_errors = translate_batch(batch_items[:midpoint], endpoints, timeout, max_retries)
    right, right_errors = translate_batch(batch_items[midpoint:], endpoints, timeout, max_retries)
    return {**left, **right}, errors + left_errors + right_errors


def read_jsonl(path: Path, max_records: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if max_records is not None and idx >= max_records:
                break
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_cache(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    cache: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                translation = row.get("translation")
                if isinstance(translation, str) and contains_cjk(translation):
                    continue
                cache[row["key"]] = row
    return cache


def append_cache(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, row in rows.items():
            handle.write(json.dumps({"key": key, **row}, ensure_ascii=False) + "\n")


def materialize_records(rows: list[dict[str, Any]], cache: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    translated_rows: list[dict[str, Any]] = []
    cjk_remaining = 0
    translated_fields = 0
    missing_translations = 0
    for row in rows:
        new_row = json.loads(json.dumps(row, ensure_ascii=False))
        for path, text in iter_cjk_strings(new_row):
            key = stable_hash(text)
            item = cache.get(key)
            if item:
                set_path(new_row, path, item["translation"])
                translated_fields += 1
            else:
                missing_translations += 1
        if new_row.get("task_type") in {"operation_ticket_check", "regulation_compliance_check"}:
            label = new_row.get("compliance_label")
            if isinstance(label, str) and label:
                new_row["output"] = label
        metadata = new_row.setdefault("metadata", {})
        metadata["language"] = "en"
        metadata["source_language"] = "zh"
        metadata["translation_status"] = "machine_translated"
        metadata["translation_cache_version"] = "v1"
        if any(contains_cjk(text) for _, text in iter_cjk_strings(new_row)):
            cjk_remaining += 1
        translated_rows.append(new_row)
    return translated_rows, {
        "translated_fields": translated_fields,
        "missing_translations": missing_translations,
        "records_with_cjk_remaining": cjk_remaining,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--cache", default="metadata/translation_cache_v1.jsonl")
    parser.add_argument("--report-json", default="reports/english_translation_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/english_translation_v1.2_sd_core.md")
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--shuffle-missing", action="store_true")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    endpoints = build_endpoint_pool()
    input_path = ROOT / args.input
    output_path = ROOT / args.output
    cache_path = ROOT / args.cache
    report_json = ROOT / args.report_json
    report_md = ROOT / args.report_md

    rows = read_jsonl(input_path, args.max_records)
    cache = load_cache(cache_path)

    unique: dict[str, str] = {}
    total_cjk_fields = 0
    for row in rows:
        for _, text in iter_cjk_strings(row):
            total_cjk_fields += 1
            unique.setdefault(stable_hash(text), text)
    missing = [(key, text) for key, text in unique.items() if key not in cache]
    if args.shuffle_missing:
        random.Random(2041).shuffle(missing)

    batches = [missing[i : i + args.batch_size] for i in range(0, len(missing), args.batch_size)]
    endpoint_labels = [endpoint.label for endpoint in endpoints]
    errors: list[str] = []
    translated_count = 0

    if batches:
        with futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(translate_batch, batch, endpoints, args.timeout, args.max_retries): batch
                for batch in batches
            }
            for future in tqdm(futures.as_completed(future_map), total=len(future_map), desc="translate batches"):
                try:
                    new_rows, batch_errors = future.result()
                    cache.update(new_rows)
                    append_cache(cache_path, new_rows)
                    translated_count += len(new_rows)
                    errors.extend(batch_errors[:20])
                except Exception as exc:  # noqa: BLE001
                    batch = future_map[future]
                    errors.append(f"batch failed size={len(batch)} first={batch[0][0]}: {type(exc).__name__}: {exc}")

    translated_rows, materialize_stats = materialize_records(rows, cache)
    write_jsonl(output_path, translated_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "records": len(rows),
        "max_records": args.max_records,
        "total_cjk_fields": total_cjk_fields,
        "unique_cjk_strings": len(unique),
        "cache_entries": len(cache),
        "new_translations": translated_count,
        "batch_size": args.batch_size,
        "workers": args.workers,
        "endpoint_order": endpoint_labels,
        "materialize": materialize_stats,
        "errors_sample": errors[:100],
    }
    write_json(report_json, report)
    report_md.write_text(
        "\n".join(
            [
                "# English Translation Report",
                "",
                f"Generated: `{report['generated_at']}`",
                f"Input: `{args.input}`",
                f"Output: `{args.output}`",
                f"Records: {len(rows)}",
                f"Unique CJK strings: {len(unique)}",
                f"New translations: {translated_count}",
                f"Records with CJK remaining: {materialize_stats['records_with_cjk_remaining']}",
                "",
                "## Endpoint order",
                "",
                *[f"- `{label}`" for label in endpoint_labels],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
