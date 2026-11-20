"""Dataset loaders. Every dataset is normalised to:

{"name": str, "users": [{
    "user_id": str,
    "sessions": [{"session_id": str, "date": ISO8601Z, "messages": [{"role": "user"|"assistant", "content": str}]}],
    "questions": [{"qid", "category", "question", "question_date", "answer", "facts": [str], "abstention": bool}],
    "probes": {"unique_tokens": [str], "secrets": [str]}     # optional (web3_zh only)
}]}
"""
from __future__ import annotations

import hashlib
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, read_json

LME_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
LME_CATEGORIES = ["single-session-user", "single-session-assistant", "single-session-preference",
                  "multi-session", "temporal-reasoning", "knowledge-update", "abstention"]


def _lme_date(s: str) -> str:
    # "2023/05/20 (Sat) 02:21" -> ISO
    d = datetime.strptime(re.sub(r"\s*\(\w+\)\s*", " ", s), "%Y/%m/%d %H:%M")
    return d.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def lme_category(item: dict) -> str:
    return "abstention" if item["question_id"].endswith("_abs") else item["question_type"]


def load_lme(cfg: dict) -> dict:
    c = cfg["datasets"]["lme"]
    path = ROOT / c["path"]
    if not path.exists():
        raise SystemExit(f"Download LongMemEval-S first: curl -L -o {path} '{c['url']}'")
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    if h != LME_SHA256:
        raise SystemExit(f"SHA256 mismatch for {path}: {h}")
    items = read_json(path)
    rng = random.Random(c["seed"])
    picked = []
    for cat in LME_CATEGORIES:
        pool = sorted([x for x in items if lme_category(x) == cat], key=lambda x: x["question_id"])
        picked += rng.sample(pool, c["samples_per_category"])
    users = []
    for x in picked:
        sessions = []
        seen: dict[str, int] = {}
        for sid, date, msgs in zip(x["haystack_session_ids"], x["haystack_dates"], x["haystack_sessions"]):
            seen[sid] = seen.get(sid, 0) + 1
            uniq = sid if seen[sid] == 1 else f"{sid}__{seen[sid]}"
            sessions.append({"session_id": uniq, "date": _lme_date(date),
                             "messages": [{"role": m["role"], "content": m["content"]} for m in msgs]})
        sessions.sort(key=lambda s: s["date"])
        users.append({
            "user_id": "lme_" + x["question_id"],
            "sessions": sessions,
            "questions": [{
                "qid": x["question_id"], "category": lme_category(x), "lme_type": x["question_type"],
                "question": x["question"], "question_date": _lme_date(x["question_date"]),
                "answer": str(x["answer"]), "facts": [], "abstention": x["question_id"].endswith("_abs"),
            }],
        })
    return {"name": "lme", "users": users}


def load_web3(cfg: dict) -> dict:
    data = read_json(ROOT / cfg["datasets"]["web3_zh"])
    data["name"] = "web3_zh"
    return data


def load_custom(name: str, cfg: dict) -> dict:
    """Any other dataset: data/<name>/dataset.json (or config datasets.<name>), already in the schema above."""
    path = cfg.get("datasets", {}).get(name) or f"data/{name}/dataset.json"
    data = read_json(ROOT / path)
    data["name"] = name
    return data


def load(name: str, cfg: dict, limit: int | None = None) -> dict:
    if name == "lme":
        data = load_lme(cfg)
    elif name == "web3_zh":
        data = load_web3(cfg)
    else:
        data = load_custom(name, cfg)
    if limit:
        data["users"] = data["users"][:limit]
    return data
