# Stage 1 — build React frontend
FROM node:20-slim AS react-builder
WORKDIR /build
COPY src/web_gateway/react/package*.json ./
RUN npm ci --silent
COPY src/web_gateway/react/ ./
RUN npm run build

# Stage 2 — Python runtime
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Embed compiled React assets (served by react_router.py)
COPY --from=react-builder /build/dist ./src/web_gateway/react/dist/

ENV PYTHONPATH=/app/src

# Default: web server — overridden per-service in docker-compose
CMD ["python3", "-m", "uvicorn", "web.react_router:app", "--host", "0.0.0.0", "--port", "5000"]
