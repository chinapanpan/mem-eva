# mem-eva 评估框架设计（草案 v0）

## 1. 评测对象（arms）
| arm | 说明 |
|---|---|
| `none` | 不用记忆，测模型先验/拒答基线 |
| `tdam` | TencentDB-Agent-Memory Memory Core（自建 docker，SQLite + BM25 + Titan 向量混合检索；抽取 LLM=Bedrock Haiku 5.5 经 OpenAI 兼容网关） |
| `tdam_bm25` | 同上但关闭向量（TDAM 默认配置 `embedding.provider: none`） |
| `agentcore_spu` | AgentCore Memory：Semantic + UserPreference + Summary 三策略，结果按排名交错合并 |
| `agentcore_spue` | SPU + Episodic（可选） |

所有 arm 共用：同一回答模型（Haiku 5.5）、同一记忆上下文预算（4,096 tokens）、同一评分模型（Sonnet 5.5，LongMemEval 官方 judge prompt）。

## 2. 评测维度与指标

| # | 维度 | 指标 | 方法 | 预期体现 |
|---|---|---|---|---|
| D1 | 记忆质量（QA） | 答题正确率（总体 + 分类） | LongMemEval-S 子集 + 自建中英双语 Web3 数据集 | 抽取/检索质量 |
| D2 | 检索质量 | 4K 上下文事实覆盖率、Recall@K、MRR | 标注 gold facts，LLM 判定是否被覆盖 | 召回 vs 噪声 |
| D3 | 知识更新/冲突 | 更新类题正确率、过期事实残留率 | 数据集中对同一事实多次修改 | 合并/consolidation |
| D4 | 写入→可检索时延 | ingest-to-ready p50/p95（秒） | 写入后轮询直到可召回 | 异步管线 |
| D5 | 在线检索性能 | p50/p95/p99、并发 1/8/32/64 下吞吐与错误率 | 预热后 closed-loop 压测 | 托管弹性 vs 单机 SQLite |
| D6 | 多租户隔离 | 跨租户泄漏率（应为 0）、越权访问是否被拒 | A 用户的独有事实，用 B 身份检索 | IAM + namespace vs 字段隔离 |
| D7 | 删除/遗忘合规 | 删除后残留率、删除生效时延 | 删除用户记忆后检索 | GDPR 类需求 |
| D8 | 可靠性/可用性 | 节点故障期间错误率、恢复时间、数据是否丢失 | kill 容器 / 重启；托管服务看 SLA | 托管 HA |
| D9 | 运维与 TCO | 组件数、需自运维项、按量成本估算（1K/10K/100K 用户） | 清单 + 定价公式 | 免运维 |
| D10 | 安全合规 | 鉴权方式、加密（KMS）、审计（CloudTrail）、VPC 私有接入 | Checklist（文档 + 实测可验证项） | 企业级 |

D1–D7 是自动化实测；D8 半自动实测；D9–D10 为可核查的清单（每项附文档链接/验证命令）。

## 3. 数据集
1. **LongMemEval-S（cleaned，固定 HF commit + SHA256）**：7 类 × N 题（默认 N=2，可配置），每题约 115K tokens 历史。与 AgentMemBench 可比。
2. **web3-assistant-zh（自建，提交到仓库）**：模拟交易所 AI 助手，中英混合。
   - 合成 users × sessions，每个用户有 gold facts：偏好（风险偏好、常用链、语言）、事实（KYC 等级、VIP 等级、API key 用途）、更新（换绑钱包、调整杠杆上限）、时间（"上周三"类相对时间）、跨会话聚合、拒答题、隔离探针、删除探针。
   - 生成脚本固定 seed 与模型，产物 JSON 直接入库，客户可直接复用或替换成自己的脱敏数据（提供 schema）。

## 4. 流程
`prepare → provision → ingest → wait/readiness → evaluate(retrieve→answer→judge) → perf → isolation → deletion → report`
- 每题独立 actor/user，历史按时间顺序写入；不注入问题和答案。
- AgentCore：CreateEvent（带原始 timestamp），限速 100K tok/min，等待 ≥300s 并检查 extraction job 失败、可 repair。
- TDAM：/v3/conversation/add（带 timestamp），写完调用 /session/end 冲刷，轮询 /v2/pipeline/status + atomic/count 直到稳定。
- Checkpoint 续跑；所有原始检索结果、上下文、回答、评分落盘。

## 5. 部署
- us-east-1 一台 bench runner EC2（无入站，SSM 管理）：运行 TDAM docker + LiteLLM 网关 + harness；AgentCore 同区域。
- 开发期在本机跑小规模 smoke。

## 6. 公平性与限制
- 托管服务内部抽取模型不可控；TDAM 抽取模型选用与回答模型同档的 Haiku 5.5。
- 统一上下文预算与回答模型；报告列出所有配置与版本（docker image digest、SDK 版本）。
- 小样本结果给出 95% 置信区间（Wilson）；不宣称生产 SLO。
