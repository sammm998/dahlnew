# SmartHangers – container för Railway (eller annan Docker-värd)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SMARTHANGERS_DATA=/data

WORKDIR /app

# Installera beroenden först (bättre cache), sedan koden
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/smarthangers backend/smarthangers
RUN pip install "./backend[postgres]"

RUN mkdir -p /data
EXPOSE 8000

# Railway sätter $PORT. Sätt SMARTHANGERS_SEED_DEMO=1 för att skapa demoprojektet vid start.
CMD ["sh", "-c", "if [ \"$SMARTHANGERS_SEED_DEMO\" = \"1\" ]; then python -m smarthangers.demo; fi; exec python -m smarthangers --host 0.0.0.0 --port ${PORT:-8000}"]
