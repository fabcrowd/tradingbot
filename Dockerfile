FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend-new/package.json frontend-new/package-lock.json ./
RUN npm ci
COPY frontend-new/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY config.toml /app/config.toml

COPY --from=frontend-build /build/dist /app/frontend

EXPOSE 8080

CMD ["python", "-m", "backend.server.main"]
