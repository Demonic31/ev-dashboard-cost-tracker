FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 TZ=Europe/Paris
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY psacc_sheets_sync.py .

# Utilisateur non root ; la config psacc est montée en lecture seule
USER 1000:1000
ENTRYPOINT ["python", "-I", "/app/psacc_sheets_sync.py"]
