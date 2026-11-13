# Файл: Dockerfile
# Назначение: образ для запуска бота на сервере. База данных лежит в папке /data,
# к ней подключается том, чтобы данные пережили пересоздание контейнера.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

ENV DB_PATH=/data/homework.db
VOLUME /data

CMD ["python", "-m", "bot"]
