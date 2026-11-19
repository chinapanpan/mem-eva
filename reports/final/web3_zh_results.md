# Results — run `final` / dataset `web3_zh`

| arm | correct/n | accuracy | 95% CI | 4K fact coverage | ctx tokens | retrieval p50 (s) | p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 17/120 | 14.2% | 9–22% | —% | 0 | — | — |
| tdam_full | 43/120 | 35.8% | 28–45% | 47.6% | 2411 | 0.153 | 0.241 |
| tdam_l1 | 48/120 | 40.0% | 32–49% | 47.2% | 662 | 0.149 | 0.178 |
| tdam_shared_full | 37/120 | 30.8% | 23–40% | 70.9% | 3021 | 0.154 | 0.190 |
| tdam_shared_l1 | 72/120 | 60.0% | 51–68% | 71.8% | 965 | 0.137 | 0.177 |
| agentcore_spu | 96/120 | 80.0% | 72–86% | 95.1% | 4078 | 0.192 | 0.282 |

## By category

| arm | abstention | cross_lingual | fact | multi_session | preference | temporal | update |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 17/17 | 0/17 | 0/18 | 0/17 | 0/17 | 0/17 | 0/17 |
| tdam_full | 17/17 | 12/17 | 6/18 | 5/17 | 1/17 | 0/17 | 2/17 |
| tdam_l1 | 17/17 | 12/17 | 6/18 | 5/17 | 4/17 | 2/17 | 2/17 |
| tdam_shared_full | 17/17 | 3/17 | 2/18 | 0/17 | 2/17 | 4/17 | 9/17 |
| tdam_shared_l1 | 17/17 | 15/17 | 13/18 | 10/17 | 4/17 | 4/17 | 9/17 |
| agentcore_spu | 17/17 | 17/17 | 18/18 | 15/17 | 7/17 | 5/17 | 17/17 |

## Temporal sensitivity (deterministic date match)

| arm | n | gold date | gold−7d ("下周X" read as nearest X) | either |
|---|---:|---:|---:|---:|
| none | 17 | 0 | 0 | 0 |
| tdam_full | 17 | 2 | 3 | 5 |
| tdam_l1 | 17 | 2 | 3 | 5 |
| tdam_shared_full | 17 | 3 | 4 | 7 |
| tdam_shared_l1 | 17 | 2 | 4 | 6 |
| agentcore_spu | 17 | 3 | 10 | 13 |

## Paired comparison (exact McNemar)

- agentcore_spu_vs_none: n=120, discordant=[79, 0], p=0.000
- agentcore_spu_vs_tdam_full: n=120, discordant=[54, 1], p=0.000
- agentcore_spu_vs_tdam_l1: n=120, discordant=[51, 3], p=0.000
- agentcore_spu_vs_tdam_shared_full: n=120, discordant=[60, 1], p=0.000
- agentcore_spu_vs_tdam_shared_l1: n=120, discordant=[26, 2], p=0.000

## Cost estimate (public prices, this run)

```json
{
 "agentcore": {
  "events": 240,
  "ltm_records": 1832,
  "retrieve_calls_eval": 360,
  "stm_ingest_usd": 0.0027,
  "ltm_storage_usd_month": 1.374,
  "retrieval_usd": 0.18,
  "total_usd": 1.5567
 },
 "tdam": {
  "internal_llm_embedding_usd": 5.2505,
  "ec2_hourly_usd": 0.2016,
  "note": "add EC2/EBS hours, HA replicas, backups and ops time"
 }
}
```
