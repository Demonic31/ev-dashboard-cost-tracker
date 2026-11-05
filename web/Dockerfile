# Image du dashboard (et de la page « PREPROD en pause »). Contexte de build : racine du dépôt (voir .dockerignore),
# pour embarquer les sources de données des marques (brands/<marque>/source.py).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 TZ=Europe/Paris
WORKDIR /app
COPY web/requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY web/dashboard_server.py web/pause_server.py web/vehicle_source.py web/VERSION ./
COPY web/static/ static/
COPY CHANGELOG.md ./
COPY brands/ brands/
# Données gardées entre deux redémarrages (prix passés du carburant), sur un volume Docker
RUN mkdir /data && chown 1000:1000 /data
ENV DATA_DIR=/data

USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["python", "-I", "/app/dashboard_server.py"]
