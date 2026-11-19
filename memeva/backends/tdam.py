"""TencentDB-Agent-Memory (Memory Core v3 HTTP API) adapter.

Arms:
  tdam_full : mirrors TDAM's native auto-recall assembly (core/hooks/auto-recall.ts):
              L3 persona  +  L2 scene navigation  +  L1 relevant memories (hybrid BM25+vector+RRF)
  tdam_l1   : L1 relevant memories only
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import httpx

from .base import Backend, Record, Retrieval


NAV_HEADER = "## 🗺️ Scene Navigation"


class TdamError(RuntimeError):
    pass


class TdamBackend(Backend):
    def __init__(self, cfg: dict, mode: str = "full", journal=None):
        c = cfg["tdam"]
        self.c = c
        self.mode = mode
        self.name = f"tdam_{mode}"
        self.top_k = min(int(cfg["top_k"]), 100)
        self.journal = journal
        key = os.environ.get(c["api_key_env"], "")
        self.http = httpx.Client(base_url=c["url"], timeout=120, headers={
            "x-tdai-service-id": c["service_id"], "content-type": "application/json",
            **({"authorization": f"Bearer {key}"} if key else {}),
        }, limits=httpx.Limits(max_connections=128, max_keepalive_connections=64))
        self.pool = ThreadPoolExecutor(max_workers=64)

    # ── low level ──
    def _iso(self, user_id: str) -> dict:
        # TDAM v3 scopes L2 scenes / L3 persona by team+agent only (core/profile/profile-scope.ts); a missing
        # team_id falls back to "default", i.e. still shared by every user. team_id "{user}" (default) gives
        # every end user a private team => private L2/L3. A fixed team_id reproduces the shared-team topology.
        team = self.c.get("team_id") or "{user}"
        return {"team_id": user_id if team == "{user}" else team, "agent_id": self.c["agent_id"], "user_id": user_id}

    def post(self, path: str, body: dict, retries: int = 4) -> dict:
        for attempt in range(retries):
            try:
                r = self.http.post(path, json=body)
                data = r.json()
            except (httpx.TransportError, ValueError) as e:
                if attempt == retries - 1:
                    raise TdamError(f"{path}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if data.get("code") == 0:
                return data.get("data") or {}
            if data.get("code") in (409, 429, 500, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise TdamError(f"{path}: {data.get('code')} {data.get('message')}")
        raise TdamError(path)

    def health(self) -> dict:
        return self.http.get("/health").json()

    # ── ingest ──
    def ingest_user(self, user, state, save):
        """One /v3/conversation/add call per user→assistant turn (how an online agent writes)."""
        uid = user["user_id"]
        done = state.setdefault("done", {})
        maxc = int(self.c["max_chars_per_message"])
        for s in user["sessions"]:
            sid = f"{uid}__{s['session_id']}"[:250]
            base = datetime.fromisoformat(s["date"].replace("Z", "+00:00"))
            turns, cur = [], []
            for m in s["messages"]:
                cur.append(m)
                if m["role"] == "assistant":
                    turns.append(cur)
                    cur = []
            if cur:
                turns.append(cur)
            for ti, turn in enumerate(turns):
                key = f"{s['session_id']}#{ti}"
                if key in done:
                    continue
                msgs = []
                for mi, m in enumerate(turn):
                    ts = (base + timedelta(seconds=ti * 30 + mi)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    text = m["content"] or "(empty)"
                    for k in range(0, len(text), maxc):
                        msgs.append({"role": m["role"], "content": text[k:k + maxc], "timestamp": ts})
                t0 = time.monotonic()
                self.post("/v3/conversation/add", {**self._iso(uid), "session_id": sid, "messages": msgs[:100]})
                done[key] = round(time.monotonic() - t0, 4)
                state["last_write"] = time.time()
                save()

    # ── readiness ──
    def counts(self, user_id):
        iso = self._iso(user_id)
        l1 = self.post("/v3/atomic/count", iso).get("total", 0)
        l2 = self.post("/v3/scenario/count", iso).get("total", 0)
        core = self.post("/v3/core/read", iso).get("content")
        return {"l1": l1, "l2": l2, "l3": 1 if core else 0}

    def pipeline_status(self) -> dict:
        st = self.post("/v2/pipeline/status", {})
        return {k: {"queued": st.get(k, {}).get("queued", 0), "running": st.get(k, {}).get("running", 0),
                    "idle": st.get(k, {}).get("idle", True)} for k in ("l1", "l2", "l3")}

    def pipeline_idle(self) -> bool:
        return all(v["idle"] for v in self.pipeline_status().values())

    def wait_ready(self, user_ids, last_write_ts, log):
        t_start = time.time()
        min_wait = float(self.c["min_wait_seconds"])
        while time.time() - last_write_ts < min_wait:
            log(f"[{self.name}] waiting for L1 idle timeout ({int(min_wait - (time.time() - last_write_ts))}s left)")
            time.sleep(min(60, max(1, min_wait - (time.time() - last_write_ts))))
        prev, stable, snapshots = None, 0, []
        while True:
            snap = {u: self.counts(u) for u in user_ids}
            pst = self.pipeline_status()
            idle = all(v["idle"] for v in pst.values())
            snapshots.append({"t": time.time(), "idle": idle, "total": _sum(snap), "queue": pst})
            stable = stable + 1 if (snap == prev and idle) else 0
            prev = snap
            log(f"[{self.name}] idle={idle} totals={_sum(snap)} queue(l1/l2/l3)="
                f"{pst['l1']['queued']}/{pst['l2']['queued']}/{pst['l3']['queued']} stable={stable}")
            if stable >= int(self.c["stable_polls"]):
                break
            if time.time() - t_start > float(self.c["max_wait_seconds"]):
                log(f"[{self.name}] max wait reached; continuing with current state")
                break
            time.sleep(float(self.c["poll_seconds"]))
        return {"waited": time.time() - t_start, "counts": prev, "snapshots": snapshots}

    # ── retrieval ──
    def _l1(self, uid, query):
        items = self.post("/v3/atomic/search", {**self._iso(uid), "query": query[:2048], "limit": self.top_k}).get("items", [])
        return [Record(x["id"], x["content"], f"L1:{x.get('type', '')}", x.get("score"), None,
                       {"background": x.get("background"), "created_at": x.get("created_at")}) for x in items]

    def _persona(self, uid):
        return self.post("/v3/core/read", self._iso(uid)).get("content")

    def _scenes(self, uid):
        return self.post("/v3/scenario/ls", self._iso(uid)).get("entries", [])

    def retrieve(self, user_id, query):
        t0 = time.monotonic()
        if self.mode == "l1":
            recs = self._l1(user_id, query)
            return Retrieval(recs, time.monotonic() - t0, 1)
        f1 = self.pool.submit(self._l1, user_id, query)
        f3 = self.pool.submit(self._persona, user_id)
        f2 = self.pool.submit(self._scenes, user_id)
        recs, persona, scenes = f1.result(), f3.result(), f2.result()
        pre = []
        if persona:
            # native auto-recall strips the embedded nav and re-generates it (scene-navigation.ts)
            idx = persona.find(NAV_HEADER)
            persona = persona[:idx].rstrip().rstrip("-").rstrip() if idx >= 0 else persona
            pre.append(f"<user-persona>\n{persona.strip()}\n</user-persona>")
        nav = [f"- {e['path']}: {e.get('summary') or ''}".rstrip(": ") for e in scenes if not e["path"].endswith("/")]
        if nav:
            pre.append("<scene-navigation>\n" + "\n".join(nav) + "\n</scene-navigation>")
        return Retrieval(recs, time.monotonic() - t0, 3, preamble=pre)

    # ── inspection / deletion ──
    def _all_atoms(self, uid):
        out, off = [], 0
        while True:
            d = self.post("/v3/atomic/query", {**self._iso(uid), "limit": 100, "offset": off})
            out += d.get("items", [])
            off += 100
            if off >= d.get("total", 0) or not d.get("items"):
                return out

    def dump(self, user_id):
        iso = self._iso(user_id)
        texts = [x["content"] for x in self._all_atoms(user_id)]
        core = self.post("/v3/core/read", iso).get("content")
        if core:
            texts.append(core)
        for e in self._scenes(user_id):
            if not e["path"].endswith("/"):
                c = self.post("/v3/scenario/read", {**iso, "path": e["path"]}).get("content")
                if c:
                    texts.append(c)
        return texts

    def delete_user(self, user_id):
        iso = self._iso(user_id)
        res = {}
        ids = [x["id"] for x in self._all_atoms(user_id)]
        for i in range(0, len(ids), 1000):
            self.post("/v3/atomic/delete", {**iso, "ids": ids[i:i + 1000]})
        res["l1_deleted"] = len(ids)
        sess = sorted({m.get("session_id") for m in self._l0(user_id) if m.get("session_id")})
        for i in range(0, len(sess), 100):
            self.post("/v3/conversation/delete", {**iso, "session_ids": sess[i:i + 100]})
        res["l0_sessions_deleted"] = len(sess)
        scenes = [e["path"] for e in self._scenes(user_id) if not e["path"].endswith("/")]
        for p in scenes:
            self.post("/v3/scenario/rm", {**iso, "path": p})
        res["l2_deleted"] = len(scenes)
        try:
            if self._persona(user_id):
                self.post("/v3/core/write", {**iso, "content": " "})
                res["l3_overwritten"] = True
        except TdamError as e:
            res["l3_error"] = str(e)
        return res

    def _l0(self, uid):
        out, off = [], 0
        while True:
            d = self.post("/v3/conversation/query", {**self._iso(uid), "limit": 100, "offset": off})
            out += d.get("messages", [])
            off += 100
            if off >= d.get("total", 0) or not d.get("messages"):
                return out


def _sum(snap: dict) -> dict:
    tot: dict = {}
    for v in snap.values():
        for k, n in v.items():
            tot[k] = tot.get(k, 0) + n
    return tot
