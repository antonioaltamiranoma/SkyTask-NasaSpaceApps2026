# Sky Task API for Cloud Run (card #3, phase A).
# The task snapshot (deploy/_snapshot: generator database + master strips) is baked into this PRIVATE image
# by deploy/deploy_api.*. It is never in git and never served as a file: the API only serves variants.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY service/requirements.txt service/requirements.txt
RUN pip install --no-cache-dir -r service/requirements.txt
COPY generator/skytask_gen generator/skytask_gen
COPY service/app service/app
COPY service/settings.cloud.toml service/settings.cloud.toml
COPY explorer explorer
COPY widget widget
COPY deploy/_snapshot data
ENV SKYTASK_SETTINGS=/app/service/settings.cloud.toml
WORKDIR /app/service
CMD exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"
