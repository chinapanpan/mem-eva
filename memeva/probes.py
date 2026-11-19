"""Non-QA probes: isolation, secret leakage, deletion, ingest→retrievable lag, latency under concurrency,
identity spoofing, failover. All results are written under runs/<run>/<dataset>/probes/."""
from __future__ import annotations

import json
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
import httpx

from .common import digest, write_json
from .pipeline import STORE_ARM, Run, log
from .stats import pct


def _texts(r) -> str:
    return "\n".join(r.preamble + [x.content for x in r.records])


# ── D6a cross-tenant leakage (correctly configured callers) ──
def isolation(run: Run, stores: list[str], others: int = 3) -> dict:
    users = run.data()["users"]
    rng = random.Random(7)
    out = {}
    for store in stores:
        be = run.backend(STORE_ARM[store])
        probes, leaks = 0, []
        jobs = []
        for u in users:
            toks = u.get("probes", {}).get("unique_tokens", [])
            if not toks:
                continue
            pool = [x for x in users if x["user_id"] != u["user_id"]]
            for v in rng.sample(pool, min(others, len(pool))):
                jobs.append((u, v, toks))

        def one(j):
            u, v, toks = j
            r = be.retrieve(run.ukey(v["user_id"]), f"我的提币白名单地址是 {toks[1]} 吗？我的宠物叫 {toks[2]}，之前地址 {toks[0]}")
            text = _texts(r)
            return [(u["user_id"], v["user_id"], t) for t in toks if t in text]

        with ThreadPoolExecutor(8) as ex:
            for hit in ex.map(one, jobs):
                probes += 1
                leaks += hit
        out[store] = {"probes": probes, "leaking_probes": len({(a, b) for a, b, _ in leaks}), "leaks": leaks}
        log(f"[isolation] {store}: {out[store]['leaking_probes']}/{probes} leaking")
    write_json(run.dir / "probes" / "isolation.json", out)
    return out


# ── D10a secrets persisted into long-term memory ──
def secrets(run: Run, stores: list[str]) -> dict:
    users = [u for u in run.data()["users"] if u.get("probes", {}).get("secrets")]
    out = {}
    for store in stores:
        be = run.backend(STORE_ARM[store])

        def one(u):
            texts = "\n".join(be.dump(run.ukey(u["user_id"])))
            sec = u["probes"]["secrets"][0]
            return {"user": u["user_id"], "exact": sec in texts, "partial": sec[-24:-12] in texts,
                    "mentions_secret_event": any(k in texts for k in ("API secret", "密钥", "secret"))}

        with ThreadPoolExecutor(8) as ex:
            rows = list(ex.map(one, users))
        out[store] = {"users": len(rows), "exact_secret_stored": sum(r["exact"] for r in rows),
                      "partial_secret_stored": sum(r["partial"] for r in rows), "rows": rows}
        log(f"[secrets] {store}: exact={out[store]['exact_secret_stored']}/{len(rows)}")
    write_json(run.dir / "probes" / "secrets.json", out)
    return out


# ── D7 deletion / right-to-be-forgotten ──
def deletion(run: Run, stores: list[str], n: int = 5, checks=(0, 30, 120)) -> dict:
    users = [u for u in run.data()["users"] if u.get("probes", {}).get("unique_tokens")][-n:]
    out = {}
    for store in stores:
        be = run.backend(STORE_ARM[store])
        rows = []
        for u in users:
            uk, toks = run.ukey(u["user_id"]), u["probes"]["unique_tokens"]
            before = be.counts(uk)
            t0 = time.time()
            res = be.delete_user(uk)
            api_s = time.time() - t0
            seen = []
            for c in checks:
                while time.time() - t0 < c:
                    time.sleep(1)
                r = be.retrieve(uk, f"我的提币白名单地址是什么？宠物叫什么？{toks[2]}")
                dump = "\n".join(be.dump(uk))
                seen.append({"t": c, "retrieved_records": len(r.records) + len(r.preamble),
                             "token_in_retrieval": any(t in _texts(r) for t in toks),
                             "token_in_storage": any(t in dump for t in toks), "counts": be.counts(uk)})
            rows.append({"user": u["user_id"], "before": before, "delete_result": res, "delete_api_seconds": api_s, "checks": seen})
            log(f"[deletion] {store} {u['user_id']}: {seen[-1]}")
        out[store] = rows
    write_json(run.dir / "probes" / "deletion.json", out)
    return out


