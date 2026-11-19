"""Aggregate LiteLLM usage rows (TDAM internal LLM/embedding calls) between two unix timestamps.
Prints aggregated rows in the same JSONL schema the report reader expects."""
import json
import sys
from collections import defaultdict

path, since, until = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else 9e12
agg = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
for line in open(path):
    r = json.loads(line)
    if since <= r["ts"] <= until:
        a = agg[r.get("model")]
        a["prompt_tokens"] += r.get("prompt_tokens") or 0
        a["completion_tokens"] += r.get("completion_tokens") or 0
        a["calls"] += 1
for m, a in agg.items():
    print(json.dumps({"model": m, **a}))
