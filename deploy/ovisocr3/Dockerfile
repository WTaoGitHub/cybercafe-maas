ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY resource_guard.py /opt/maas/resource_guard.py
COPY entrypoint.py /opt/maas/ovisocr3_entrypoint.py
COPY execution_guard.py vllm_guard.py ovis_scheduler.py /opt/maas/
ENTRYPOINT ["python3", "/opt/maas/ovisocr3_entrypoint.py"]
