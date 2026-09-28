# VAIIP API — root-level Dockerfile (build context = repo root).
# Render's default context resolves ./Dockerfile here; apps/api/
# Dockerfile remains for builds with context=apps/api.
FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1

COPY apps/api/pyproject.toml ./
RUN pip install --no-cache-dir .

COPY apps/api/app ./app
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/migrations ./migrations

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
