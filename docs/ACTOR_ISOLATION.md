# 方案分析：让用户只能访问自己 actorId 下的记忆

**结论先说：** 在 AgentCore Memory 上，可以做到“即使知道别人的 actorId，也读不到、写不进、删不掉别人的记忆”，而且由 AWS 在服务端强制。做法是 IAM ABAC：给每个用户签发带 `actorId` 标签的短期凭证，策略里用 `${aws:PrincipalTag/actorId}` 约束 `actorId`、`namespace`、`namespacePath` 三个条件键。我们实测了 33 种访问方式（25 种越权尝试 + 8 种访问自己数据的操作），在 actorId 为不透明 ID 时全部符合预期：25 种越权尝试全部被拒。

有三个前提，缺一个都会留下口子：
1. **actorId 必须是不透明 ID，不能含 `/`**。AgentCore 允许 actorId 含 `/`，实测中 actor `alice/x` 的记忆会被 `alice` 的凭证读到。
2. **标签必须从已验签的用户身份派生**，不能由业务代码随手填。否则“后端传错 actorId”的 bug 会原样变成“标签打错”。
3. **Agent 场景要走 Gateway**：AgentCore Runtime 里所有用户共用一个执行角色，Agent 自己拿着全量权限；这种场景要在 Gateway 层按用户 JWT 做校验。

测试代码：[`scripts/actor_isolation_test.py`](../scripts/actor_isolation_test.py) · 结果：`runs/iso3/actor_isolation/results.json`（不透明 ID）、`runs/iso2/...`（含斜杠 actorId 的风险演示）

---

## 1. 要防的是什么

助手后端为每个 C 端用户维护一个 `actorId`，长期记忆放在 `/u/{actorId}/semantic/`、`/u/{actorId}/preference/`、`/u/{actorId}/summary/{sessionId}/` 下。需要挡住：

| # | 威胁 | 例子 |
|---|---|---|
| T1 | 客户端篡改参数 | 前端请求里带了 `actorId=bob`，后端直接透传 |
| T2 | 后端代码 bug | 缓存串号、并发变量被覆盖，用 Alice 的会话查了 Bob 的记忆 |
| T3 | 提示注入 | 用户让 Agent“帮我查一下 actor bob 的记忆”，Agent 的工具调用拼出了 Bob 的 ID |
| T4 | 枚举 | 调 `ListActors` 拿到所有 actorId，再逐个读 |
| T5 | 前缀 / 路径混淆 | `alice` 读到 `alice2`、`alice/x` 的 namespace；或者用 `/u/`、`/` 这种上层路径一次查所有人 |
| T6 | 按记录 ID 直接访问 | 拿到 Bob 的记录 ID，再调 `GetMemoryRecord` / `DeleteMemoryRecord`，并谎报 namespace |

## 2. AgentCore 提供的授权能力

这是决定方案可行性的关键：各个 API 支持哪些 IAM 条件键（来自 AWS 服务授权参考）。

| API | 可用的条件键 |
|---|---|
| `CreateEvent` / `GetEvent` / `ListEvents` / `DeleteEvent` | `bedrock-agentcore:actorId`、`sessionId` |
| `ListSessions` | `actorId` |
| `RetrieveMemoryRecords` / `ListMemoryRecords` | `namespace`、`namespacePath`、`strategyId` |
| `GetMemoryRecord` / `DeleteMemoryRecord` / `BatchCreate` / `BatchDelete` | `namespace` |
| `ListActors` / `ListMemoryExtractionJobs` | **没有** actor 级条件键（只能整体禁止） |

`actorId` 和 `namespacePath` 都有对应的条件键，所以“按用户限制”能在 IAM 层表达出来。

## 3. 候选方案对比

| 方案 | 做法 | 挡住 | 代价 / 挡不住 |
|---|---|---|---|
| **A 应用层强制** | 后端从登录态（JWT `sub`）推导 actorId，客户端不传；Agent 工具不暴露 actorId 参数 | T1；T3（前提是工具不暴露参数） | T2 没有兜底；所有调用共用一个高权限角色 |
| **B IAM ABAC（会话标签）** | 按用户签发带 `actorId` 标签的短期凭证；一条策略用 `${aws:PrincipalTag/actorId}` 约束三个条件键，并显式拒绝无条件键的 API | T1、T3、T4、T5、T6，且由 AWS 服务端强制；T2 取决于标签从哪来 | 要有凭证签发环节（STS 默认共享配额约 600 RPS/账号/区域，需要缓存凭证）；actorId 不能含 `/` |
| **C Gateway + Policy（Cedar）** | 用户 OAuth JWT 进入 AgentCore Gateway，Cedar 策略要求 `context.input.actorId == principal.getTag("sub")`、`namespacePath == principal.getTag("namespace")`；Memory 资源策略只接受来自 Gateway 的请求 | T1–T6，并且以用户自己的 token 为准 | 多一层 Gateway；Cedar 不能拼字符串，需要 IdP 直接签发 namespace claim |
| **D Cognito Identity Pool 主体标签** | 客户端用 Cognito 换 AWS 凭证，身份池把 token 属性映射成 principal tag，策略同 B | 同 B，且标签来自 IdP | 客户端直连 AWS，暴露面更大，适合移动端 |
| **E 每租户一个 Memory 资源** | 按 Memory ARN 授权 | 同 B | 只适合少量 B2B 租户；C 端百万用户不可行 |

