ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY resource_guard.py /opt/maas/resource_guard.py
COPY entrypoint.py /opt/maas/ovisocr3_entrypoint.py
ENTRYPOINT ["python3", "/opt/maas/ovisocr3_entrypoint.py"]
