FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt requests

COPY app.py bambu_mqtt.py filament_tracker.py get_credentials.py ./
COPY api/ api/
COPY core/ core/
COPY models/ models/
COPY repositories/ repositories/
COPY services/ services/
COPY templates/ templates/
COPY static/ static/
COPY config.example.py .

RUN useradd -m -u 1000 app

ENV FILAMENT_TRACKER_DATA_DIR=/app/data
ENV APP_PORT=5000
ENV APP_HOST=0.0.0.0
ENV AUTH_ENABLED=1
RUN mkdir -p /app/data /app/config && chown app:app /app/data /app/config
VOLUME /app/data
VOLUME /app/config

USER app

EXPOSE 5000

CMD ["sh", "-c", "python3 filament_tracker.py --host ${APP_HOST} --port ${APP_PORT}"]
