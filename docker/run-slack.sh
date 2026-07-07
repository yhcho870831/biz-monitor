#!/bin/sh
set -eu

cd /app
exec python -m app.main run-slack
