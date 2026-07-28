FROM python:3.11

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# The container image is a production deployment. Lightsail runtime environment
# variables can still override this value when required.
ENV APP_ENV=production

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY wsgi.py .

# Secrets and deployment configuration must be provided by Lightsail environment
# variables. The root .env file is intentionally excluded by .dockerignore.
COPY meeting_assistant ./meeting_assistant
COPY static ./static
COPY templates ./templates

EXPOSE 5000

# Start Gunicorn and the durable recorder worker under one small supervisor.
CMD ["python", "-m", "meeting_assistant.run_production"]
