# ---------- build the web UI ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- python runtime ----------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
# version shown in the app and checked by the deploy (deploy/docker-deploy.sh passes these)
ARG GIT_SHA=""
ARG GIT_SUBJECT=""
RUN if [ -n "$GIT_SHA" ]; then python -c "import json, os; sha = os.environ['GIT_SHA']; \
json.dump({'sha': sha, 'short': sha[:7], 'subject': os.environ.get('GIT_SUBJECT', '')}, \
open('/app/VERSION.json', 'w'), ensure_ascii=False)"; fi
WORKDIR /app/backend
EXPOSE 8000
VOLUME ["/app/data"]
CMD ["python", "-m", "app.main"]
