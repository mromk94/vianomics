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
# One image, two roles: cron jobs (Render's Docker runtime offers no
# command override — it runs CMD) set CRON_JOB=<job_key> in env and the
# image runs the trigger and exits; unset → normal API server boot.
CMD ["sh", "-c", "if [ -n \"$CRON_JOB\" ]; then exec python -m app.cron_trigger \"$CRON_JOB\"; else exec sh -c 'alembic upgrade head && python -m app.seeds.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'; fi"]
