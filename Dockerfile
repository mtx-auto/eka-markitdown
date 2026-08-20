FROM python:3.12-slim AS builder

# Install system dependencies needed for building markitdown[all] extensions
# (tesseract for OCR, ffmpeg for audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3-dev \
    tesseract-ocr tesseract-ocr-eng \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS server

# Install runtime system dependencies for markitdown[all]
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1001 appuser
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=appuser server.py ./

EXPOSE 8080
USER appuser
CMD ["python", "server.py"]