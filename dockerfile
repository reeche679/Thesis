# Build from repo root: docker build -f dockerfile -t thesis-app .
# Prefer renaming this file to "Dockerfile" so `docker build .` picks it up by default.

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/scripts:${PATH}"

# Runtime libs for Pillow, pdf2image, pytesseract, EasyOCR/OpenCV, ortools
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch first — avoids multi-GB NVIDIA CUDA wheels (common on small VMs / VirtualBox).
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY Solver-Side/ /app/Solver-Side/
COPY Django-Back-Front/ /app/Django-Back-Front/

WORKDIR /app/Django-Back-Front

COPY scripts/ /scripts/
# Windows checkouts may use CRLF; strip \r so the shebang is valid in Linux
RUN sed -i 's/\r$//' /scripts/*.sh && chmod +x /scripts/*

RUN useradd --create-home --no-log-init appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["/scripts/entrypoint.sh"]
