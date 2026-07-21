#!/bin/sh
set -eu

python manage.py migrate --noinput
exec gunicorn storyrunner.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-2}" --timeout 60
