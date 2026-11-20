#!/usr/bin/env bash
# Run the non-QA probes for one run/dataset in a safe order (deletion last: it is destructive).
# Run after `memeva all` has evaluated every arm (incl. agentcore_path).
#   scripts/run_probes.sh <run> [tdam-instance-id-for-failover]
set -uo pipefail
RUN=${1:?run}; TDAM_ID=${2:-}
DATASET=${DATASET:-web3_zh}
P=".venv/bin/python -m memeva probe --run $RUN --dataset $DATASET"
STORES=${STORES:-tdam,tdam_shared,agentcore}
$P --name isolation --stores "$STORES"
$P --name secrets --stores "$STORES"
$P --name perf --arms tdam_l1,tdam_shared_l1,tdam_full,agentcore_spu,agentcore_path
$P --name lag --stores tdam,agentcore
$P --name tdam_team_scope
.venv/bin/python scripts/analyze_profile_sharing.py "runs/$RUN/$DATASET" > "runs/$RUN/$DATASET/probes_profile_sharing.jsonl"
if [ -n "$TDAM_ID" ]; then
  $P --name failover --kill-cmd "aws ssm send-command --region ${REGION:-us-east-1} --instance-ids $TDAM_ID --document-name AWS-RunShellScript --parameters commands='docker restart tdam-core' --output text --query Command.CommandId"
fi
$P --name deletion --stores "$STORES"
echo PROBES_DONE
