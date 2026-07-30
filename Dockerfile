FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    PORT=8000

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# Copy the merged application: shared Career Bridge definitions, the Réunia
# shell, and the Resume Taylor Application Builder.
COPY app.py ./app.py
COPY career_bridge ./career_bridge
COPY products/reunia/meeting_assistant ./products/reunia/meeting_assistant
COPY products/reunia/templates ./products/reunia/templates
COPY products/reunia/static ./products/reunia/static
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

# Deployment invariant: Lightsail must not override this image command.
# One worker is required because the current resume workflow store is
# process-local. Four threads allow concurrent requests without splitting state.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "600", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
