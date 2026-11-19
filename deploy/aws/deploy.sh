#!/usr/bin/env bash
# Deploy / operate the mem-eva benchmark environment in one region (default us-east-1).
#
#   deploy/aws/deploy.sh up                    # create stack (default VPC, first default subnet), bootstrap TDAM host
#   deploy/aws/deploy.sh run "<cmd>"           # run a shell command on the runner as ec2-user (via SSM)
#   deploy/aws/deploy.sh tdam "<cmd>"          # run a shell command on the TDAM host
#   deploy/aws/deploy.sh down                  # delete stack
#
# Requires: aws cli v2, permissions for CloudFormation/EC2/IAM/SSM. No inbound ports are opened to the internet.
set -euo pipefail
REGION="${REGION:-us-east-1}"
STACK="${STACK:-memeva-bench}"
REPO="${REPO:-https://github.com/chinapanpan/mem-eva.git}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENVF="$HERE/.env"      # holds the generated TDAM key locally (git-ignored)

out() { aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

ssm() {  # ssm <instance-id> <command> [timeout-seconds]
  local id=$1 cmd=$2 to=${3:-3600}
  local cid
  cid=$(aws ssm send-command --region "$REGION" --instance-ids "$id" --document-name AWS-RunShellScript \
        --timeout-seconds 600 --parameters "{\"commands\":[$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$cmd")],\"executionTimeout\":[\"$to\"]}" \
        --query Command.CommandId --output text)
  while :; do
    st=$(aws ssm get-command-invocation --region "$REGION" --command-id "$cid" --instance-id "$id" --query Status --output text 2>/dev/null || echo Pending)
    case "$st" in Success|Failed|Cancelled|TimedOut) break;; esac
    sleep 5
  done
  aws ssm get-command-invocation --region "$REGION" --command-id "$cid" --instance-id "$id" \
      --query '[StandardOutputContent,StandardErrorContent]' --output text
  [ "$st" = Success ]
}

case "${1:-}" in
  up)
    VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
    SUBNET=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC" Name=default-for-az,Values=true \
             --query 'sort_by(Subnets,&AvailabilityZone)[0].SubnetId' --output text)
    aws cloudformation deploy --region "$REGION" --stack-name "$STACK" --template-file "$HERE/stack.yaml" \
      --capabilities CAPABILITY_IAM --parameter-overrides VpcId="$VPC" SubnetId="$SUBNET" --tags ManagedBy=mem-eva
    [ -f "$ENVF" ] || echo "TDAM_API_KEY=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$ENVF"
    . "$ENVF"
    TDAM=$(out TdamId); RUNNER=$(out RunnerId)
    echo "waiting for SSM agents..."
    for id in "$TDAM" "$RUNNER"; do
      until aws ssm describe-instance-information --region "$REGION" --filters Key=InstanceIds,Values="$id" \
            --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null | grep -q Online; do sleep 10; done
    done
    ssm "$TDAM" "set -e; until command -v docker && command -v git && command -v python3.12; do sleep 5; done; systemctl start docker
      rm -rf /opt/mem-eva && git clone -q $REPO /opt/mem-eva && cd /opt/mem-eva/deploy/tdam && TDAM_API_KEY=$TDAM_API_KEY ./start.sh" 1800
    ssm "$RUNNER" "set -e; until command -v git && command -v python3.12; do sleep 5; done
      sudo -u ec2-user bash -lc 'rm -rf ~/mem-eva && git clone -q $REPO ~/mem-eva && cd ~/mem-eva && python3.12 -m venv .venv && .venv/bin/pip install -q -r requirements.txt'
      echo 'export TDAM_API_KEY=$TDAM_API_KEY MEMEVA_TDAM__URL=http://$(out TdamPrivateIp):8420' >> /home/ec2-user/.bashrc" 1800
    echo "runner=$RUNNER tdam=$TDAM tdam_ip=$(out TdamPrivateIp)"
    ;;
  run)  ssm "$(out RunnerId)" "sudo -u ec2-user bash -lc $(printf %q "cd ~/mem-eva && $2")" "${3:-7200}";;
  tdam) ssm "$(out TdamId)" "$2" "${3:-3600}";;
  down) aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK"
        aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK"; echo deleted;;
  *) sed -n 2,9p "$0"; exit 1;;
esac