设计初稿交给 sub agent 做了一轮反方评审，提出的意见我们都采纳了：
- 只用 B 挡不住 T2：标签如果由同一个业务后端从自己的 actorId 变量生成，变量错了，标签也就错了。标签要从已验签的 JWT 派生，并且由独立、权限最小的组件负责签发。
- AgentCore Runtime 里所有用户共用一个执行角色，B 管不到 Agent 内部的工具调用。Agent 路径应该走 C。
- 要加显式 Deny 并禁用缺口 API；actorId 要不透明；容量上要算 STS 配额。

## 4. 推荐方案

按调用路径分两类：

| 调用路径 | 推荐 | 要点 |
|---|---|---|
| 业务后端确定性地读写记忆（会话开始预取、对话结束写入） | **A + B** | actorId 由登录态推导；Memory 客户端使用带 `actorId` 标签的短期凭证。标签通过 `AssumeRoleWithWebIdentity` 或一个独立的凭证签发服务从已验签 JWT 派生；按（用户，过期时间）缓存凭证 |
| Agent 在运行中自主调用记忆工具（AgentCore Runtime） | **C** | Runtime 入站 JWT 认证 → Gateway（Memory target）→ Cedar 校验 actorId / namespacePath 与 JWT claim 一致；Memory 资源策略只允许 Gateway 访问 |

两类都要满足：
- **actorId 不透明**：用 UUID 或 `HMAC(sub, secret)`，固定长度，字符只用 `[a-z0-9-]`，并在应用层用正则校验。
- **IAM 策略写法**：只用不带 `IfExists` 的 `StringEquals` / `StringLike`。显式 Deny 要配上 `Null: false`，防止条件键缺失时出现意外。
- **禁用缺口 API**：显式禁止 `ListActors`、`ListMemoryExtractionJobs`、`StartMemoryExtractionJob`、`BatchCreate/UpdateMemoryRecords` 以及控制面操作。
- **调用记录类 API 时始终传 namespace**：实测中，不传 namespace 的 `GetMemoryRecord` 连自己的记录也会被拒，因为条件键不会由服务端回填。

### 实测用的策略（B）

