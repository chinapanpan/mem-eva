"""Amazon Bedrock AgentCore Memory adapter.

One Memory resource with three built-in long-term strategies (SPU):
  semantic   -> /u/{actorId}/semantic/
  preference -> /u/{actorId}/preference/
  summary    -> /u/{actorId}/summary/{sessionId}/
Retrieval: one RetrieveMemoryRecords call per strategy namespace (issued in parallel),
results interleaved by rank (sem#1, pref#1, sum#1, sem#2, ...).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config

from ..common import ROOT, digest, ntokens, read_json, write_json
from .base import Backend, Record, Retrieval

STRATEGIES = {
    "semantic": ("semanticMemoryStrategy", "/u/{actorId}/semantic/"),
    "preference": ("userPreferenceMemoryStrategy", "/u/{actorId}/preference/"),
    "summary": ("summaryMemoryStrategy", "/u/{actorId}/summary/{sessionId}/"),
}
MANIFEST = ROOT / "runs" / "agentcore_resources.json"


class Pacer:
    """Token bucket (tokens/minute) shared by all ingest threads."""

    def __init__(self, per_minute: int):
        self.rate = per_minute / 60.0
        self.allow = float(per_minute) / 6
        self.cap = self.allow
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def wait(self, n: int) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.allow = min(self.cap, self.allow + (now - self.t) * self.rate)
                self.t = now
                if self.allow >= min(n, self.cap):
                    self.allow -= n
                    return
                need = (min(n, self.cap) - self.allow) / self.rate
            time.sleep(min(need, 5))


def _cfg():
    return Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=128)


def provision(cfg: dict) -> dict:
    if MANIFEST.exists() and not read_json(MANIFEST).get("deleted"):
        return read_json(MANIFEST)
    ctl = boto3.client("bedrock-agentcore-control", region_name=cfg["region"], config=_cfg())
    name = f"{cfg['agentcore']['memory_name_prefix']}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    strategies = [{k: {"name": n, "namespaces": [ns]}} for n, (k, ns) in STRATEGIES.items()]
    mem = ctl.create_memory(
        name=name, description="mem-eva benchmark (SPU)", eventExpiryDuration=int(cfg["agentcore"]["event_expiry_days"]),
        memoryStrategies=strategies, tags={"Purpose": "mem-eva-benchmark", "ManagedBy": "mem-eva"},
    )["memory"]
    manifest = {"region": cfg["region"], "memory_id": mem["id"], "name": name, "created_at": time.time()}
    write_json(MANIFEST, manifest)
    while True:
        m = ctl.get_memory(memoryId=mem["id"])["memory"]
        if m["status"] == "ACTIVE":
            break
        if m["status"] == "FAILED":
            raise RuntimeError(f"memory failed: {m}")
        time.sleep(10)
    manifest["strategies"] = {s["name"]: s["strategyId"] for s in m["strategies"]}
    write_json(MANIFEST, manifest)
    return manifest


def cleanup(cfg: dict) -> None:
    if not MANIFEST.exists():
        print("no manifest")
        return
    man = read_json(MANIFEST)
    if man.get("deleted"):
        print("already deleted")
        return
    ctl = boto3.client("bedrock-agentcore-control", region_name=man["region"])
    ctl.delete_memory(memoryId=man["memory_id"])
    man["deleted"] = time.time()
    write_json(MANIFEST, man)
    print("deleted", man["memory_id"])


class AgentCoreBackend(Backend):
    name = "agentcore_spu"

    def __init__(self, cfg: dict, journal=None, manifest: dict | None = None):
        self.cfg = cfg
        self.c = cfg["agentcore"]
        self.man = manifest or read_json(MANIFEST)
        self.mid = self.man["memory_id"]
        self.top_k = int(cfg["top_k"])
        self.dp = boto3.client("bedrock-agentcore", region_name=cfg["region"], config=_cfg())
        self.pacer = Pacer(int(self.c["ingest_tokens_per_minute"]))
        self.pool = ThreadPoolExecutor(max_workers=96)
        self.journal = journal

    @staticmethod
    def actor(uid: str) -> str:
        return uid.replace(".", "-")[:255]

    # ── ingest ──
    def ingest_user(self, user, state, save):
        uid = self.actor(user["user_id"])
        done = state.setdefault("done", {})
        for s in user["sessions"]:
            sid = "s" + digest([user["user_id"], s["session_id"]], 24)
            base = datetime.fromisoformat(s["date"].replace("Z", "+00:00"))
            for ci, chunk in enumerate(_chunks(s["messages"])):
                key = f"{s['session_id']}#{ci}"
                if key in done:
                    continue
                self.pacer.wait(sum(ntokens(m["content"]) + 8 for m in chunk) + 500)
                t0 = time.monotonic()
                self.dp.create_event(
                    memoryId=self.mid, actorId=uid, sessionId=sid,
                    eventTimestamp=base + timedelta(seconds=ci * 30),
                    clientToken=digest([uid, key, len(chunk)], 32),
                    payload=[{"conversational": {"role": m["role"].upper(), "content": {"text": m["content"][:90000] or "(empty)"}}}
                             for m in chunk],
                )
                done[key] = round(time.monotonic() - t0, 4)
                state["last_write"] = time.time()
                save()

    # ── readiness ──
    def _records(self, uid: str, path: str | None = None) -> list[dict]:
        kw = {"memoryId": self.mid, "namespacePath": path or f"/u/{uid}/", "maxResults": 100}
        out = []
        while True:
            r = self.dp.list_memory_records(**kw)
            out += r.get("memoryRecordSummaries", [])
            if not r.get("nextToken"):
                return out
            kw["nextToken"] = r["nextToken"]

    def counts(self, user_id):
        uid = self.actor(user_id)
        recs = self._records(uid)
        out = {k: 0 for k in STRATEGIES}
        for r in recs:
            ns = (r.get("namespaces") or [""])[0]
            for k in STRATEGIES:
                if f"/{k}/" in ns:
                    out[k] += 1
        return out

    def failed_jobs(self, user_id: str) -> list[dict]:
        kw = {"memoryId": self.mid, "filter": {"actorId": self.actor(user_id), "status": "FAILED"}, "maxResults": 50}
        out = []
        while True:
            r = self.dp.list_memory_extraction_jobs(**kw)
            out += r.get("jobs", [])
            if not r.get("nextToken"):
                return out
            kw["nextToken"] = r["nextToken"]

    def wait_ready(self, user_ids, last_write_ts, log):
        t_start = time.time()
        min_wait = float(self.c["min_wait_seconds"])
        repaired = 0
        while time.time() - last_write_ts < min_wait:
            log(f"[agentcore] waiting for async extraction ({int(min_wait - (time.time() - last_write_ts))}s left)")
            time.sleep(min(60, max(1, min_wait - (time.time() - last_write_ts))))
        prev, stable, snapshots = None, 0, []
        while True:
            snap = dict(zip(user_ids, self.pool.map(self.counts, user_ids)))
            failed = [j for u in user_ids for j in self.failed_jobs(u)]
            for j in failed:
                try:
                    self.dp.start_memory_extraction_job(memoryId=self.mid, extractionJob={"jobId": j["jobID"] if "jobID" in j else j["jobId"]})
                    repaired += 1
                except Exception as e:  # noqa: BLE001
                    log(f"[agentcore] repair failed: {e}")
            snapshots.append({"t": time.time(), "failed": len(failed), "total": _sum(snap)})
            stable = stable + 1 if (snap == prev and not failed) else 0
            prev = snap
            log(f"[agentcore] totals={_sum(snap)} failed_jobs={len(failed)} stable={stable}")
            if stable >= int(self.c["stable_polls"]):
                break
            if time.time() - t_start > float(self.c["max_wait_seconds"]):
                log("[agentcore] max wait reached; continuing with current state")
                break
            time.sleep(float(self.c["poll_seconds"]))
        return {"waited": time.time() - t_start, "counts": prev, "repaired_jobs": repaired, "snapshots": snapshots}

    # ── retrieval ──
    def _search(self, uid: str, kind: str, query: str) -> list[Record]:
        r = self.dp.retrieve_memory_records(
            memoryId=self.mid, namespacePath=f"/u/{uid}/{kind}/",
            searchCriteria={"searchQuery": query[:10000], "topK": self.top_k}, maxResults=100)
        out = []
        for x in r.get("memoryRecordSummaries", []):
            if any(not ns.startswith(f"/u/{uid}/") for ns in x.get("namespaces", [])):
                raise RuntimeError("record outside requested actor scope")
            content = x.get("content", {})
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            ts = x.get("timestamp")  # source event time (createdAt = processing time, not used)
            out.append(Record(x["memoryRecordId"], text, kind, x.get("score"),
                              ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else None))
        return out

    def retrieve(self, user_id, query):
        uid = self.actor(user_id)
        t0 = time.monotonic()
        futs = [self.pool.submit(self._search, uid, k, query) for k in STRATEGIES]
        lists = [f.result() for f in futs]
        merged, seen = [], set()
        for i in range(max((len(x) for x in lists), default=0)):
            for lst in lists:
                if i < len(lst) and lst[i].id not in seen:
                    merged.append(lst[i])
                    seen.add(lst[i].id)
        return Retrieval(merged, time.monotonic() - t0, len(STRATEGIES))

    # ── inspection / deletion ──
    def dump(self, user_id):
        recs = self._records(self.actor(user_id))
        return [(r.get("content") or {}).get("text", "") for r in recs]

    def delete_user(self, user_id):
        uid = self.actor(user_id)
        ids = [r["memoryRecordId"] for r in self._records(uid)]
        for i in range(0, len(ids), 100):
            self.dp.batch_delete_memory_records(memoryId=self.mid, records=[{"memoryRecordId": x} for x in ids[i:i + 100]])
        events = 0
        kw = {"memoryId": self.mid, "actorId": uid, "maxResults": 100}
        sessions = []
        while True:
            r = self.dp.list_sessions(**kw)
            sessions += [s["sessionId"] for s in r.get("sessionSummaries", [])]
            if not r.get("nextToken"):
                break
            kw["nextToken"] = r["nextToken"]
        for sid in sessions:
            ekw = {"memoryId": self.mid, "actorId": uid, "sessionId": sid, "maxResults": 100, "includePayloads": False}
            while True:
                r = self.dp.list_events(**ekw)
                for e in r.get("events", []):
                    self.dp.delete_event(memoryId=self.mid, actorId=uid, sessionId=sid, eventId=e["eventId"])
                    events += 1
                if not r.get("nextToken"):
                    break
                ekw["nextToken"] = r["nextToken"]
        return {"records_deleted": len(ids), "events_deleted": events, "sessions": len(sessions)}


def _chunks(messages: list[dict], max_msgs: int = 20, max_tokens: int = 3000):
    cur, tok = [], 0
    for m in messages:
        t = ntokens(m["content"])
        if cur and (len(cur) >= max_msgs or tok + t > max_tokens):
            yield cur
            cur, tok = [], 0
        cur.append(m)
        tok += t
    if cur:
        yield cur


def _sum(snap: dict) -> dict:
    tot: dict = {}
    for v in snap.values():
        for k, n in v.items():
            tot[k] = tot.get(k, 0) + n
    return tot
