# Pipeline 2 image: same OCR setup as container/ocr/Dockerfile + Qwen field extraction (pipeline2/).
# Build from the repo root (models must be copied first):
#   python scripts/copy_models.py
#   docker build -f container/qwen/Dockerfile -t bill-ocr-qwen .
FROM python:3.11.9-slim-bookworm

# libgl1 / libglib2.0-0: opencv-contrib-python, libgomp1: paddlepaddle
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /var/task

# paddlepaddle 3.4.0 for Linux is only on Paddle's own index, everything else from PyPI
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt awslambdaric==4.1.0 boto3==1.40.40 \
        --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/

# the exact model files the local tests ran with
COPY models/ /opt/models/

COPY billocr/ billocr/
COPY pipeline2/ pipeline2/
COPY container/qwen/app.py .

# Lambda can only write to /tmp; models are read from the image, nothing is downloaded
ENV BILLOCR_MODEL_DIR=/opt/models \
    PADDLE_PDX_CACHE_HOME=/tmp/paddlex \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    HOME=/tmp \
    PYTHONUNBUFFERED=1

# load the models once at build time so a broken image fails here, not on the first upload
RUN python -c "from billocr.ocr import get_engine; get_engine(); import pipeline2.qwen_extract; print('models OK')"

ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["app.handler"]
