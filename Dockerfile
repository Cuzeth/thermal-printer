FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libusb-1.0 for python-escpos USB transport
# fonts-dejavu for the Pillow-based renderer (no macOS fonts in here)
# libjpeg/zlib for Pillow's image decoders
RUN apt-get update && apt-get install -y --no-install-recommends \
        libusb-1.0-0 \
        fonts-dejavu-core \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 5005

# 1 worker keeps SQLite + the printer USB lock single-process. 4 threads is
# plenty for this app's traffic.
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5005", "--access-logfile", "-", "app:app"]
