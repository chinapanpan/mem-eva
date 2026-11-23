# Results — run `paced` / dataset `web3_zh`

| arm | correct/n | accuracy | 95% CI | 4K fact coverage | ctx tokens | retrieval p50 (s) | p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 17/120 | 14.2% | 9–22% | —% | 0 | — | — |
| tdam_full | 89/120 | 74.2% | 66–81% | 92.2% | 2508 | 0.154 | 0.182 |
| tdam_l1 | 89/120 | 74.2% | 66–81% | 92.2% | 1160 | 0.148 | 0.184 |
| tdam_shared_full | 66/120 | 55.0% | 46–64% | 94.2% | 3855 | 0.148 | 0.194 |
| tdam_shared_l1 | 88/120 | 73.3% | 65–80% | 94.2% | 1165 | 0.135 | 0.200 |
| agentcore_spu | 94/120 | 78.3% | 70–85% | 96.8% | 4079 | 0.188 | 0.282 |
| agentcore_path | 98/120 | 81.7% | 74–88% | 95.1% | 4085 | 0.178 | 0.209 |

## By category

| arm | abstention | cross_lingual | fact | multi_session | preference | temporal | update |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 17/17 | 0/17 | 0/18 | 0/17 | 0/17 | 0/17 | 0/17 |
| tdam_full | 17/17 | 17/17 | 15/18 | 17/17 | 6/17 | 4/17 | 13/17 |
| tdam_l1 | 17/17 | 17/17 | 15/18 | 17/17 | 6/17 | 4/17 | 13/17 |
| tdam_shared_full | 17/17 | 17/17 | 17/18 | 0/17 | 1/17 | 2/17 | 12/17 |
| tdam_shared_l1 | 17/17 | 17/17 | 17/18 | 15/17 | 6/17 | 4/17 | 12/17 |
| agentcore_spu | 17/17 | 17/17 | 18/18 | 16/17 | 4/17 | 5/17 | 17/17 |
| agentcore_path | 17/17 | 17/17 | 18/18 | 16/17 | 8/17 | 5/17 | 17/17 |

## Temporal sensitivity (deterministic date match)

| arm | n | gold date | gold−7d ("下周X" read as nearest X) | either |
|---|---:|---:|---:|---:|
| none | 17 | 0 | 0 | 0 |
| tdam_full | 17 | 4 | 4 | 8 |
| tdam_l1 | 17 | 3 | 5 | 8 |
| tdam_shared_full | 17 | 4 | 8 | 12 |
| tdam_shared_l1 | 17 | 4 | 8 | 12 |
| agentcore_spu | 17 | 4 | 10 | 14 |
| agentcore_path | 17 | 3 | 10 | 13 |

## Paired comparison (exact McNemar)

- agentcore_spu_vs_none: n=120, discordant=[77, 0], p=0.000
- agentcore_spu_vs_tdam_full: n=120, discordant=[10, 5], p=0.302
- agentcore_spu_vs_tdam_l1: n=120, discordant=[10, 5], p=0.302
- agentcore_spu_vs_tdam_shared_full: n=120, discordant=[29, 1], p=0.000
- agentcore_spu_vs_tdam_shared_l1: n=120, discordant=[10, 4], p=0.180
- agentcore_spu_vs_agentcore_path: n=120, discordant=[1, 5], p=0.219

## Cost estimate (public prices, this run)

```json
{
 "agentcore": {
  "events": 1415,
  "ltm_records": 1365,
  "retrieve_calls_eval": 360,
  "stm_ingest_usd": 0.0162,
  "ltm_storage_usd_month": 1.0237,
  "retrieval_usd": 0.18,
  "total_usd": 1.2199
 },
 "tdam": {
  "internal_llm_embedding_usd": 5.8623,
  "ec2_hourly_usd": 0.2016,
  "note": "add EC2/EBS hours, HA replicas, backups and ops time"
 }
}
```
