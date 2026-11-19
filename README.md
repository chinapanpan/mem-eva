# mem-eva：Agent 记忆系统评测框架

用同一套数据、同一个回答模型和同一份上下文预算，对比自建记忆系统和托管记忆服务。目前内置两个被测系统：

- **TencentDB Agent Memory（TDAM）**：开源自建，Memory Core v3（L0 对话 → L1 原子 → L2 场景 → L3 画像），SQLite + BM25 + 向量混合检索。
- **Amazon Bedrock AgentCore Memory**：托管服务，内置 Semantic + User Preference + Summary 三种长期记忆策略（下文简称 SPU）。

框架和被测系统解耦，换成 mem0、Zep 或自研系统时只需要再写一个 adapter（见 `memeva/backends/base.py`）。

> 结论与数据：[`docs/REPORT.md`](docs/REPORT.md) · 评测设计：[`docs/DESIGN.md`](docs/DESIGN.md) · TDAM 配置差异：[`docs/CONFIG_DIFF.md`](docs/CONFIG_DIFF.md)

## 评测维度

| # | 维度 | 指标 | 怎么测 |
|---|---|---|---|
| D1 | 记忆问答质量 | 答题正确率（总体 / 分类，Wilson 95% CI，配对 McNemar 检验） | web3_zh 自建数据集 + LongMemEval-S 子集，LLM judge 用 LongMemEval 官方模板 |
| D2 | 上下文质量 | 4K 上下文事实覆盖率 | 判定标注事实是否出现在真正交给回答模型的 4K 上下文里 |
| D3 | 知识更新 | update 类题正确率 | 同一事实在后续会话中被修改（白名单地址、杠杆上限） |
| D4 | 写入→可检索时延 | ready 秒数 p50/p95 | 写入一段带邀请码的会话，轮询直到能检索到 |
| D5 | 在线检索性能 | p50/p95/p99、吞吐、错误率（并发 1/8/32） | 预热后做 closed-loop 压测 |
| D6 | 多租户隔离 | 跨租户泄漏率；伪造身份能否越权读取 | 用 B 的身份查 A 的独有 token；IAM 条件键 vs 共享 API Key |
| D7 | 删除 / 遗忘 | 删除后的检索残留、存储残留、生效时延 | 删除用户后在 0/30/120 秒各检查一次 |
| D8 | 故障恢复 | 故障窗口、错误数、数据是否丢失 | 压测期间重启 TDAM 容器 |
| D9 | 运维与成本 | 组件清单、按量成本 | 实测 token 用量 + 公开单价 |
| D10 | 安全 | 密钥 / 助记词是否被写进长期记忆、鉴权方式、加密、审计 | 数据集里埋了假的 API secret |

## 数据集

- **`data/web3_zh/dataset.json`**（已入库，可直接用）：30 个模拟交易所用户，每人 8 个会话，中文为主、夹带英文。共 120 题，覆盖 7 类：
  `fact` / `preference` / `update` / `temporal` / `multi_session` / `abstention` / `cross_lingual`。
  事实、时间线和标准答案由带种子的 schema 确定性生成（`scripts/gen_web3_dataset.py`），LLM 只负责写对话。每个会话都校验过：埋入的关键字符串必须原样出现在用户发言里。数据里还埋了隔离探针（钱包地址、宠物名）和泄漏探针（假 API secret）。
- **LongMemEval-S**（公开基准，固定 HF commit 和 SHA256）：每类抽 1 题，共 7 题，每题大约 115K tokens 的对话历史。
- **换成自己的脱敏数据**：按 `memeva/datasets.py` 顶部的 schema 准备 JSON，放到 `data/<name>/dataset.json` 并在 `config.yaml` 里登记即可。

## 快速复现

### 0. 前提
- AWS 账号，开通 Bedrock 模型访问（Claude Haiku 5.5、Sonnet 5.5、Titan Text Embeddings V2），区域默认 `us-east-1`。
- 本机装好 aws cli、python3.12、docker。

### 1. 一键部署测试环境（推荐，和报告的拓扑一致）
```bash
deploy/aws/deploy.sh up      # 默认 VPC 中起 2 台 EC2：runner + TDAM（只开放 runner SG → 8420，SSM 管理，无公网入站）
deploy/aws/deploy.sh run "python -m memeva provision"   # 创建 AgentCore Memory（SPU）
```

### 2. 跑评测
```bash
R=r1
deploy/aws/deploy.sh run ".venv/bin/python -m memeva all --run $R --dataset web3_zh"   # ingest → wait → evaluate → report
deploy/aws/deploy.sh run ".venv/bin/python -m memeva all --run $R --dataset lme"       # 需先下载 LongMemEval-S（见下）
for p in isolation secrets lag perf deletion; do
  deploy/aws/deploy.sh run ".venv/bin/python -m memeva probe --run $R --dataset web3_zh --name $p"
done
```
`spoof`（身份伪造）探针要临时创建一个 IAM 角色，需要在有 IAM 权限的机器上运行：`python -m memeva probe --name spoof --run $R`。
`failover` 探针通过 `--kill-cmd` 指定怎么重启 TDAM，例如用 SSM 在 TDAM 主机上执行 `docker restart tdam-core`。

### 本机单机跑（开发 / 小样本）
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd deploy/tdam && TDAM_API_KEY=$(openssl rand -hex 16) TDAM_BIND=127.0.0.1 ./start.sh && cd -
export TDAM_API_KEY=...   # 同上
.venv/bin/python -m memeva provision
.venv/bin/python -m memeva all --run dev --dataset web3_zh --limit 2
```

LongMemEval-S 下载：
```bash
mkdir -p .cache && curl -L -o .cache/longmemeval_s_cleaned.json \
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_s_cleaned.json
```

### 3. 清理
```bash
deploy/aws/deploy.sh run ".venv/bin/python -m memeva cleanup"   # 删除 AgentCore Memory
deploy/aws/deploy.sh down                                      # 删除 EC2 / SG / IAM
```

## 输出
`runs/<run>/<dataset>/` 下保存：`dataset.json`（冻结的数据）、`ingest/`（写入 checkpoint 和时延）、`readiness/`（异步抽取等待记录）、
`results/<arm>/<qid>.json`（检索上下文、回答、评分、事实覆盖）、`probes/*.json`、`usage.jsonl`（模型 token），以及汇总的 `report.json` 和 `results.md`。
所有步骤都支持断点续跑。

## 公平性约定
- 所有系统共用同一个回答模型（Haiku 5.5）、同一个评分模型（Sonnet 5.5）、同样的 4,096-token 上下文预算和 50 条候选上限。
- TDAM 按它最强的配置来测：chat 模式、hybrid 检索、原生的 L3+L2+L1 组装方式，召回条数从默认 5 条放宽到 50 条。每一项改动都列在 `docs/CONFIG_DIFF.md`。
- AgentCore 用默认内置策略，没有自定义 prompt。托管服务内部用什么抽取模型不可控，报告里写明了这一点。
- 每题和每个用户都使用独立的 actor/user，按时间顺序写入，问题和答案不会注入记忆系统。

## 许可
MIT。评分模板改编自 [LongMemEval](https://github.com/xiaowu0162/LongMemEval)（MIT，见 `vendor/LICENSE.LongMemEval`）。