# ── D4 ingest → retrievable lag ──
LAG_TURNS = [
    ("你好，我想了解一下现货网格机器人。", "好的，现货网格会在设定区间内低买高卖，需要设置上下限和网格数量。"),
    ("区间一般怎么选？", "可以参考近 30 天的波动区间，并留出一定缓冲。"),
    ("对了，我的专属邀请码是 {code}，帮我记一下，下次填活动表要用。", "好的，已记下你的邀请码 {code}。"),
    ("网格数量多一点好还是少一点好？", "网格越多单格利润越薄、成交越频繁，需要权衡手续费。"),
    ("那手续费怎么算？", "现货按成交额收取 maker/taker 手续费，VIP 等级越高费率越低。"),
    ("明白了，谢谢。", "不客气，有问题随时问我。"),
]


def lag(run: Run, stores: list[str], n: int = 6, max_seconds: int = 1800, poll: int = 10) -> dict:
    out = {}

    def one(store, i):
        be = run.backend(STORE_ARM[store])
        code = "INV-" + digest([run.run, store, i], 6).upper()
        uid = f"{run.run}-lag-{store}-{i}"
        msgs = []
        for u, a in LAG_TURNS:
            msgs += [{"role": "user", "content": u.format(code=code)}, {"role": "assistant", "content": a.format(code=code)}]
        user = {"user_id": uid, "sessions": [{"session_id": "s0", "date": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                                                "messages": msgs}]}
        t0 = time.time()
        be.ingest_user(user, {}, lambda: None)
        t_written = time.time()
        while time.time() - t_written < max_seconds:
            r = be.retrieve(uid, "我的邀请码是多少？")
            if code in _texts(r):
                return {"i": i, "write_seconds": t_written - t0, "ready_seconds": time.time() - t_written}
            time.sleep(poll)
        return {"i": i, "write_seconds": t_written - t0, "ready_seconds": None}

    with ThreadPoolExecutor(len(stores) * n) as ex:
        futs = {(s, i): ex.submit(one, s, i) for s in stores for i in range(n)}
        for s in stores:
            rows = [futs[(s, i)].result() for i in range(n)]
            ok = [r["ready_seconds"] for r in rows if r["ready_seconds"] is not None]
            out[s] = {"rows": rows, "ready": len(ok), "n": n, "p50": pct(ok, .5), "p95": pct(ok, .95), "max": max(ok) if ok else None}
            log(f"[lag] {s}: {out[s]['ready']}/{n} p50={out[s]['p50']}")
    write_json(run.dir / "probes" / "lag.json", out)
    return out


# ── D5 retrieval latency under concurrency ──
def perf(run: Run, arms: list[str]) -> dict:
    p = run.cfg["perf"]
    data = run.data()
    pairs = [(run.ukey(u["user_id"]), q["question"]) for u in data["users"] for q in u["questions"]]
    out = {}
    for arm in arms:
        be = run.backend(arm)
        for uk, q in pairs[: int(p["warmup_requests"])]:
            be.retrieve(uk, q)
        out[arm] = {}
        for c in p["concurrency"]:
            lat, errs, stop = [], [], time.time() + float(p["seconds_per_level"])
            lock = threading.Lock()

            def worker(seed):
                rng = random.Random(seed)
                while time.time() < stop:
                    uk, q = rng.choice(pairs)
                    t0 = time.monotonic()
                    try:
                        be.retrieve(uk, q)
                        with lock:
                            lat.append(time.monotonic() - t0)
                    except Exception as e:  # noqa: BLE001
                        with lock:
                            errs.append(str(e)[:200])

            t_start = time.time()
            ths = [threading.Thread(target=worker, args=(i,)) for i in range(c)]
            [t.start() for t in ths]
            [t.join() for t in ths]
            wall = time.time() - t_start
            out[arm][str(c)] = {"requests": len(lat), "errors": len(errs), "error_samples": errs[:5],
                                "throughput_rps": len(lat) / wall, "p50": pct(lat, .5), "p95": pct(lat, .95),
                                "p99": pct(lat, .99), "max": max(lat) if lat else None}
            log(f"[perf] {arm} c={c}: {out[arm][str(c)]}")
    write_json(run.dir / "probes" / "perf.json", out)
    return out


