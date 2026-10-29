FROM node:22-bookworm-slim AS frontend
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG DOCSCAN_FRONTEND_REF=6395ec99565b933191aa5faca53e4a8e7c01e05d
WORKDIR /build/frontend
RUN test "${#DOCSCAN_FRONTEND_REF}" = 40 \
    && git init \
    && git remote add origin https://github.com/daikhoa/FE-scantailieu.git \
    && git -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=60 fetch --depth 1 origin "$DOCSCAN_FRONTEND_REF" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$DOCSCAN_FRONTEND_REF"
RUN npm install --global npm@10.9.8 && npm ci && npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DOCSCAN_FRONTEND_DIR=/app/frontend DOCSCAN_DATA_DIR=/tmp/docscan PORT=10000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 docscan
COPY app ./app
COPY --from=frontend /build/frontend/dist ./frontend
USER docscan
EXPOSE 10000
HEALTHCHECK --interval=5s --timeout=5s --start-period=10s --retries=6 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','10000')+'/api/v1/health',timeout=3).close()"
CMD ["python", "-m", "app.serve"]
