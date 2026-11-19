"""Aggregate run outputs into report.json + results.md (tables only; narrative lives in docs/REPORT.md)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .common import ROOT, read_json, write_json
from .pipeline import Run
from .stats import mcnemar_exact, pct, wilson


def _dates(text: str, year_hint: int) -> set:
    import re
    from datetime import date
    out = set()
    for y, m, d in re.findall(r"(20\d\d)[-/年](\d{1,2})[-/月](\d{1,2})", text):
        out.add(date(int(y), int(m), int(d)))
    for m, d in re.findall(r"(?<![\d年-])(\d{1,2})月(\d{1,2})[日号]", text):
        try:
            out.add(date(year_hint, int(m), int(d)))
        except ValueError:
            pass
    return out


def temporal_sensitivity(rows: list[dict]) -> dict:
    """web3_zh temporal questions: count answers naming the gold date, or exactly 7 days earlier
    ("下周X" read as "this coming X"). Deterministic, judge-free."""
    from datetime import date, timedelta
    ok = week_early = n = 0
    for r in rows:
        if r["category"] != "temporal":
            continue
        n += 1
        g = date.fromisoformat(r["gold"][:10])
        ds = _dates(r["answer"], g.year)
        ok += g in ds
        week_early += (g - timedelta(days=7)) in ds and g not in ds
    return {"n": n, "gold_date": ok, "gold_minus_7d": week_early, "either": ok + week_early}


def _f(x, nd=1):
    return "—" if x is None or x != x else f"{x:.{nd}f}"


def summarize(run: Run, arms: list[str]) -> dict:
    rep: dict = {"run": run.run, "dataset": run.dataset, "arms": {}, "by_category": {}, "pairwise": {}}
    rows = {a: run.results(a) for a in arms}
    cats = sorted({r["category"] for a in arms for r in rows[a]})
    for a in arms:
        rs = rows[a]
        if not rs:
            continue
        k, n = sum(r["correct"] for r in rs), len(rs)
        cov = [sum(r["coverage"]) / len(r["coverage"]) for r in rs if r.get("coverage")]
        lat = [r["retrieval_seconds"] for r in rs if r["retrieval_calls"]]
        rep["arms"][a] = {"correct": k, "n": n, "accuracy": k / n, "ci95": wilson(k, n),
                          "fact_coverage_4k": sum(cov) / len(cov) if cov else None,
                          "context_tokens_mean": sum(r["context_tokens"] for r in rs) / n,
                          "retrieval_p50": pct(lat, .5) if lat else None, "retrieval_p95": pct(lat, .95) if lat else None}
        if run.dataset == "web3_zh":
            rep["arms"][a]["temporal_sensitivity"] = temporal_sensitivity(rs)
        rep["by_category"][a] = {}
        for c in cats:
            cr = [r for r in rs if r["category"] == c]
            rep["by_category"][a][c] = {"correct": sum(r["correct"] for r in cr), "n": len(cr)}
    # paired comparison on the same questions
    base = "agentcore_spu"
    for a in arms:
        if a == base or not rows.get(a) or not rows.get(base):
            continue
        A = {r["qid"]: r["correct"] for r in rows[base]}
        B = {r["qid"]: r["correct"] for r in rows[a]}
        common = A.keys() & B.keys()
        b = sum(A[q] and not B[q] for q in common)
        c = sum(B[q] and not A[q] for q in common)
        rep["pairwise"][f"{base}_vs_{a}"] = {"n": len(common), f"{base}_only": b, f"{a}_only": c,
                                             "mcnemar_p": mcnemar_exact(b, c)}
    # infra-side artefacts
    for name in ("ingest/tdam/_summary.json", "ingest/agentcore/_summary.json", "readiness/tdam.json", "readiness/agentcore.json"):
        p = run.dir / name
        if p.exists():
            d = read_json(p)
            d.pop("snapshots", None)
            rep.setdefault("infra", {})[name] = d
    for p in sorted((run.dir / "probes").glob("*.json")) if (run.dir / "probes").exists() else []:
        d = read_json(p)
        rep.setdefault("probes", {})[p.stem] = _trim(d)
    rep["usage"] = usage(run)
    rep["cost_estimate"] = cost_estimate(run, rep)
    write_json(run.dir / "report.json", rep)
    (run.dir / "results.md").write_text(markdown(rep, cats))
    return rep


def _trim(d):
    if isinstance(d, dict):
        return {k: (_trim(v) if k not in ("rows", "leaks") else (v[:5] if isinstance(v, list) else v)) for k, v in d.items()}
    return d


def usage(run: Run) -> dict:
    agg: dict = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})
    for r in run.journal.rows():
        key = f"{r.get('stage')}|{r.get('model')}"
        agg[key]["calls"] += 1
        agg[key]["input_tokens"] += r.get("input_tokens", 0)
        agg[key]["output_tokens"] += r.get("output_tokens", 0)
    lite = run.dir / "tdam_internal_usage.jsonl"   # fetched from the TDAM host via deploy/tdam/usage_summary.py
    if lite.exists():
        for line in lite.read_text().splitlines():
            r = json.loads(line)
            m = str(r.get("model"))
            key = "tdam_internal|" + ("tdam-embed" if "embed" in m else "tdam-llm")
            agg[key]["calls"] += 1
            agg[key]["input_tokens"] += r.get("prompt_tokens", 0) or 0
            agg[key]["output_tokens"] += r.get("completion_tokens", 0) or 0
    prices = run.cfg.get("pricing", {}).get("tokens_per_million", {})
    for key, v in agg.items():
        pin, pout = prices.get(key.split("|", 1)[1], [0, 0])
        v["usd"] = round((v["input_tokens"] * pin + v["output_tokens"] * pout) / 1e6, 4)
    return dict(agg)


def cost_estimate(run: Run, rep: dict) -> dict:
    """Public-price estimate for THIS run (not a bill). AgentCore: STM ingest + LTM records (1 month) + retrievals.
    TDAM: internal LLM/embedding tokens (LiteLLM log) + EC2 hours supplied in config (pricing.tdam_ec2_hourly)."""
    pr = run.cfg.get("pricing", {})
    ac = pr.get("agentcore", {})
    out: dict = {}
    ing = run.dir / "ingest" / "agentcore"
    if ing.exists():
        events = sum(len(read_json(p).get("done", {})) for p in ing.glob("*.json") if not p.name.startswith("_"))
        recs = sum((rep.get("infra", {}).get("readiness/agentcore.json", {}).get("counts") or {}).get(u, {}).get(k, 0)
                   for u in (rep.get("infra", {}).get("readiness/agentcore.json", {}).get("counts") or {})
                   for k in ("semantic", "preference", "summary"))
        n_retr = sum(1 for r in run.journal.rows() if r.get("stage") == "answer" and r.get("arm") == "agentcore_spu") * 3
        stm_gb = events * 12 / 1024 / 1024          # billed at >=12 KB per event (our events are <64 KB)
        out["agentcore"] = {"events": events, "ltm_records": recs, "retrieve_calls_eval": n_retr,
                            "stm_ingest_usd": round(stm_gb * ac.get("stm_ingest_per_gb", 0), 4),
                            "ltm_storage_usd_month": round(recs / 1000 * ac.get("ltm_records_per_1000_month", 0), 4),
                            "retrieval_usd": round(n_retr / 1000 * ac.get("ltm_retrievals_per_1000", 0), 4)}
        out["agentcore"]["total_usd"] = round(sum(v for k, v in out["agentcore"].items() if k.endswith("_usd") or k.endswith("_month")), 4)
    tdam_llm = sum(v.get("usd", 0) for k, v in rep.get("usage", {}).items() if k.startswith("tdam_internal|"))
    out["tdam"] = {"internal_llm_embedding_usd": round(tdam_llm, 4),
                   "ec2_hourly_usd": pr.get("tdam_ec2_hourly"), "note": "add EC2/EBS hours, HA replicas, backups and ops time"}
    return out


def markdown(rep: dict, cats: list[str]) -> str:
    L = [f"# Results — run `{rep['run']}` / dataset `{rep['dataset']}`", "",
         "| arm | correct/n | accuracy | 95% CI | 4K fact coverage | ctx tokens | retrieval p50 (s) | p95 (s) |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a, s in rep["arms"].items():
        L.append(f"| {a} | {s['correct']}/{s['n']} | {s['accuracy']*100:.1f}% | {s['ci95'][0]*100:.0f}–{s['ci95'][1]*100:.0f}% | "
                 f"{_f(s['fact_coverage_4k'] * 100 if s['fact_coverage_4k'] is not None else None)}% | {s['context_tokens_mean']:.0f} | "
                 f"{_f(s['retrieval_p50'], 3)} | {_f(s['retrieval_p95'], 3)} |")
    L += ["", "## By category", "", "| arm | " + " | ".join(cats) + " |", "|---|" + "---:|" * len(cats)]
    for a, bc in rep["by_category"].items():
        L.append(f"| {a} | " + " | ".join(f"{bc[c]['correct']}/{bc[c]['n']}" for c in cats) + " |")
    ts = {a: s.get("temporal_sensitivity") for a, s in rep["arms"].items() if s.get("temporal_sensitivity")}
    if ts:
        L += ["", "## Temporal sensitivity (deterministic date match)", "", "| arm | n | gold date | gold−7d (\"下周X\" read as nearest X) | either |", "|---|---:|---:|---:|---:|"]
        for a, t in ts.items():
            L.append(f"| {a} | {t['n']} | {t['gold_date']} | {t['gold_minus_7d']} | {t['either']} |")
    if rep["pairwise"]:
        L += ["", "## Paired comparison (exact McNemar)", ""]
        for k, v in rep["pairwise"].items():
            L.append(f"- {k}: n={v['n']}, discordant={[x for x in v.values()][1:3]}, p={v['mcnemar_p']:.3f}")
    if rep.get("cost_estimate"):
        L += ["", "## Cost estimate (public prices, this run)", "", "```json", json.dumps(rep["cost_estimate"], indent=1), "```"]
    return "\n".join(L) + "\n"
