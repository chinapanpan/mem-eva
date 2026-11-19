"""How many distinct L3/L2 preambles did TDAM inject across users? (identical preamble for different users
= the persona/scenes were built from a shared, multi-user profile)."""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

run_dir = Path(sys.argv[1])
for arm in ("tdam_full", "tdam_shared_full"):
    per_user = {}
    for p in sorted((run_dir / "results" / arm).glob("*.json")):
        r = json.loads(p.read_text())
        ctx = r["context"]
        pre = ctx.split("[L1:")[0] if "<user-persona>" in ctx or "<scene-navigation>" in ctx else ""
        per_user.setdefault(r["user_id"], set()).add(hashlib.sha1(pre.encode()).hexdigest()[:10] if pre else "-")
    hashes = Counter(h for hs in per_user.values() for h in hs if h != "-")
    users_with_pre = sum(1 for hs in per_user.values() if hs - {"-"})
    shared = sum(1 for hs in per_user.values() if any(hashes[h] > 1 and len([u for u, x in per_user.items() if h in x]) > 1 for h in hs))
    print(json.dumps({"arm": arm, "users": len(per_user), "users_with_persona_or_scenes": users_with_pre,
                      "distinct_preambles": len(hashes), "users_sharing_a_preamble_with_another_user": shared}))
