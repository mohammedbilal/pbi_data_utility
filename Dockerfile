# PBI Test Utility — single image, single port (8000).
#
# The Windows launcher runs two servers (uvicorn :8000 + Vite :5173) because Vite
# gives hot reload. In a container there is nothing to hot-reload, so the frontend
# is built once here and FastAPI serves the static bundle — one process, one port,
# no /api proxy.
#
#   docker build -t pbi-test-utility .
#   docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" pbi-test-utility
#
# Caveat: Outlook email sending (backend/engines/outlook_sender.py) is Windows-COM
# only and is unavailable in the container — the engines log "Email skipped" and
# carry on. Everything else, including expectation capture and Email Compare, works.

# ── stage 1: build the frontend ──────────────────────────────────────────────
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend
COPY backend/requirements.txt ./
# pywin32 carries a `sys_platform == "win32"` marker, so pip skips it here.
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY --from=ui /ui/dist /app/frontend/dist

# Mutable state (settings, history, expectation store) — bind-mount or it is
# lost when the container is recreated. See backend/paths.py.
ENV PBI_STATE_DIR=/app/data
VOLUME /app/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# Absolute imports (`from engines... import`) require the working directory to be
# backend/ — same constraint as the Windows manual-dev flow.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
