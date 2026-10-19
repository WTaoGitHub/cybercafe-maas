#!/bin/sh
set -eu
for variant in A_v4_no_cls M2_v6_medium M3_v5_server; do
  docker exec -e PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True wta-ocr-model-setup python3 /experiment/run_models.py --variant "$variant" > "/root/wtaobj/ocr-experiments/model-$variant.log" 2>&1
done
