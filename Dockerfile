FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Непривилегированный пользователь; instance/ — том с SQLite-базой
RUN useradd --create-home appuser && \
    mkdir -p /app/instance && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --preload: приложение (и автомиграции БД) инициализируется один раз
# в мастер-процессе, воркеры получают готовую копию — без гонок на миграциях.
CMD ["gunicorn", "--preload", "--workers", "2", "--bind", "0.0.0.0:8000", "app:app"]
