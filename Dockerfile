FROM python:3.10-slim

WORKDIR /app

# Устанавливаем ffmpeg и зависимости
RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем бота
COPY bot.py .

# Запускаем
CMD ["python", "bot.py"]
