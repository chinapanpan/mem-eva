"""Core benchmark flow: ingest → wait → evaluate."""
from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import datasets
from .backends import make
from .common import ROOT, Bedrock, Journal, ntokens, pack_context, read_json, write_json
from .judge import ANSWER_SYSTEM, answer_prompt, coverage_prompt, judge_prompt, parse_coverage, parse_yes

STORE = {"none": None, "tdam_full": "tdam", "tdam_l1": "tdam", "agentcore_spu": "agentcore",
         "tdam_shared_full": "tdam_shared", "tdam_shared_l1": "tdam_shared"}
STORE_ARM = {"tdam": "tdam_full", "agentcore": "agentcore_spu", "tdam_shared": "tdam_shared_full"}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Run:
    def __init__(self, cfg: dict, run: str, dataset: str):
        self.cfg = copy.deepcopy(cfg)
        self.cfg["tdam"]["service_id"] = f"{cfg['tdam']['service_id']}-{run}"
        self.run, self.dataset = run, dataset
        self.dir = ROOT / "runs" / run / dataset
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.dir / "usage.jsonl")
        self.llm = Bedrock(cfg["region"], self.journal)
        self._backends: dict = {}

    # dataset is frozen into the run dir at prepare time
    def data(self, limit: int | None = None) -> dict:
        p = self.dir / "dataset.json"
        if not p.exists():
            write_json(p, datasets.load(self.dataset, self.cfg, limit))
        return read_json(p)

    def ukey(self, uid: str) -> str:
        return f"{self.run}-{uid}"

    def backend(self, arm: str):
        if arm not in self._backends:
            self._backends[arm] = make(arm, self.cfg, self.journal)
        return self._backends[arm]

    # ── ingest ──
    def ingest(self, stores: list[str], workers: int = 4) -> None:
        data = self.data()
        for store in stores:
            be = self.backend(STORE_ARM[store])
            folder = self.dir / "ingest" / store

            def one(user):
                path = folder / f"{user['user_id']}.json"
                state = read_json(path) if path.exists() else {"started": time.time()}
                if state.get("finished"):
                    return state
                u = dict(user, user_id=self.ukey(user["user_id"]))
                be.ingest_user(u, state, lambda: write_json(path, state))
                state["finished"] = time.time()
                write_json(path, state)
                log(f"[{store}] ingested {user['user_id']} ({len(state['done'])} writes)")
                return state

            t0 = time.time()
            with ThreadPoolExecutor(workers) as ex:
                states = list(ex.map(one, data["users"]))
            lat = [v for s in states for v in s["done"].values()]
            write_json(folder / "_summary.json", {
                "store": store, "users": len(states), "writes": len(lat), "wall_seconds": time.time() - t0,
                "last_write": max(s.get("last_write", 0) for s in states),
                "write_latency": _lat(lat),
            })

    def wait(self, stores: list[str]) -> None:
        data = self.data()
        for store in stores:
            summ = read_json(self.dir / "ingest" / store / "_summary.json")
            be = self.backend(STORE_ARM[store])
            res = be.wait_ready([self.ukey(u["user_id"]) for u in data["users"]], summ["last_write"], log)
            res["ready_at"] = time.time()
            write_json(self.dir / "readiness" / f"{store}.json", res)
            empty = [u for u, c in (res.get("counts") or {}).items() if sum(c.values()) == 0]
            if len(empty) > len(data["users"]) // 2:
                raise SystemExit(f"[{store}] {len(empty)}/{len(data['users'])} users have NO long-term memory after "
                                 f"waiting — extraction is failing (check the memory service's LLM/embedding logs).")

    # ── evaluate ──
    def evaluate(self, arms: list[str], workers: int = 4, coverage: bool = True) -> None:
        data = self.data()
        budget = int(self.cfg["memory_tokens"])
        jobs = [(arm, u, q) for arm in arms for u in data["users"] for q in u["questions"]]

        def one(job):
            arm, u, q = job
            path = self.dir / "results" / arm / f"{q['qid']}.json"
            if path.exists():
                return read_json(path)
            be = self.backend(arm)
            r = be.retrieve(self.ukey(u["user_id"]), q["question"])
            blocks = r.preamble + [x.render() for x in r.records]
            context, kept = pack_context(blocks, budget)
            ans = self.llm.chat(self.cfg["answer_model"], answer_prompt(context, q["question"], q["question_date"]),
                                system=ANSWER_SYSTEM, max_tokens=800, stage="answer", arm=arm, qid=q["qid"])
            verdict = self.llm.chat(self.cfg["judge_model"], judge_prompt(q, ans), max_tokens=10,
                                    stage="judge", arm=arm, qid=q["qid"])
            cov = None
            if coverage and q.get("facts") and arm != "none":
                cov = parse_coverage(self.llm.chat(self.cfg["judge_model"], coverage_prompt(context, q["facts"]),
                                                   max_tokens=200, stage="coverage", arm=arm, qid=q["qid"]), len(q["facts"]))
            row = {"arm": arm, "qid": q["qid"], "user_id": u["user_id"], "category": q["category"],
                   "question": q["question"], "gold": q["answer"], "answer": ans, "judge_raw": verdict,
                   "correct": parse_yes(verdict), "facts": q.get("facts", []), "coverage": cov,
                   "retrieval_seconds": r.seconds, "retrieval_calls": r.calls, "candidates": len(r.records),
                   "context_tokens": ntokens(context), "blocks_kept": len(kept), "context": context}
            write_json(path, row)
            log(f"[{arm}] {q['qid']} correct={row['correct']} cov={cov}")
            return row

        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(one, jobs))

    def results(self, arm: str) -> list[dict]:
        folder = self.dir / "results" / arm
        return [read_json(p) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def _lat(xs: list[float]) -> dict:
    from .stats import pct
    return {"n": len(xs), "p50": pct(xs, .5), "p95": pct(xs, .95), "p99": pct(xs, .99),
            "mean": sum(xs) / len(xs) if xs else None}
