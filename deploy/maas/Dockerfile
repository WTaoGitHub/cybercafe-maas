FROM python:3.11-slim
WORKDIR /app
COPY requirements-maas.txt requirements-maas-tested-lock.txt /app/
RUN pip install --no-cache-dir -r requirements-maas.txt -c requirements-maas-tested-lock.txt
COPY maas_platform /app/maas_platform
COPY plugins/gpustack-replica/maas_replica_policy.py \
     plugins/gpustack-replica/maas_replica_planner.py \
     plugins/gpustack-replica/maas_replica_pressure.py /app/
RUN useradd --uid 10001 --create-home maas && mkdir -p /var/lib/maas/artifacts && chown -R maas /var/lib/maas
USER 10001
CMD ["uvicorn", "maas_platform.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
