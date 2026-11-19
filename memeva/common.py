"""Shared helpers: config, JSON IO, token counting, Bedrock LLM client with usage journal."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import boto3
import tiktoken
import yaml
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent
ENC = tiktoken.get_encoding("cl100k_base")


def load_config(extra: str | None = None) -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    if extra:
        _deep_update(cfg, yaml.safe_load(Path(extra).read_text()) or {})
    for key, val in os.environ.items():
        if key.startswith("MEMEVA_"):
            # MEMEVA_TOP_K=20  /  nested with double underscore: MEMEVA_TDAM__URL=http://10.0.0.5:8420
            path = key[7:].lower().split("__")
            node = cfg
            for p in path[:-1]:
                node = node.setdefault(p, {})
            node[path[-1]] = yaml.safe_load(val)
    return cfg


def _deep_update(base: dict, new: dict) -> None:
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def ntokens(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def truncate_tokens(text: str, limit: int) -> str:
    ids = ENC.encode(text, disallowed_special=())
    return text if len(ids) <= limit else ENC.decode(ids[:limit])


def digest(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:n]


def read_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text())


def write_json(path: Path | str, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    tmp.replace(path)


class Journal:
    """Append-only JSONL log (model usage, API timings, errors)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def add(self, **row: Any) -> None:
        row.setdefault("ts", time.time())
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]


class Bedrock:
    """Thin Converse wrapper with retries and token accounting."""

    def __init__(self, region: str, journal: Journal | None = None):
        self.client = boto3.client(
            "bedrock-runtime", region_name=region,
            config=Config(retries={"max_attempts": 10, "mode": "adaptive"}, read_timeout=300),
        )
        self.journal = journal
        self.no_temperature: set[str] = set()

    def chat(self, model: str, prompt: str, system: str | None = None, max_tokens: int = 1024,
             temperature: float = 0.0, stage: str = "", **meta: Any) -> str:
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if model in self.no_temperature:
            kwargs["inferenceConfig"].pop("temperature", None)
        for attempt in range(6):
            try:
                t0 = time.monotonic()
                r = self.client.converse(**kwargs)
                break
            except self.client.exceptions.ThrottlingException:
                time.sleep(2 ** attempt)
            except self.client.exceptions.ValidationException as e:
                # newer models (e.g. Claude 5.x) reject sampling params; fall back to model defaults
                if "temperature" in str(e) and "temperature" in kwargs["inferenceConfig"]:
                    self.no_temperature.add(model)
                    kwargs["inferenceConfig"].pop("temperature")
                    continue
                raise
        else:
            raise RuntimeError("Bedrock throttled after retries")
        usage = r.get("usage", {})
        if self.journal:
            self.journal.add(stage=stage, model=model, input_tokens=usage.get("inputTokens", 0),
                             output_tokens=usage.get("outputTokens", 0),
                             seconds=time.monotonic() - t0, **meta)
        parts = r["output"]["message"]["content"]
        return "".join(p.get("text", "") for p in parts).strip()


def pack_context(blocks: list[str], budget: int, sep: str = "\n---\n") -> tuple[str, list[int]]:
    """Greedy pack ordered text blocks into a token budget; the last block may be cut.

    Returns (context, list of indices of blocks fully or partially included).
    """
    out, used, kept = [], 0, []
    sep_t = ntokens(sep)
    for i, b in enumerate(blocks):
        cost = ntokens(b) + (sep_t if out else 0)
        if used + cost <= budget:
            out.append(b)
            used += cost
            kept.append(i)
            continue
        room = budget - used - (sep_t if out else 0)
        if room > 32:
            out.append(truncate_tokens(b, room))
            kept.append(i)
        break
    return sep.join(out), kept
