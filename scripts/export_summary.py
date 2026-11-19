"""Export a publishable snapshot of a finished run into reports/<run>/:
summary.json (all aggregate numbers + probes), <dataset>_results.md, <dataset>_per_question.csv.
No contexts or raw logs are exported (they are reproducible and large)."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memeva.common import ROOT, read_json, write_json  # noqa: E402

run = sys.argv[1] if len(sys.argv) > 1 else "final"
src, dst = ROOT / "runs" / run, ROOT / "reports" / run
dst.mkdir(parents=True, exist_ok=True)
out: dict = {"run": run, "datasets": {}}
for ds_dir in sorted(p for p in src.iterdir() if p.is_dir()):
    rep_p = ds_dir / "report.json"
    if not rep_p.exists():
        continue
    rep = read_json(rep_p)
    probes = {}
    for p in sorted((ds_dir / "probes").glob("*.json")) if (ds_dir / "probes").exists() else []:
        probes[p.stem] = read_json(p)
    extra = ds_dir / "probes_profile_sharing.jsonl"
    if extra.exists():
        probes["profile_sharing"] = [json.loads(x) for x in extra.read_text().splitlines() if x.strip()]
    rep["probes_full"] = probes
    out["datasets"][ds_dir.name] = rep
    if (ds_dir / "results.md").exists():
        (dst / f"{ds_dir.name}_results.md").write_text((ds_dir / "results.md").read_text())
    with (dst / f"{ds_dir.name}_per_question.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arm", "qid", "user_id", "category", "correct", "coverage", "context_tokens", "retrieval_seconds"])
        deleted = {r["user"] for rows in (probes.get("deletion") or {}).values() for r in rows}
        for arm_dir in sorted((ds_dir / "results").iterdir()):
            for p in sorted(arm_dir.glob("*.json")):
                r = read_json(p)
                # arms evaluated AFTER the destructive deletion probe would see empty memory for those users
                if arm_dir.name == "agentcore_path" and r["user_id"] in deleted:
                    continue
                w.writerow([arm_dir.name, r["qid"], r["user_id"], r["category"], int(r["correct"]),
                            "" if r.get("coverage") is None else f"{sum(r['coverage'])}/{len(r['coverage'])}",
                            r["context_tokens"], f"{r['retrieval_seconds']:.4f}"])
import re  # noqa: E402

def scrub(o):
    """Remove account IDs / instance IDs / memory IDs from string values (numbers are left alone)."""
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    if isinstance(o, str):
        o = re.sub(r"\b\d{12}\b", "<account-id>", o)
        o = re.sub(r"\bi-[0-9a-f]{8,17}\b", "<instance-id>", o)
        return re.sub(r"memeva_\d{8}_\d{6}-\w+", "<memory-id>", o)
    return o


write_json(dst / "summary.json", scrub(out))
print("exported", dst, list(out["datasets"]))
