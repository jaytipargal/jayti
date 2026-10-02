FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/tmp/hf-cache

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY adapter.tar.gz /tmp/adapter.tar.gz
RUN mkdir -p /app/adapter \
    && tar -xzf /tmp/adapter.tar.gz -C /app/adapter \
    && rm /tmp/adapter.tar.gz

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
