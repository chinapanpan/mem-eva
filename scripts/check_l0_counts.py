import json, sys
sys.path.insert(0, ".")
from memeva.common import load_config
from memeva.pipeline import Run
run = Run(load_config(), "final", "web3_zh"); be = run.backend("tdam_l1")
bad = []
for u in run.data()["users"]:
    exp = sum(len(s["messages"]) for s in u["sessions"])
    got = be.post("/v3/conversation/count", be._iso(run.ukey(u["user_id"]))).get("total")
    if got != exp: bad.append((u["user_id"], exp, got))
print("mismatch", bad)
