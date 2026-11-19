# mem-eva 评估框架设计（v1）

v0 草案经过一轮反方评审（challenger），又跑了两轮 smoke test，期间改了不少东西，改动记录见文末。

## 1. 评测对象（arms）

| arm | 说明 |
|---|---|
| `none` | 不用记忆，测模型先验和拒答基线 |
| `tdam_full` | TDAM Memory Core v3，复刻原生 auto-recall 的组装方式：L3 persona → L2 scene navigation → L1 relevant memories（BM25 + Titan 向量 + RRF） |
| `tdam_l1` | 只用 TDAM 的 L1 原子记忆 |
| `agentcore_spu` | AgentCore Memory，一个 Memory 资源挂三种内置策略：Semantic、UserPreference、Summary。三个 namespace 并行检索，按名次交错合并 |

所有 arm 共用同一个回答模型（Claude Haiku 5.5）、同一个评分模型（Claude Sonnet 5.5，LongMemEval 官方 judge 模板）、4,096 tokens（cl100k）的上下文预算，候选上限都是 50。
TDAM 内部的抽取 LLM 是 Haiku 5.5，embedding 用 Titan V2，经 LiteLLM 提供 OpenAI 兼容接口。AgentCore 的抽取模型由服务托管，外部不可控。

**TDAM 的多租户拓扑**：v3 的 L2/L3 只按 `team + agent` 划分作用域（`profile-scope.ts`），不传 `team_id` 时会回落到 `default`。
为了让每个终端用户有私有的 persona，评测默认给每个用户分配一个 team（`team_id = user_id`）。共享 team 的拓扑放在 D6c 单独测。

## 2. 维度与指标

| # | 维度 | 指标 | 方法 | 自动化 |
|---|---|---|---|---|
| D1 | 问答质量 | 正确率（总体/分类），Wilson 95% CI；以 `agentcore_spu` 为基准做配对精确 McNemar 检验 | web3_zh 120 题 + LongMemEval-S 7 题 | 全自动 |
| D2 | 上下文质量 | 4K 上下文事实覆盖率 | Sonnet 判定每条 gold fact 是否出现在实际喂给回答模型的上下文中 | 全自动 |
| D3 | 知识更新 | `update` / `knowledge-update` 类正确率 | 白名单地址、杠杆上限在后续会话中被修改 | 全自动 |
| D4 | 写入→可检索时延 | ready 秒数 p50/p95 | 6 个新用户，在 6 轮对话的第 3 轮写入邀请码，每 10 秒轮询一次 | 全自动 |
| D5 | 在线检索性能 | p50/p95/p99、吞吐、错误率，并发 1/8/32 | closed-loop，每档 45 秒，先预热；runner 到 TDAM 走 VPC 私网，AgentCore 走同区域 API | 全自动 |
| D6a | 跨租户泄漏（调用方配置正确） | 泄漏率 | 每个用户用另外 3 个用户的身份检索，query 里直接带上本人的独有 token | 全自动 |
| D6b | 身份伪造 | 能否越权读取 | TDAM：持有网关 Bearer key 的调用方改 `user_id`。AgentCore：IAM 角色加 `bedrock-agentcore:namespace` 条件 | 全自动（需要 IAM 权限） |
| D6c | 共享 team 拓扑 | 用户 B 的 persona/scene 里是否出现用户 A 的 token | 3 个用户使用同一个 team+agent | 全自动（约 20 分钟） |
| D7 | 删除 | 删除后检索残留、存储残留 | 删除 5 个用户，在 0/30/120 秒各检查一次 | 全自动（破坏性操作，最后执行） |
| D8 | 故障 | 故障窗口、失败请求数、数据是否丢失 | 并发 4 的检索期间重启 TDAM 容器 | 半自动 |
| D9 | 成本 | 实测 token（含 TDAM 内部 LLM）+ 公开单价 | `usage.jsonl` + LiteLLM 回调 | 全自动 |
| D10 | 安全 | 假 API secret 是否被写进长期记忆；鉴权、加密、审计清单 | 每个用户在会话 5 里贴一个假 secret | 全自动 + 清单 |

