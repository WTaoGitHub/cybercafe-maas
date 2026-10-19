#!/bin/sh
# Run as root on each worker. Bootstrap token is read from a private file.
set -eu
ROOT=/root/wtaobj
IMAGE=quay.io/gpustack/gpustack:v2.2.3
IP=${1:?usage: deploy-gpustack.sh 10.11.31.1-or-2}
docker pull "$IMAGE"
if [ "$IP" = 10.11.31.1 ]; then
  docker inspect wta-gpustack-server >/dev/null 2>&1 || docker run -d \
    --name wta-gpustack-server --restart unless-stopped \
    -p 127.0.0.1:18080:80 -p 10.11.31.1:18080:80 \
    -v "$ROOT/gpustack-server:/var/lib/gpustack" "$IMAGE" \
    --system-default-container-registry quay.io
fi
test -s "$ROOT/worker-token"
docker inspect wta-gpustack-worker >/dev/null 2>&1 || docker run -d \
  --name wta-gpustack-worker --restart unless-stopped --privileged --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$ROOT/gpustack-worker:/var/lib/gpustack" \
  -v "$ROOT/gpu-ocr:$ROOT/gpu-ocr:ro" --gpus all "$IMAGE" \
  --server-url http://10.11.31.1:18080 --token "$(cat "$ROOT/worker-token")" \
  --advertise-address "$IP" --worker-name "$(hostname)"
