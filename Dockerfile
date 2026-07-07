FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY docker /app/docker
COPY tests /app/tests
COPY seeds /app/seeds
COPY README.md /app/README.md
COPY .env.example /app/.env.example

RUN chmod +x /app/docker/run-slack.sh /app/docker/run-scheduler.sh /app/docker/run-web.sh /app/docker/run-worker.sh

CMD ["python", "-m", "app.main", "show-config"]
