FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    WORKSPACE=/data

# serif font for the plots (Times New Roman is not available on Linux)
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY macrospin ./macrospin
COPY web ./web
COPY examples ./examples
COPY evals ./evals

RUN useradd --create-home --uid 1000 sim \
 && mkdir -p /data && chown -R sim:sim /data /app
USER sim

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/config')"

# one worker: the run queue lives in this process
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