# ── D6b identity spoofing: can a caller read another tenant by changing an ID? ──
def spoof(run: Run) -> dict:
    users = run.data()["users"]
    a, b = run.ukey(users[0]["user_id"]), run.ukey(users[1]["user_id"])
    out = {}
    # TDAM: the shared gateway key authorises every user_id; data plane does not bind caller identity to user_id.
    tb = run.backend("tdam_l1")
    r = tb.retrieve(b, "我的提币白名单地址是什么？")
    out["tdam"] = {"caller": "client holding gateway Bearer key (intended for tenant A)",
                   "request": f"atomic/search with user_id={b}", "records_returned": len(r.records),
                   "cross_tenant_read_possible": len(r.records) > 0}
    # AgentCore: IAM role scoped to tenant A's namespace via bedrock-agentcore:namespace condition.
    ab = run.backend("agentcore_spu")
    out["agentcore"] = _agentcore_scoped_role_test(run.cfg["region"], ab.mid, a, b)
    write_json(run.dir / "probes" / "spoof.json", out)
    log(f"[spoof] {json.dumps(out, ensure_ascii=False)[:400]}")
    return out


def _agentcore_scoped_role_test(region: str, memory_id: str, actor_a: str, actor_b: str) -> dict:
    sts, iam = boto3.client("sts"), boto3.client("iam")
    me = sts.get_caller_identity()
    acct = me["Account"]
    caller = me["Arn"]
    if ":assumed-role/" in caller:
        r = caller.split(":assumed-role/")[1].split("/")[0]
        caller = f"arn:aws:iam::{acct}:role/{r}"
    name = "memeva-tenant-scope-probe"
    arn = f"arn:aws:bedrock-agentcore:{region}:{acct}:memory/{memory_id}"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": caller}, "Action": "sts:AssumeRole"}]}
    policy = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
        "Resource": arn, "Condition": {"StringLike": {"bedrock-agentcore:namespace": f"/u/{actor_a}/*"}}}]}
    try:
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                        Tags=[{"Key": "ManagedBy", "Value": "mem-eva"}])
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
    iam.put_role_policy(RoleName=name, PolicyName="tenant-a-only", PolicyDocument=json.dumps(policy))
    role_arn = f"arn:aws:iam::{acct}:role/{name}"
    creds = None
    for _ in range(20):  # IAM propagation
        try:
            creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="tenant-a")["Credentials"]
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    dp = boto3.client("bedrock-agentcore", region_name=region, aws_access_key_id=creds["AccessKeyId"],
                      aws_secret_access_key=creds["SecretAccessKey"], aws_session_token=creds["SessionToken"])

    def attempt(actor, style):
        kw = {"namespace": f"/u/{actor}/semantic/"} if style == "namespace" else {"namespacePath": f"/u/{actor}/semantic/"}
        last = {}
        for _ in range(12):
            try:
                r = dp.retrieve_memory_records(memoryId=memory_id, searchCriteria={"searchQuery": "提币白名单地址", "topK": 5}, **kw)
                return {"allowed": True, "records": len(r.get("memoryRecordSummaries", []))}
            except dp.exceptions.AccessDeniedException as e:
                last = {"allowed": False, "error": "AccessDeniedException", "message": str(e)[-160:]}
                if actor != actor_a:
                    return last
                time.sleep(10)  # own tenant: allow IAM propagation
        return last

    res = {style: {"own_tenant": attempt(actor_a, style), "other_tenant": attempt(actor_b, style)}
           for style in ("namespace", "namespacePath")}
    iam.delete_role_policy(RoleName=name, PolicyName="tenant-a-only")
    iam.delete_role(RoleName=name)
    return {"policy_condition": policy["Statement"][0]["Condition"], **res,
            "cross_tenant_read_possible": any(v["other_tenant"].get("allowed") for v in res.values()),
            "own_tenant_allowed": {k: v["own_tenant"].get("allowed") for k, v in res.items()}}


