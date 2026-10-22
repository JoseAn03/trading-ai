FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADING_DEMO_MODE=true \
    TRADING_DB_PATH=/app/var/trading.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY jobs ./jobs
COPY backtests ./backtests
COPY app ./app

RUN pip install --no-cache-dir . \
    && adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/var/logs /app/var/reports \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"

CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
