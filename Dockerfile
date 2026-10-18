FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Kyiv

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/__init__.py bot/__init__.py
COPY bot/services/__init__.py bot/services/dates_ocr.py bot/services/
RUN python -c "from bot.services.dates_ocr import warmup; warmup()" \
    && ls /usr/local/lib/python3.12/site-packages/rapidocr/models/ | grep -q eslav

COPY main.py .
COPY bot bot

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app/data
USER bot

ENV DATABASE_URL=sqlite+aiosqlite:////app/data/warehouse.db

CMD ["python", "main.py"]
