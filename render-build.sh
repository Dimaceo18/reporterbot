#!/bin/bash

echo "🚀 Starting build..."

# Обновляем pip
pip install --upgrade pip

# Устанавливаем Pillow через бинарное колесо (без компиляции)
echo "📦 Installing Pillow as binary wheel..."
pip install --only-binary :all: Pillow==9.5.0

# Устанавливаем остальные зависимости
echo "📦 Installing other dependencies..."
pip install -r requirements.txt

echo "✅ Build completed!"
