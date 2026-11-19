"""Aggregate run outputs into report.json + results.md (tables only; narrative lives in docs/REPORT.md)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .common import ROOT, read_json, write_json
from .pipeline import Run
from .stats import mcnemar_exact, pct, wilson


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
    lite = ROOT / "runs" / "litellm_usage.jsonl"
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
    if rep["pairwise"]:
        L += ["", "## Paired comparison (exact McNemar)", ""]
        for k, v in rep["pairwise"].items():
            L.append(f"- {k}: n={v['n']}, discordant={[x for x in v.values()][1:3]}, p={v['mcnemar_p']:.3f}")
    return "\n".join(L) + "\n"
