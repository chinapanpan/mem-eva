# TDAM 配置与上游默认值的差异

被测版本：`agentmemory/memory-core@sha256:342331d5ccdb31ce84ac1ba205aa0b1a8b16a13fe71b6a104d17d41197eb7112`（Docker Hub `latest`，构建于 2026-10-26），
源码参考分支 `feat/server_team`。基准配置文件：`deploy/tdam/tdai-gateway.yaml.tmpl`；上游参考：`deploy/global-images/start-memory-core.sh` 生成的配置。

| 配置项 | 上游默认 | mem-eva | 原因 / 对 TDAM 的影响 |
|---|---|---|---|
| `memory.promptMode` | `code`（抽取工程事实/SOP） | `chat` | 评测场景是个人助手；`code` 模式在闲聊里可能抽出 0 条记忆。**对 TDAM 有利** |
| `memory.embedding.provider` | `none`（只有 BM25） | `openai` 兼容 → Titan Text Embeddings V2（1024 维） | 打开 TDAM 的 hybrid（BM25+向量+RRF），用它最强的检索配置。**对 TDAM 有利** |
| `llm` | 用户自填（示例 deepseek / gpt-4o） | Claude Haiku 5.5（Bedrock，经 LiteLLM 提供 OpenAI 兼容接口） | 与回答模型同档；与 AgentCore 托管抽取模型不可控相对应。可替换为 Sonnet 做对照 |
| `TDAI_GATEWAY_API_KEY` | 空（脚本默认关闭） | 随机 key | v3 数据面不带 Bearer 返回 401；同时也是生产部署的最低安全要求 |
| 召回条数 | `recall.maxResults: 5`、`scoreThreshold: 0.3`（v1 `/recall`） | v3 `atomic/search limit=50`，无阈值，统一装填 4K tokens | 与 AgentCore 相同的候选上限和上下文预算。**对 TDAM 有利**（默认只给 5 条） |
| 召回组装 | `core/hooks/auto-recall.ts`：L3 persona + L2 scene navigation + L1 relevant memories | `tdam_full` 按同样顺序组装 | 复刻原生注入方式；另设 `tdam_l1` 只用 L1 |
| pipeline 定时器 | `everyNConversations 5`、`l1IdleTimeoutSeconds 600`、`l2DelayAfterL1 90`、`l2MinInterval 900` | **不改** | 保持默认；评测前等待 ≥660s 且 pipeline idle、计数稳定 |
| 存储 | SQLite（standalone） | **不改** | 官方默认。MongoDB 后端是实验特性；TCVDB 只在腾讯云上可用 |
| `persona.triggerMode` | `current` | **不改** | JEV 即时更新依赖 TokenHub 外部服务 |

## 写入方式
每个 user→assistant 轮次调用一次 `POST /v3/conversation/add`，带原始 `timestamp`，这和在线 Agent 的写法一致。
单条消息超过 8,000 字符时按 8,000 字符切分（API 上限 8,192）。

## 已知限制
- v3 原子记录的 `created_at` 是处理时间，API 不返回事件时间；抽取文本里通常已经写了绝对日期，因此 `tdam_*` 不额外渲染时间戳。
- v1 `/recall` 读的是默认单例 core，不读 v3 按实例写入的数据，因此不用。
- 如果客户生产环境的 yaml 不同，可以替换 `deploy/tdam/tdai-gateway.yaml.tmpl` 后重跑，其他步骤不变。
