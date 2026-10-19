# The base image is preloaded on both workers, not a published Quay repository.
FROM quay.io/cybercafe-gpu-ocr:0.2
COPY requirements-runtime-lock.txt /tmp/requirements-runtime-lock.txt
RUN pip3 install --no-cache-dir --no-deps -r /tmp/requirements-runtime-lock.txt
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
# Dataset and official pinned models are mounted separately at /experiment.
ENTRYPOINT ["python3", "/experiment/run_models.py"]