# ── D8 failover (self-hosted only) ──
def failover(run: Run, kill_cmd: str, seconds: int = 120, kill_at: int = 20, concurrency: int = 4) -> dict:
    be = run.backend("tdam_l1")
    data = run.data()
    pairs = [(run.ukey(u["user_id"]), q["question"]) for u in data["users"] for q in u["questions"]]
    before = sum(sum(be.counts(run.ukey(u["user_id"])).values()) for u in data["users"])
    events, lock, stop = [], threading.Lock(), time.time() + seconds
    t_start = time.time()

    def worker(seed):
        rng = random.Random(seed)
        while time.time() < stop:
            uk, q = rng.choice(pairs)
            t0 = time.time()
            try:
                be.retrieve(uk, q)
                ok = True
            except Exception:  # noqa: BLE001
                ok = False
                time.sleep(0.5)
            with lock:
                events.append((t0 - t_start, ok, time.time() - t0))

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    [t.start() for t in ths]
    time.sleep(kill_at)
    kill_t = time.time() - t_start
    proc = subprocess.run(kill_cmd, shell=True, capture_output=True, text=True, timeout=300)
    [t.join() for t in ths]
    fails = [e for e in events if not e[1]]
    after = sum(sum(be.counts(run.ukey(u["user_id"])).values()) for u in data["users"])
    first_fail = min((e[0] for e in fails), default=None)
    last_fail = max((e[0] for e in fails), default=None)
    out = {"kill_cmd": kill_cmd, "kill_at_s": kill_t, "kill_rc": proc.returncode, "requests": len(events),
           "failed": len(fails), "outage_window_s": (last_fail - first_fail) if fails else 0.0,
           "records_before": before, "records_after": after}
    write_json(run.dir / "probes" / "failover.json", out)
    log(f"[failover] {out}")
    return out


def tdam_unauth_check(url: str) -> dict:
    """Does the TDAM gateway accept data-plane calls without the Bearer key?"""
    r = httpx.post(url + "/v3/atomic/search", json={"query": "x"}, headers={"x-tdai-service-id": "probe"}, timeout=10)
    return {"status": r.status_code, "body": r.text[:200]}


# ── D6c TDAM team-shared scope: do L2/L3 (persona/scenes) mix users of one team+agent? ──
TEAM_FACTS = [
    ("我的冷钱包是 Ledger，备注名叫「{tok}」，别告诉别人。", "好的，已记住你的冷钱包备注名 {tok}。"),
    ("我比较保守，合约杠杆最多 2 倍。", "明白，杠杆上限 2 倍。"),
    ("我主要交易 BTC 和 ETH，不碰山寨币。", "收到，只关注 BTC/ETH。"),
    ("我是做量化的，平时用 API 下单。", "了解，你主要通过 API 交易。"),
    ("下个月我打算把一部分 USDT 转去链上理财。", "好的，需要时可以帮你比较链上理财方案。"),
    ("谢谢，今天就到这里。", "不客气，随时找我。"),
]


def tdam_team_scope(run: Run, team_id: str = "shared-team", n_users: int = 3, max_wait: int = 2400) -> dict:
    from .backends.tdam import TdamBackend
    cfg = dict(run.cfg)
    cfg["tdam"] = dict(run.cfg["tdam"], team_id=f"{team_id}-{run.run}")
    be = TdamBackend(cfg, mode="full")
    users = []
    for i in range(n_users):
        tok = "VAULT-" + digest([run.run, "team", i], 6).upper()
        uid = f"{run.run}-team-{i}"
        msgs = []
        for u, a in TEAM_FACTS:
            msgs += [{"role": "user", "content": u.format(tok=tok)}, {"role": "assistant", "content": a.format(tok=tok)}]
        be.ingest_user({"user_id": uid, "sessions": [{"session_id": "s0", "date": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                                                      "messages": msgs}]}, {}, lambda: None)
        users.append((uid, tok))
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if all(be.counts(u)["l3"] for u, _ in users) and time.time() - t0 > 900:
            break
        time.sleep(30)
    rows = []
    for uid, tok in users:
        r = be.retrieve(uid, "我的冷钱包备注名是什么？")
        pre = "\n".join(r.preamble)
        l1 = "\n".join(x.content for x in r.records)
        others = [t for u, t in users if u != uid]
        rows.append({"user": uid, "own_token_in_l1": tok in l1, "own_token_in_profile": tok in pre,
                     "other_tokens_in_profile": [t for t in others if t in pre],
                     "other_tokens_in_l1": [t for t in others if t in l1]})
    out = {"team_id": cfg["tdam"]["team_id"], "waited_s": time.time() - t0, "rows": rows,
           "users_seeing_other_users_profile_data": sum(bool(r["other_tokens_in_profile"]) for r in rows)}
    write_json(run.dir / "probes" / "tdam_team_scope.json", out)
    log(f"[tdam_team_scope] {out}")
    return out
