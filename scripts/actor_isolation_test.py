"""Actor isolation test for AgentCore Memory with IAM ABAC (session tags).

Setup (admin credentials): three actors write memories with unique tokens — ALICE (the attacker), BOB (victim),
and ALICE2 (an actor whose id has ALICE's id as a prefix). A role is created whose policy only allows access when
`bedrock-agentcore:actorId` / `namespace` / `namespacePath` matches `${aws:PrincipalTag/actorId}`. The backend
(admin) assumes that role with session tag actorId=ALICE and hands the credentials to "Alice"; Alice then tries every
way we know of to reach Bob's memory.

    python scripts/actor_isolation_test.py --run iso1        # writes runs/<run>/actor_isolation/results.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memeva.backends.agentcore import AgentCoreBackend  # noqa: E402
from memeva.common import ROOT, load_config, write_json  # noqa: E402

ROLE = "memeva-actor-scoped"


def policy_doc(arn: str) -> dict:
    tag = "${aws:PrincipalTag/actorId}"
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "OwnEventsOnly", "Effect": "Allow",
         "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:ListSessions"],
         "Resource": arn, "Condition": {"StringEquals": {"bedrock-agentcore:actorId": tag}}},
        {"Sid": "OwnRecordsByExactNamespace", "Effect": "Allow",
         "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:DeleteMemoryRecord",
                    "bedrock-agentcore:BatchDeleteMemoryRecords"],
         "Resource": arn, "Condition": {"StringLike": {"bedrock-agentcore:namespace": f"/u/{tag}/*"},
                                        "Null": {"bedrock-agentcore:namespacePath": "true"}}},
        {"Sid": "OwnRecordsByNamespacePath", "Effect": "Allow",
         "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
         "Resource": arn, "Condition": {"StringLike": {"bedrock-agentcore:namespacePath": f"/u/{tag}/*"},
                                        "Null": {"bedrock-agentcore:namespace": "true"}}},
        # --- hardening: explicit denies (each Deny on a negated operator is paired with Null:false so a
        #     missing key cannot accidentally trigger / bypass it) ---
        {"Sid": "DenyOtherActorEvents", "Effect": "Deny",
         "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:ListSessions"],
         "Resource": arn, "Condition": {"StringNotEquals": {"bedrock-agentcore:actorId": tag},
                                        "Null": {"bedrock-agentcore:actorId": "false"}}},
        {"Sid": "DenyOtherNamespace", "Effect": "Deny", "Action": "bedrock-agentcore:*", "Resource": arn,
         "Condition": {"StringNotLike": {"bedrock-agentcore:namespace": f"/u/{tag}/*"},
                       "Null": {"bedrock-agentcore:namespace": "false"}}},
        {"Sid": "DenyOtherNamespacePath", "Effect": "Deny", "Action": "bedrock-agentcore:*", "Resource": arn,
         "Condition": {"StringNotLike": {"bedrock-agentcore:namespacePath": f"/u/{tag}/*"},
                       "Null": {"bedrock-agentcore:namespacePath": "false"}}},
        # APIs without an actor-level condition key, write/admin APIs a user never needs, and the control plane
        {"Sid": "DenyNoActorScopeAndAdmin", "Effect": "Deny",
         "Action": ["bedrock-agentcore:ListActors", "bedrock-agentcore:ListMemoryExtractionJobs",
                    "bedrock-agentcore:StartMemoryExtractionJob", "bedrock-agentcore:BatchCreateMemoryRecords",
                    "bedrock-agentcore:BatchUpdateMemoryRecords", "bedrock-agentcore:UpdateMemory",
                    "bedrock-agentcore:DeleteMemory", "bedrock-agentcore:CreateMemory"], "Resource": "*"},
    ]}


def classify(fn):
    try:
        r = fn()
        return {"outcome": "allowed", "detail": r}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        return {"outcome": "denied" if code in ("AccessDeniedException", "AccessDenied") else "rejected" if code == "PerRecordRejected" else "error", "code": code,
                "detail": e.response["Error"].get("Message", "")[:180]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="iso1")
    ap.add_argument("--with-slash-actor", action="store_true",
                    help="also create actor '<alice>/x' to demonstrate why actorIds must not contain '/'")
    a = ap.parse_args()
    cfg = load_config()
    region = cfg["region"]
    be = AgentCoreBackend(cfg)
    mid = be.mid
    sts, iam = boto3.client("sts"), boto3.client("iam")
    me = sts.get_caller_identity()
    acct = me["Account"]
    caller = me["Arn"]
    if ":assumed-role/" in caller:
        caller = f"arn:aws:iam::{acct}:role/{caller.split(':assumed-role/')[1].split('/')[0]}"
    arn = f"arn:aws:bedrock-agentcore:{region}:{acct}:memory/{mid}"
    out_dir = ROOT / "runs" / a.run / "actor_isolation"
    A, B, A2, AX = f"{a.run}-alice", f"{a.run}-bob", f"{a.run}-alice2", f"{a.run}-alice/x"
    tokens = {A: "ALICE-7Q2Z", B: "BOB-K9X4", A2: "ALICE2-M3P8"}
    if a.with_slash_actor:
        tokens[AX] = "ALICEX-W5N1"

    def log(m):
        print(time.strftime("%H:%M:%S"), m, flush=True)

    # 1) seed memories (admin)
    for actor, tok in tokens.items():
        msgs = [{"role": "user", "content": f"帮我记一下，我的专属推荐码是 {tok}，下次填活动表要用。"},
                {"role": "assistant", "content": f"好的，已记下你的推荐码 {tok}。"},
                {"role": "user", "content": "另外我平时主要交易 BTC 现货。"},
                {"role": "assistant", "content": "收到，你主要交易 BTC 现货。"}]
        be.ingest_user({"user_id": actor, "sessions": [{"session_id": "s1", "date": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                                                       "messages": msgs}]}, {}, lambda: None)
    log("seeded; waiting for long-term records")
    t0 = time.time()
    while time.time() - t0 < 900:
        if all(any(tokens[x] in (r.get("content") or {}).get("text", "") for r in be._records(x)) for x in tokens):
            break
        time.sleep(15)
    recs = {x: be._records(x) for x in tokens}
    bob_rec = next(r for r in recs[B] if tokens[B] in (r.get("content") or {}).get("text", ""))
    bob_ns = bob_rec["namespaces"][0]
    bob_sessions = be.dp.list_sessions(memoryId=mid, actorId=B)["sessionSummaries"]
    bob_sid = bob_sessions[0]["sessionId"]
    bob_event = be.dp.list_events(memoryId=mid, actorId=B, sessionId=bob_sid, maxResults=1)["events"][0]["eventId"]
    summary_own = next((r["namespaces"][0] for r in recs[A] if r["namespaces"][0].startswith(f"/u/{A}/summary/")), None)
    own_rec = next(r for r in recs[A] if tokens[A] in (r.get("content") or {}).get("text", ""))
    log(f"records ready after {time.time() - t0:.0f}s; bob ns={bob_ns}")

    # 2) scoped role (only the backend principal may assume it, and it must set the actorId tag)
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": caller},
             "Action": ["sts:AssumeRole", "sts:TagSession"], "Condition": {"Null": {"aws:RequestTag/actorId": "false"}}}]}
    try:
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=[{"Key": "ManagedBy", "Value": "mem-eva"}])
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=ROLE, PolicyDocument=json.dumps(trust))
    iam.put_role_policy(RoleName=ROLE, PolicyName="own-actor-only", PolicyDocument=json.dumps(policy_doc(arn)))
    role_arn = f"arn:aws:iam::{acct}:role/{ROLE}"
    creds = None
    for _ in range(30):
        try:
            creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="alice-session", DurationSeconds=3600,
                                    Tags=[{"Key": "actorId", "Value": A}])["Credentials"]
            break
        except ClientError:
            time.sleep(5)
    sess = boto3.Session(aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"],
                         aws_session_token=creds["SessionToken"], region_name=region)
    dp = sess.client("bedrock-agentcore")
    # wait for IAM propagation using a call that must be allowed
    for _ in range(24):
        if classify(lambda: dp.retrieve_memory_records(memoryId=mid, namespace=f"/u/{A}/semantic/",
                                                       searchCriteria={"searchQuery": "推荐码", "topK": 3}))["outcome"] == "allowed":
            break
        time.sleep(5)

    def ret(**kw):
        r = dp.retrieve_memory_records(memoryId=mid, searchCriteria={"searchQuery": "推荐码是多少", "topK": 10}, **kw)
        txt = " ".join((x.get("content") or {}).get("text", "") for x in r.get("memoryRecordSummaries", []))
        return {"records": len(r.get("memoryRecordSummaries", [])), "tokens_seen": [t for t in tokens.values() if t in txt]}

    def lst(**kw):
        r = dp.list_memory_records(memoryId=mid, maxResults=20, **kw)
        txt = " ".join((x.get("content") or {}).get("text", "") for x in r.get("memoryRecordSummaries", []))
        return {"records": len(r.get("memoryRecordSummaries", [])), "tokens_seen": [t for t in tokens.values() if t in txt]}

    def get(**kw):
        r = dp.get_memory_record(memoryId=mid, memoryRecordId=bob_rec["memoryRecordId"], **kw)
        return {"tokens_seen": [t for t in tokens.values() if t in json.dumps(r, default=str, ensure_ascii=False)]}

    def batch(r):
        ok_n = len(r.get("successfulRecords", []))
        fails = [{"code": f.get("errorCode"), "msg": (f.get("errorMessage") or "")[:120]} for f in r.get("failedRecords", [])]
        if ok_n:   # at least one record actually written/deleted → treat as allowed
            return {"successful": ok_n, "failed": fails}
        raise ClientError({"Error": {"Code": "PerRecordRejected", "Message": json.dumps(fails, ensure_ascii=False)}}, "Batch")

    def mkevent(actor):
        r = dp.create_event(memoryId=mid, actorId=actor, sessionId="iso-attack", eventTimestamp=time.time(),
                            payload=[{"conversational": {"role": "USER", "content": {"text": "test write"}}}])
        return {"eventId": r["event"]["eventId"]}

    cases = [
        # (group, description, expected, fn)
        ("自己的数据", "精确 namespace 检索自己的语义记忆 /u/alice/semantic/", "allowed", lambda: ret(namespace=f"/u/{A}/semantic/")),
        ("自己的数据", "分层检索自己的全部记忆 namespacePath=/u/alice/", "allowed", lambda: ret(namespacePath=f"/u/{A}/")),
        ("自己的数据", "列出自己的记忆记录 namespacePath=/u/alice/", "allowed", lambda: lst(namespacePath=f"/u/{A}/")),
        ("自己的数据", "精确 namespace 检索自己的会话摘要", "allowed", lambda: ret(namespace=summary_own) if summary_own else {"skipped": True}),
        ("自己的数据", "以自己的 actorId 写入事件", "allowed", lambda: mkevent(A)),
        ("自己的数据", "列出自己的会话", "allowed", lambda: {"sessions": len(dp.list_sessions(memoryId=mid, actorId=A)["sessionSummaries"])}),
        ("读别人的长期记忆", "精确 namespace 检索 Bob 的记忆 /u/bob/semantic/", "denied", lambda: ret(namespace=f"/u/{B}/semantic/")),
        ("读别人的长期记忆", "分层检索 Bob 的全部记忆 namespacePath=/u/bob/", "denied", lambda: ret(namespacePath=f"/u/{B}/")),
        ("读别人的长期记忆", "列出 Bob 的记忆记录", "denied", lambda: lst(namespacePath=f"/u/{B}/")),
        ("读别人的长期记忆", "分层检索所有用户 namespacePath=/u/", "denied", lambda: ret(namespacePath="/u/")),
        ("读别人的长期记忆", "分层检索根路径 namespacePath=/", "denied", lambda: ret(namespacePath="/")),
        ("读别人的长期记忆", "同时传 namespace=/u/bob/semantic/ 与 namespacePath=/u/alice/（混淆参数）", "denied/error",
         lambda: ret(namespace=f"/u/{B}/semantic/", namespacePath=f"/u/{A}/")),
        ("读别人的长期记忆", "路径穿越 namespacePath=/u/alice/../bob/", "denied/error", lambda: ret(namespacePath=f"/u/{A}/../{B}/")),
        ("读别人的长期记忆", "前缀混淆：读 actorId 为 alice2 的记忆（alice 是它的前缀）", "denied", lambda: ret(namespacePath=f"/u/{A2}/")),
        ("按记录 ID 直接访问", "GetMemoryRecord 读 Bob 的记录，不传 namespace", "denied", lambda: get()),
        ("按记录 ID 直接访问", "GetMemoryRecord 读 Bob 的记录，谎报 namespace=/u/alice/semantic/", "denied/error",
         lambda: get(namespace=f"/u/{A}/semantic/")),
        ("按记录 ID 直接访问", "GetMemoryRecord 读 Bob 的记录，如实传 Bob 的 namespace", "denied", lambda: get(namespace=bob_ns)),
        ("改/删别人的数据", "DeleteMemoryRecord 删 Bob 的记录，谎报 namespace=/u/alice/semantic/", "denied/error",
         lambda: dp.delete_memory_record(memoryId=mid, memoryRecordId=bob_rec["memoryRecordId"], namespace=f"/u/{A}/semantic/")),
        ("改/删别人的数据", "BatchDeleteMemoryRecords 删 Bob 的记录，谎报 namespace", "denied/error/rejected",
         lambda: batch(dp.batch_delete_memory_records(memoryId=mid, records=[{"memoryRecordId": bob_rec["memoryRecordId"], "namespace": f"/u/{A}/semantic/"}]))),
        ("改/删别人的数据", "以 Bob 的 actorId 写入事件（冒充 Bob 写记忆）", "denied", lambda: mkevent(B)),
        ("读别人的原始对话", "列出 Bob 的会话 ListSessions", "denied", lambda: dp.list_sessions(memoryId=mid, actorId=B)),
        ("读别人的原始对话", "列出 Bob 的事件 ListEvents", "denied", lambda: dp.list_events(memoryId=mid, actorId=B, sessionId=bob_sid)),
        ("读别人的原始对话", "按事件 ID 读 Bob 的事件 GetEvent", "denied", lambda: dp.get_event(memoryId=mid, actorId=B, sessionId=bob_sid, eventId=bob_event)),
        ("枚举与提权", "ListActors 枚举所有 actorId", "denied", lambda: dp.list_actors(memoryId=mid)),
        ("枚举与提权", "ListMemoryExtractionJobs 查看 Bob 的抽取任务", "denied",
         lambda: dp.list_memory_extraction_jobs(memoryId=mid, filter={"actorId": B})),
        ("自己的数据", "GetMemoryRecord 读自己的记录，如实传自己的 namespace", "allowed",
         lambda: {"tokens_seen": [t for t in tokens.values() if t in json.dumps(dp.get_memory_record(memoryId=mid, memoryRecordId=own_rec["memoryRecordId"], namespace=own_rec["namespaces"][0]), default=str, ensure_ascii=False)]}),
        ("自己的数据", "GetMemoryRecord 读自己的记录，不传 namespace（看条件键是否由服务端回填）", "allowed/denied",
         lambda: {"tokens_seen": [t for t in tokens.values() if t in json.dumps(dp.get_memory_record(memoryId=mid, memoryRecordId=own_rec["memoryRecordId"]), default=str, ensure_ascii=False)]}),
        ("读别人的长期记忆", "namespacePath=/u/alice（缺少结尾斜杠）", "denied/error", lambda: ret(namespacePath=f"/u/{A}")),
        ("读别人的长期记忆", "namespacePath=/u/*/（字面星号）", "denied/error", lambda: ret(namespacePath="/u/*/")),
        ("改/删别人的数据", "BatchCreateMemoryRecords 往 Bob 的 namespace 写记录", "denied/error/rejected",
         lambda: batch(dp.batch_create_memory_records(memoryId=mid, records=[{"requestIdentifier": "x1", "namespaces": [f"/u/{B}/semantic/"],
                                                                         "content": {"text": "injected"}, "timestamp": time.time()}]))),
        ("枚举与提权", "StartMemoryExtractionJob 触发抽取任务", "denied/error",
         lambda: dp.start_memory_extraction_job(memoryId=mid, extractionJob={"jobId": "x"})),
        ("枚举与提权", "AssumeRole 时不带 actorId 标签（标签缺失）", "denied",
         lambda: sts.assume_role(RoleArn=role_arn, RoleSessionName="notag")),
        *([("风险演示", "actorId 含斜杠：读 actor “alice/x” 的记忆（会被 /u/alice/* 匹配）", "allowed/denied",
            lambda: ret(namespacePath=f"/u/{AX}/"))] if a.with_slash_actor else []),
        ("枚举与提权", "用 Alice 的凭证重新 AssumeRole，并把标签改成 actorId=bob", "denied",
         lambda: sess.client("sts").assume_role(RoleArn=role_arn, RoleSessionName="retag-attempt", Tags=[{"Key": "actorId", "Value": B}])),
    ]
    results = []
    try:
        _run_cases(cases, results, classify, tokens, A, B, A2, AX, log)
    finally:
        try:
            iam.delete_role_policy(RoleName=ROLE, PolicyName="own-actor-only")
            iam.delete_role(RoleName=ROLE)
        except Exception as e:  # noqa: BLE001
            log(f"role cleanup: {e}")
    still = any(x["memoryRecordId"] == bob_rec["memoryRecordId"] for x in be._records(B))
    write_json(out_dir / "results.json", {
        "run": a.run, "memory": "<memory-id>", "actors": {"attacker": A, "victim": B, "prefix_actor": A2, "slash_actor": AX}, "tokens": tokens,
        "policy": json.loads(json.dumps(policy_doc("arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>"))),
        "trust_condition": trust["Statement"][0]["Condition"], "cases": results,
        "bob_record_intact_after_test": still, "passed": sum(r["pass"] for r in results), "total": len(results)})
    log(f"passed {sum(r['pass'] for r in results)}/{len(results)}; bob record intact={still}")


def _run_cases(cases, results, classify, tokens, A, B, A2, AX, log):
    for group, desc, expected, fn in cases:
        r = classify(fn)
        if r["outcome"] == "allowed":
            d = r["detail"] if isinstance(r["detail"], dict) else {}
            r["detail"] = {k: v for k, v in d.items() if k in ("records", "tokens_seen", "sessions", "skipped", "eventId", "successful", "failed")} if d else "ok"
        leaked = r["outcome"] == "allowed" and isinstance(r["detail"], dict) and any(
            t in r["detail"].get("tokens_seen", []) for t in [v for k, v in tokens.items() if k != A])
        ok = (r["outcome"] in expected.split("/")) and (not leaked or group == "风险演示")
        results.append({"group": group, "case": desc, "expected": expected, **r, "leaked_other_users_data": leaked, "pass": ok})
        log(f"{'PASS' if ok else 'FAIL'} [{r['outcome']}] {desc} {r.get('code', '')} {r.get('detail') if r['outcome'] == 'allowed' else ''}")



if __name__ == "__main__":
    main()
