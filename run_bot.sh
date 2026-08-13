#!/bin/bash

# Убиваем старые процессы
echo "Останавливаем старые процессы..."
pkill -f "python.*bot.py" || true
sleep 2

# Запускаем бота
echo "Запускаем бота..."
python3 bot.py