v0 里的 Recall@K 和 MRR 被删掉了：两个系统的记录粒度差别很大（TDAM 的原子记录很短，AgentCore 的 summary 很长），这两个指标没有可比性。

## 3. 数据集
- **web3_zh**（主数据集）：30 个用户，每人 8 个会话，共 120 题，分 7 类（fact / preference / update / temporal / multi_session / abstention / cross_lingual）。事实、时间线和答案由带种子的 schema 生成；对话由 Sonnet 5.5 编写，并校验每个事实的关键串原样出现。每个用户的对话约 9K tokens。
- **LongMemEval-S**：7 题（每类 1 题，seed 20261119），每题约 115K tokens，用来和公开基准、AgentMemBench 对照。

## 4. 流程
`provision → ingest → wait → evaluate → report → probes`

写入时，TDAM 每个 user→assistant 轮次调用一次 `conversation/add`；AgentCore 每 ≤20 条消息或 ≤3K tokens 调用一次 `CreateEvent`。两者都带原始时间戳和幂等 checkpoint。

就绪判定：TDAM 在最后一次写入后至少等 900 秒，并且 pipeline 处于 idle、计数连续 3 次 60 秒轮询都不变；AgentCore 至少等 300 秒，计数连续 3 次稳定，且没有 FAILED 的抽取任务（如果有，用 `StartMemoryExtractionJob` 重试）。

## 5. 部署（us-east-1）
runner 和 TDAM 分别部署在两台 EC2 上（`deploy/aws/stack.yaml`）。TDAM 的 8420 端口只对 runner 的安全组开放，两台机器都通过 SSM 管理，不开放公网入站。

## 6. 局限
- 样本量小（120 题 + 7 题），CI 较宽；分类结论只能作为方向性参考。
- 托管服务的抽取模型不可控。TDAM 的抽取模型可以换（比如 Sonnet），但成本和效果会随之变化。
- TDAM 用的是 standalone + SQLite 部署形态；它的 MongoDB 后端是实验特性，TCVDB 云托管版本未测。
- 时延数据来自单 runner 的小样本，不代表生产 SLO。

## 修订记录（v0 → v1）
| 来源 | 改动 |
|---|---|
| Challenger | TDAM 拆成 full/l1 两个 arm，按原生 recall 组装；删掉 SPUE；并发最高 32；补充密钥泄漏、身份伪造维度；LongMemEval 缩减到 7 题；TDAM 部署在独立 EC2 |
| Preflight | `promptMode=chat`；必须设置 API key（v3 不带 key 时返回 401）；v1 `/recall` 读不到 v3 写入的数据，不使用 |
| Smoke 1 | 发现 L2/L3 由同一 team+agent 下的所有用户共享：给每个用户一个 team，新增 D6c 测试 |
| Smoke 2 | 不传 `team_id` 会回落到 `default`，仍然共享：改成 `team_id = user_id`；等待时间改为 ≥900 秒 |
| Runner | 两个并发进程重复写入 TDAM（TDAM 写入不幂等）：清空后重新写入，checkpoint 改成多进程安全 |
| 正式运行 | TDAM 主机重启后，LiteLLM 密钥失配，抽取全部静默失败：start.sh 改为持久化密钥并做自检，就绪门检测到零记忆时直接报错 |
| 正式运行 | Judge（Sonnet 5.5）先写推理再给结论，max_tokens=10 被截断判成 no：改为最后一行输出 VERDICT、400 tokens，并对已有回答 rejudge |
| 正式运行 | AgentCore 检索在并发 ≥8 时触发默认 30 TPS 配额：新增单次分层检索 `agentcore_path` arm |
