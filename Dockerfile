FROM python:3.11-slim

LABEL maintainer="Harshit"
LABEL description="OCI ARM Smart Auto-Claimer"

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Environment Defaults
ENV PYTHONUNBUFFERED=1
ENV OCI_CONFIG_FILE=/app/config.ini

CMD ["python", "-u", "-m", "src.cli"]