信任策略：只有后端主体可以 `sts:AssumeRole` + `sts:TagSession`，并且必须带 `actorId` 标签（`"Null": {"aws:RequestTag/actorId": "false"}`）。权限策略：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "OwnEventsOnly", "Effect": "Allow",
     "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:ListSessions"],
     "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>",
     "Condition": {"StringEquals": {"bedrock-agentcore:actorId": "${aws:PrincipalTag/actorId}"}}},
    {"Sid": "OwnRecordsByExactNamespace", "Effect": "Allow",
     "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:DeleteMemoryRecord",
                "bedrock-agentcore:BatchDeleteMemoryRecords"],
     "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>",
     "Condition": {"StringLike": {"bedrock-agentcore:namespace": "/u/${aws:PrincipalTag/actorId}/*"},
                   "Null": {"bedrock-agentcore:namespacePath": "true"}}},
    {"Sid": "OwnRecordsByNamespacePath", "Effect": "Allow",
     "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
     "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>",
     "Condition": {"StringLike": {"bedrock-agentcore:namespacePath": "/u/${aws:PrincipalTag/actorId}/*"},
                   "Null": {"bedrock-agentcore:namespace": "true"}}},
    {"Sid": "DenyOtherActorEvents", "Effect": "Deny",
     "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:ListSessions"],
     "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>",
     "Condition": {"StringNotEquals": {"bedrock-agentcore:actorId": "${aws:PrincipalTag/actorId}"},
                   "Null": {"bedrock-agentcore:actorId": "false"}}},
    {"Sid": "DenyOtherNamespace", "Effect": "Deny", "Action": "bedrock-agentcore:*",
     "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>",
     "Condition": {"StringNotLike": {"bedrock-agentcore:namespace": "/u/${aws:PrincipalTag/actorId}/*"},
                   "Null": {"bedrock-agentcore:namespace": "false"}}},
    {"Sid": "DenyOtherNamespacePath", "Effect": "Deny", "Action": "bedrock-agentcore:*",
     "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>",
     "Condition": {"StringNotLike": {"bedrock-agentcore:namespacePath": "/u/${aws:PrincipalTag/actorId}/*"},
                   "Null": {"bedrock-agentcore:namespacePath": "false"}}},
    {"Sid": "DenyNoActorScopeAndAdmin", "Effect": "Deny",
     "Action": ["bedrock-agentcore:ListActors", "bedrock-agentcore:ListMemoryExtractionJobs",
                "bedrock-agentcore:StartMemoryExtractionJob", "bedrock-agentcore:BatchCreateMemoryRecords",
                "bedrock-agentcore:BatchUpdateMemoryRecords", "bedrock-agentcore:UpdateMemory",
                "bedrock-agentcore:DeleteMemory", "bedrock-agentcore:CreateMemory"],
     "Resource": "*"}
  ]
}
```

这份策略同时允许精确 namespace 检索和单次分层检索（`namespacePath=/u/{actorId}/`）。主报告里“IAM 隔离和单次分层检索不能同时用”的问题，原因是当时只对 `namespace` 设了条件；给 `namespacePath` 也加上条件后就解决了。

## 5. 实测结果（v3，actorId 为不透明 ID）

设置：Alice（攻击者）、Bob（受害者），以及 ID 以 Alice 为前缀的 `alice2`，各自写入一个独有的推荐码，等长期记忆生成后测试。后端用带标签 `actorId=alice` 的凭证代表 Alice，再让 Alice 尝试以下各种访问。**33/33 符合预期，测试结束后 Bob 的记录完好。**

| 类别 | 操作 | 结果 |
|---|---|---|
| 自己的数据 | 精确 namespace 检索、单次分层检索、列出记录、检索会话摘要、写事件、列会话、按 ID 读自己的记录（传 namespace） | 全部允许，只看到自己的推荐码 |
| 读别人的长期记忆 | 精确 namespace / 分层检索 / 列出 Bob 的记录；`namespacePath=/u/`、`/`、`/u/alice`（缺结尾斜杠）、`/u/*/`；同时传 `namespace=Bob` 与 `namespacePath=Alice`；`alice2` 前缀混淆 | 全部 AccessDenied |
| 路径穿越 | `namespacePath=/u/alice/../bob/` | 参数校验直接拒绝（不允许 `.`） |
| 按记录 ID 访问 | Get / Delete / BatchDelete Bob 的记录：不传 namespace、谎报为 Alice 的 namespace、如实传 Bob 的 namespace | 拒绝（AccessDenied / ResourceNotFound / 逐条拒绝），记录未被删除 |
| 写别人的数据 | 以 Bob 的 actorId 写事件；`BatchCreateMemoryRecords` 写到 Bob 的 namespace | AccessDenied |
| 读别人的原始对话 | Bob 的 `ListSessions` / `ListEvents` / `GetEvent` | AccessDenied |
| 枚举与提权 | `ListActors`、`ListMemoryExtractionJobs`、`StartMemoryExtractionJob`；不带标签 AssumeRole；用 Alice 的凭证重新 AssumeRole 并把标签改成 Bob | 全部拒绝 |

### 风险演示（v2）：actorId 含 `/` 时会泄漏

AgentCore 的 actorId 允许 `/`。我们额外创建了 actor `alice/x`，它的记忆落在 `/u/alice/x/...` 下，正好被 `/u/alice/*` 匹配：
- Alice 的单次分层检索和列出记录（`/u/alice/`）返回了 `alice/x` 的推荐码；
- Alice 直接检索 `/u/alice/x/` 也被允许。

这不是策略写错了，而是 namespace 模板和 actorId 字符集的组合问题。所以 actorId 必须用不含 `/` 的不透明 ID，并在应用层强制校验。

## 6. 对照：TDAM 能做到吗

TDAM 的数据面只认一个共享的网关 Bearer key，`user_id` 由调用方在请求里自报。主评测中，持有这个 key 的调用方改一下 `user_id`，就读到了另一个用户的 12 条记录。TDAM 没有把调用方身份和 `user_id` 绑定的机制（它的 user key 只用于元数据面）。要达到同等效果，只能在 TDAM 前面自建一层鉴权代理：从 JWT 推导 `user_id` 并覆盖请求，同时网关 key 不能下发到任何业务以外的组件。这相当于方案 A，没有服务端兜底。

## 7. 落地清单

- [ ] actorId 改为不透明 ID（UUID / HMAC），应用层正则校验，禁止 `/`、`*`、`:`
- [ ] 凭证签发：从已验签的 JWT 派生 `actorId` 标签（`AssumeRoleWithWebIdentity` / Cognito 主体标签 / 独立签发服务），按用户缓存到过期前 5 分钟
- [ ] 上线上述 IAM 策略，配合 SCP 兜底禁止控制面操作
- [ ] Agent 路径：Runtime 入站 JWT → Gateway Memory target + Cedar 策略；Memory 资源策略限制只允许 Gateway 访问；先用 LOG_ONLY 模式观察
- [ ] 记录类 API 始终带 namespace 参数
- [ ] 容量：估算峰值 STS 请求数，提前申请提额；监控 STS 和 Memory 的 Throttles
- [ ] 回归测试：把 `scripts/actor_isolation_test.py` 纳入 CI，每次修改策略后都运行一遍
