#!/bin/sh
set -eu

cd /app
exec python -m app.main run-web --host 0.0.0.0 --port 8080
