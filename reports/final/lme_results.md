# Results — run `final` / dataset `lme`

| arm | correct/n | accuracy | 95% CI | 4K fact coverage | ctx tokens | retrieval p50 (s) | p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 1/7 | 14.3% | 3–51% | —% | 0 | — | — |
| agentcore_spu | 5/7 | 71.4% | 36–92% | —% | 4073 | 0.201 | 0.254 |

## By category

| arm | abstention | knowledge-update | multi-session | single-session-assistant | single-session-preference | single-session-user | temporal-reasoning |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 1/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 |
| agentcore_spu | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 1/1 |

## Paired comparison (exact McNemar)

- agentcore_spu_vs_none: n=7, discordant=[4, 0], p=0.125

## Cost estimate (public prices, this run)

```json
{
 "agentcore": {
  "events": 443,
  "ltm_records": 2268,
  "retrieve_calls_eval": 21,
  "stm_ingest_usd": 0.0051,
  "ltm_storage_usd_month": 1.701,
  "retrieval_usd": 0.0105,
  "total_usd": 1.7166
 },
 "tdam": {
  "internal_llm_embedding_usd": 0,
  "ec2_hourly_usd": 0.2016,
  "note": "add EC2/EBS hours, HA replicas, backups and ops time"
 }
}
```
