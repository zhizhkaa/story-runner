FROM node:22-alpine AS assets

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
COPY stories/templates ./stories/templates
COPY stories/static/stories/app.js ./stories/static/stories/app.js
RUN npm run css:build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /app/stories/static/stories/styles.css /app/stories/static/stories/styles.css
RUN mkdir -p /app/data /app/staticfiles && \
    chmod +x /app/entrypoint.sh && \
    DEBUG=0 SECRET_KEY=build-only-secret ADMIN_PASSWORD=build-only-password \
    python manage.py collectstatic --noinput

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
