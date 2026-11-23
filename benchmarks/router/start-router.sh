#!/usr/bin/env bash
set -euo pipefail

policy=${1:?usage: start-router.sh power_of_two or cache_aware}
case "$policy" in
  power_of_two|cache_aware) ;;
  *) echo "Unsupported benchmark policy: $policy" >&2; exit 2 ;;
esac

name=vllm-router-test
image=vllm/vllm-router@sha256:1128e09f5dc92b201753bc2f306488942d608ec747410f6c207354f0bf377c42
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" --network host --restart no "$image" vllm-router \
  --host 127.0.0.1 --port 8100 --prometheus-port 29100 --policy "$policy" \
  --worker-urls "${WORKER_A:-http://10.169.25.15:8000}" "${WORKER_B:-http://10.169.25.16:8000}" \
  --worker-startup-check-interval 2 --health-check-interval-secs 5 --health-check-timeout-secs 3 \
  --health-failure-threshold 2 --health-success-threshold 2 \
  --cache-threshold 0.3 --balance-abs-threshold 2 --balance-rel-threshold 1.2 --log-level debug
