# The API: FastAPI over data/events.json. The scrapers run in GitHub Actions
# (and scripts/weekly_local_scrape.sh), never in this container, so the image
# leaves out Playwright and the test tools.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN grep -vE '^(playwright|pytest|flake8|httpx)\b' requirements.txt > /tmp/requirements-api.txt \
    && pip install -r /tmp/requirements-api.txt

COPY . .

# Railway and most hosts set PORT; 8000 otherwise.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
