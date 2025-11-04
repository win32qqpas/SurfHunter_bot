# Dockerfile — Poseidon v7
FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем приложение
COPY app.py .

# Окружение
ENV PORT=10000
ENV PYTHONUNBUFFERED=1

# Запуск FastAPI через Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]