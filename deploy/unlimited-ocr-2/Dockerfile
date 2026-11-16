ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN timeout 300 apt-get update && timeout 600 apt-get install -y --no-install-recommends python3 python3-pip ca-certificates && rm -rf /var/lib/apt/lists/*
COPY sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl /tmp/sglang.whl
COPY requirements.txt /tmp/requirements.txt
RUN echo '576c71dab3cacad843f6870739494e13b2e8169a69cc038f84395d6503e801ea  /tmp/sglang.whl' | sha256sum -c - && mv /tmp/sglang.whl /tmp/sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl && timeout 3600 python3 -m pip install --timeout 60 --retries 1 -r /tmp/requirements.txt /tmp/sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl && python3 -m pip check && rm /tmp/sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl
COPY patch_model.py /tmp/patch_model.py
RUN python3 /tmp/patch_model.py && rm /tmp/patch_model.py && python3 -c 'import torch,torchaudio,transformers,flashinfer; import sglang.srt.models.unlimited_ocr_v2; print(torch.__version__,torchaudio.__version__,transformers.__version__,flashinfer.__version__)'
