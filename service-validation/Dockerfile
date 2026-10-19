FROM quay.io/cybercafe-gpu-ocr:0.2
COPY service-validation/requirements-service.txt /tmp/requirements-service.txt
COPY service-validation/wheels /wheels
RUN pip3 install --no-cache-dir --no-deps --no-index --find-links=/wheels -r /tmp/requirements-service.txt
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True MODEL_ROOT=/app/models PYTHONUNBUFFERED=1
COPY service-validation/engine.py service-validation/validation_server.py /app/
COPY gpu-ocr/ocr_protocol.py /app/ocr_protocol.py
COPY model-benchmark/models/PP-OCRv6_small_det_onnx /app/models/PP-OCRv6_small_det_onnx
COPY model-benchmark/models/PP-OCRv6_small_rec_onnx /app/models/PP-OCRv6_small_rec_onnx
ENTRYPOINT ["python3", "/app/validation_server.py"]
