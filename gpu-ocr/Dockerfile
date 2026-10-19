FROM nvidia/cuda:12.1.0-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir 'numpy<2' opencv-python==4.11.0.86 rapidocr-onnxruntime==1.4.4 fastapi==0.115.12 uvicorn==0.34.2 pillow && pip3 uninstall -y onnxruntime && pip3 install --no-cache-dir onnxruntime-gpu==1.20.2 nvidia-cudnn-cu12==9.1.0.70 nvidia-cublas-cu12==12.1.3.1 nvidia-cuda-nvrtc-cu12==12.1.105
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib:/usr/local/cuda/lib64
WORKDIR /app
COPY server.py /app/server.py
COPY ocr_protocol.py /app/ocr_protocol.py
ENTRYPOINT ["python3", "/app/server.py"]
