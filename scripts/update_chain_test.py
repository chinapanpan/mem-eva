"""Update-chain test: one fact (withdrawal whitelist address) is changed N times for a single actor.
After every change we wait until the new value is visible in long-term memory (or a timeout), then ask
"what is my current whitelist address?" and judge the answer.

    python scripts/update_chain_test.py --run chain1 --steps 10 --systems agentcore,tdam

Outputs runs/<run>/update_chain/results.json (all steps, both systems) — feed it to build_update_chain_html.py.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memeva.backends import make  # noqa: E402
from memeva.common import ROOT, Bedrock, Journal, load_config, pack_context, read_json, write_json  # noqa: E402
from memeva.judge import ANSWER_SYSTEM, answer_prompt, judge_prompt, parse_yes  # noqa: E402

QUESTION = "我现在的提币白名单地址是哪个？"
FILLERS = [
    ("USDT 走 TRC20 和 ERC20 提币，手续费差多少？", "一般 TRC20 手续费更低，ERC20 受 Gas 影响波动较大，以提币页面实时显示为准。"),
    ("提币一般多久能到账？", "大多数网络在确认数达到要求后几分钟内到账，拥堵时会更久。"),
    ("现货网格的区间怎么设比较合理？", "可以参考近 30 天的波动区间，上下各留一些缓冲。"),
    ("两步验证换成验证器 App 要注意什么？", "先离线保存好备份密钥，确认能正常生成动态码后再关闭短信验证。"),
    ("防钓鱼码是做什么用的？", "官方邮件会带上你设置的防钓鱼码，没有或不一致的邮件很可能是钓鱼邮件。"),
    ("合约的资金费率多久结算一次？", "主流永续合约一般每 8 小时结算一次，具体以合约规则为准。"),
    ("子账户之间划转要手续费吗？", "同一主账户下的子账户内部划转通常不收手续费。"),
    ("理财产品提前赎回会损失收益吗？", "活期一般可以随时赎回；定期提前赎回可能损失部分或全部收益。"),
    ("P2P 买币要注意什么？", "只和高完成率的商家交易，确认收款后再放币，不要线下私聊转账。"),
    ("ETH 链上转账什么时候 Gas 便宜？", "一般在欧美夜间、周末链上不拥堵时 Gas 较低。"),
]
FOLLOWUPS = [("好的，明白了，谢谢。", "不客气，有问题随时问我。"),
             ("那就先这样。", "好的，有需要随时找我。")]


def addresses(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    out: list[str] = []
    while len(out) < n:
        a = "0x" + "".join(rng.choice("0123456789ABCDEF") for _ in range(4)) + "".join(rng.choice("0123456789abcdef") for _ in range(4))
        if a not in out:
            out.append(a)
    return out


def step_messages(i: int, addr: str, prev: str | None) -> list[dict]:
    fq, fa = FILLERS[i % len(FILLERS)]
    if prev is None:
        u = f"对了，帮我记一下，我的提币白名单地址是 {addr}，以后提币都用这个。"
        a = f"好的，已记录：你的提币白名单地址是 {addr}。"
    else:
        u = f"对了，我的提币白名单地址刚换成了 {addr}，之前那个 {prev} 不用了。"
        a = f"好的，已更新：你当前的提币白名单地址是 {addr}，旧地址 {prev} 不再使用。"
    gq, ga = FOLLOWUPS[i % len(FOLLOWUPS)]
    return [{"role": "user", "content": fq}, {"role": "assistant", "content": fa},
            {"role": "user", "content": u}, {"role": "assistant", "content": a},
            {"role": "user", "content": gq}, {"role": "assistant", "content": ga}]


def context_of(be, uid: str) -> tuple[str, float]:
    r = be.retrieve(uid, QUESTION)
    blocks = r.preamble + [x.render() for x in r.records]
    ctx, _ = pack_context(blocks, 4096)
    return ctx, r.seconds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="chain1")
    ap.add_argument("--steps", type=int, default=10)
    ap.add_argument("--systems", default="agentcore,tdam")
    ap.add_argument("--max-wait", type=int, default=1200, help="seconds to wait per step for the new value to appear")
    ap.add_argument("--poll", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261120)
    ap.add_argument("--turn-gap", type=int, default=20, help="seconds between turns within a round (realistic chat pacing)")
    a = ap.parse_args()
    cfg = load_config()
    cfg["tdam"]["service_id"] = f"{cfg['tdam']['service_id']}-{a.run}"
    out_dir = ROOT / "runs" / a.run / "update_chain"
    out_path = out_dir / "results.json"
    llm = Bedrock(cfg["region"], Journal(out_dir / "usage.jsonl"))
    arms = {"agentcore": ["agentcore_spu"], "tdam": ["tdam_full", "tdam_l1"]}
    systems = [s for s in a.systems.split(",") if s]
    bes = {arm: make(arm, cfg) for s in systems for arm in arms[s]}
    store_be = {s: bes[arms[s][0]] for s in systems}
    uid = f"{a.run}-chain-user"
    addrs = addresses(a.steps, a.seed)
    state = read_json(out_path) if out_path.exists() else {
        "run": a.run, "actor_id": uid, "question": QUESTION, "addresses": addrs, "steps": [],
        "config": {"answer_model": cfg["answer_model"], "judge_model": cfg["judge_model"], "max_wait_s": a.max_wait,
                   "poll_s": a.poll, "turn_gap_s": a.turn_gap, "systems": systems}}

    def log(m):
        print(time.strftime("%H:%M:%S"), m, flush=True)

    for i in range(len(state["steps"]), a.steps):
        addr, prev = addrs[i], (addrs[i - 1] if i else None)
        msgs = step_messages(i, addr, prev)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        session = {"session_id": f"chain-s{i + 1}", "date": now, "messages": msgs}
        step = {"step": i + 1, "address": addr, "previous": prev, "fact_utterance": msgs[2]["content"],
                "messages": msgs, "written_at": now, "systems": {}}
        # 1) write to every system, one user→assistant turn at a time with realistic gaps
        #    (bursting all turns within a second triggers a TDAM warmup race that skips the fact turn)
        for s in systems:
            step["systems"][s] = {"write_seconds": 0.0}
        turns = [msgs[k:k + 2] for k in range(0, len(msgs), 2)]
        for ti, turn in enumerate(turns):
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            for s in systems:
                t0 = time.time()
                store_be[s].ingest_user({"user_id": uid, "sessions": [{"session_id": session["session_id"], "date": ts,
                                                                      "messages": turn}]}, {}, lambda: None)
                step["systems"][s]["write_seconds"] += round(time.time() - t0, 3)
            if ti < len(turns) - 1:
                time.sleep(a.turn_gap)
        t_written = time.time()
        log(f"step {i + 1}: wrote {addr} (prev {prev})")

        # 2) wait until the new value is visible in long-term memory (per system, in parallel)
        def wait(s):
            be = store_be[s]
            while time.time() - t_written < a.max_wait:
                ctx, _ = context_of(be, uid)
                if addr in ctx:
                    return round(time.time() - t_written, 1)
                time.sleep(a.poll)
            return None

        with ThreadPoolExecutor(len(systems)) as ex:
            ready = dict(zip(systems, ex.map(wait, systems)))
        for s in systems:
            step["systems"][s]["effective_seconds"] = ready[s]
        log(f"step {i + 1}: effective {ready}")

        # 3) ask + judge (every arm of every system)
        q = {"category": "update", "question": QUESTION, "answer": addr}
        for s in systems:
            for arm in arms[s]:
                ctx, secs = context_of(bes[arm], uid)
                ans = llm.chat(cfg["answer_model"], answer_prompt(ctx, QUESTION, datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                               system=ANSWER_SYSTEM, max_tokens=2000, stage="answer", arm=arm, step=i + 1)
                v = llm.chat(cfg["judge_model"], judge_prompt(q, ans), max_tokens=400, stage="judge", arm=arm, step=i + 1)
                step["systems"][s][arm] = {
                    "answer": ans, "judge": v, "correct": parse_yes(v),
                    "answer_mentions_current": addr in ans,
                    "answer_mentions_old": [x for x in addrs[:i] if x in ans],
                    "context_has_current": addr in ctx,
                    "context_old_values": [x for x in addrs[:i] if x in ctx],
                    "context_chars": len(ctx), "retrieval_seconds": round(secs, 3), "context": ctx}
                log(f"step {i + 1} [{arm}] correct={step['systems'][s][arm]['correct']} ans={ans[:60]!r}")
        state["steps"].append(step)
        write_json(out_path, state)

    # final snapshot: what long-term memory holds about the address for each system
    for s in systems:
        texts = store_be[s].dump(uid)
        state.setdefault("final_memory", {})[s] = [t for t in texts if any(x in t for x in addrs)][:60]
    write_json(out_path, state)
    log(f"done → {out_path}")


if __name__ == "__main__":
    main()
