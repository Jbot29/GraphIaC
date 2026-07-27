# Fargate runs linux/amd64. On an Apple Silicon machine, docker build
# defaults to arm64 and the task dies at startup with an exec format error
# that ECS reports as a plain "task stopped" — push.sh passes --platform.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"

WORKDIR /app
COPY main.py .

# Must match container_port in webapp.giac.
EXPOSE 8000
CMD ["python", "main.py"]
