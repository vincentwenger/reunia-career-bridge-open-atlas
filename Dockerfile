FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    PORT=8000

# Install dependencies before copying application code to preserve Docker layer
# caching when only Python, template, or static files change.
COPY requirements.txt ./requirements.txt
COPY products/resume_taylor/requirements.txt ./products/resume_taylor/requirements.txt
COPY products/resume_taylor/requirements-deploy.txt ./products/resume_taylor/requirements-deploy.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# Copy only the files needed by the Application Builder service. The imported
# Réunia source tree and test suites are intentionally not included.
COPY app.py ./app.py
COPY products/resume_taylor/app.py ./products/resume_taylor/app.py
COPY products/resume_taylor/resume_tailor ./products/resume_taylor/resume_tailor
COPY products/resume_taylor/templates ./products/resume_taylor/templates
COPY products/resume_taylor/static ./products/resume_taylor/static
COPY products/resume_taylor/data ./products/resume_taylor/data

RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /home/appuser appuser \
    && mkdir -p /app/instance /app/products/resume_taylor/instance \
    && chown -R appuser:appgroup /app/instance /app/products/resume_taylor/instance

USER appuser

EXPOSE 8000

# Keep one worker because the current resume workflow store is process-local.
# Threads allow overlapping requests without separating a user's workflow state.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "600", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
