"""Re-answer + re-judge every saved round of an update-chain run from its stored retrieval contexts
(no new writes / retrievals). Used after raising the answer token cap.
    python scripts/update_chain_reanswer.py chain2"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memeva.common import ROOT, Bedrock, Journal, load_config, write_json  # noqa: E402
from memeva.judge import ANSWER_SYSTEM, answer_prompt, judge_prompt, parse_yes  # noqa: E402

run = sys.argv[1]
p = ROOT / "runs" / run / "update_chain" / "results.json"
S = json.loads(p.read_text())
cfg = load_config()
llm = Bedrock(cfg["region"], Journal(p.parent / "usage_reanswer.jsonl"))
for st in S["steps"]:
    q = {"category": "update", "question": S["question"], "answer": st["address"]}
    olds = S["addresses"][: st["step"] - 1]
    for s, arms in st["systems"].items():
        for arm, r in arms.items():
            if not isinstance(r, dict) or "context" not in r:
                continue
            day = st["written_at"][:10]
            ans = llm.chat(cfg["answer_model"], answer_prompt(r["context"], S["question"], day), system=ANSWER_SYSTEM,
                           max_tokens=2000, stage="answer", arm=arm, step=st["step"])
            v = llm.chat(cfg["judge_model"], judge_prompt(q, ans), max_tokens=400, stage="judge", arm=arm, step=st["step"])
            r.setdefault("first_attempt", {"answer": r["answer"], "correct": r["correct"], "note": "answer max_tokens=400 (truncated)"})
            r.update(answer=ans, judge=v, correct=parse_yes(v), answer_mentions_current=st["address"] in ans,
                     answer_mentions_old=[x for x in olds if x in ans])
            print(st["step"], arm, r["correct"], ans[:70].replace("\n", " "), flush=True)
S["config"]["answer_max_tokens"] = 2000
write_json(p, S)
