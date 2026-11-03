FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 TZ=Europe/Paris
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY dashboard_server.py pause_server.py ./
COPY static/ static/

USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["python", "-I", "/app/dashboard_server.py"]
