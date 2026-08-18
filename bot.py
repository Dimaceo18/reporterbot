# -*- coding: utf-8 -*-

import os
import re
import logging
import sys
import tempfile
import subprocess
from io import BytesIO
from typing import Optional
import traceback
import asyncio
from datetime import datetime

# ==================== АВТОУСТАНОВКА ЗАВИСИМОСТЕЙ ====================

def install_dependencies():
    """Автоматическая установка всех зависимостей"""
    deps = [
        "python-telegram-bot==20.7",
        "Pillow==10.1.0",
        "moviepy==1.0.3",
        "requests==2.31.0",
        "numpy==1.26.0",
        "ffmpeg-python==0.2.0"
    ]
    for dep in deps:
        try:
            package_name = dep.split("==")[0].replace("-", "_")
            __import__(package_name)
        except ImportError:
            print(f"📦 Устанавливаем {dep}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", dep])

install_dependencies()

# ==================== ИМПОРТЫ ====================

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler, CallbackQueryHandler

try:
    from moviepy import VideoFileClip
except ImportError:
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip
    except:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy==1.0.3"])
        from moviepy.video.io.VideoFileClip import VideoFileClip

# ==================== НАСТРОЙКИ ====================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL_ID = os.getenv("SOURCE_CHANNEL_ID")
SOURCE_CHANNEL_2_ID = os.getenv("SOURCE_CHANNEL_2_ID")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не настроен!")
if not SOURCE_CHANNEL_ID:
    raise ValueError("❌ SOURCE_CHANNEL_ID не настроен!")
if not TARGET_CHANNEL_ID:
    raise ValueError("❌ TARGET_CHANNEL_ID не настроен!")

try:
    SOURCE_CHANNEL_ID = int(SOURCE_CHANNEL_ID)
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID)
    if SOURCE_CHANNEL_2_ID:
        SOURCE_CHANNEL_2_ID = int(SOURCE_CHANNEL_2_ID)
except ValueError:
    raise ValueError("❌ ID каналов должны быть числами!")

# Стиль ЧП ВМ
TARGET_W, TARGET_H = 720, 900
CHP_GRADIENT_PCT = 0.48
MN_TITLE_ZONE_PCT = 0.23
BRIGHTNESS_FACTOR = 0.85
FONT_CHP = "Montserrat-Black.ttf"
FONT_FALLBACK = "Arial.ttf"

# ==================== ЛОГИРОВАНИЕ ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Статистика
stats = {
    "started_at": datetime.now(),
    "processed": 0,
    "errors": 0,
    "last_post": None,
    "last_error": None,
    "pending_posts": 0,
    "processing": False
}

# Хранилище для статусов постов
post_statuses = {}

# ==================== ШРИФТЫ ====================

def download_fonts():
    """Скачивание шрифтов"""
    fonts_urls = {
        "Montserrat-Black.ttf": "https://raw.githubusercontent.com/Dimaceo18/reporterbot/main/Montserrat-Black.ttf",
        "Arial.ttf": "https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial.ttf",
    }
    for font_name, url in fonts_urls.items():
        if not os.path.exists(font_name):
            try:
                logger.info(f"⬇️ Скачивание шрифта {font_name}...")
                response = requests.get(url, timeout=30)
                if response.status_code == 200:
                    with open(font_name, "wb") as f:
                        f.write(response.content)
                    logger.info(f"✅ Шрифт {font_name} скачан (размер: {len(response.content)} байт)")
                else:
                    logger.warning(f"⚠️ Не удалось скачать {font_name}, статус: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Ошибка скачивания {font_name}: {e}")
        else:
            logger.info(f"✅ Шрифт {font_name} уже есть (размер: {os.path.getsize(font_name)} байт)")

def load_font(font_name: str, size: int):
    """Загрузка шрифта с fallback (без логов)"""
    # Пробуем Montserrat
    try:
        if os.path.exists("Montserrat-Black.ttf"):
            return ImageFont.truetype("Montserrat-Black.ttf", size=size)
    except:
        pass
    
    # Пробуем Arial
    try:
        if os.path.exists("Arial.ttf"):
            return ImageFont.truetype("Arial.ttf", size=size)
    except:
        pass
    
    # Пробуем системные шрифты
    system_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
    ]
    
    for font_path in system_fonts:
        try:
            return ImageFont.truetype(font_path, size=size)
        except:
            pass
    
    return ImageFont.load_default()

# ==================== ОБРАБОТКА ИЗОБРАЖЕНИЙ ====================

def crop_to_ratio(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    w, h = img.size
    target_ratio = target_w / target_h
    cur_ratio = w / h
    
    if cur_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

def apply_bottom_gradient(img: Image.Image, height_pct: float, max_alpha: int = 220) -> Image.Image:
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh):
        a = int(max_alpha * (y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, h - gh))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def text_width(draw, s: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), s, font=font)
        return bbox[2] - bbox[0]
    except:
        return len(s) * font.size // 2

def wrap_text(draw, text: str, font, max_width: int, max_lines: int = 6):
    words = text.split()
    if not words:
        return [""], True
    
    lines = []
    current = words[0]
    for word in words[1:]:
        test = current + " " + word
        if text_width(draw, test, font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                return lines, False
    lines.append(current)
    return lines, True

def fit_text_block(draw, text: str, safe_w: int, max_block_h: int,
                   max_lines: int = 6, start_size: int = 90, min_size: int = 16):
    """Подбор размера шрифта с защитой от зацикливания"""
    text = (text or "").strip()
    if not text:
        text = " "
    
    # Ограничиваем длину текста
    if len(text) > 300:
        text = text[:297] + "..."
    
    size = start_size
    attempts = 0
    while size >= min_size and attempts < 30:
        attempts += 1
        font = load_font(FONT_CHP, size)
        lines, ok = wrap_text(draw, text, font, safe_w, max_lines=max_lines)
        spacing = int(size * 0.22)
        heights = []
        total_h = 0
        max_w = 0
        for ln in lines:
            try:
                bb = draw.textbbox((0, 0), ln, font=font)
                lw = bb[2] - bb[0]
                lh = bb[3] - bb[1]
            except:
                lw = len(ln) * size // 2
                lh = size
            heights.append(lh)
            total_h += lh
            max_w = max(max_w, lw)
        total_h += spacing * (len(lines) - 1)
        
        if ok and max_w <= safe_w and total_h <= max_block_h:
            return font, lines, heights, spacing, total_h
        
        size -= 2
    
    # Если ничего не подошло - минимальный размер
    font = load_font(FONT_CHP, min_size)
    lines, _ = wrap_text(draw, text, font, safe_w, max_lines=max_lines)
    spacing = int(min_size * 0.22)
    heights = []
    total_h = 0
    for ln in lines:
        try:
            bb = draw.textbbox((0, 0), ln, font=font)
            lh = bb[3] - bb[1]
        except:
            lh = min_size
        heights.append(lh)
        total_h += lh
    total_h += spacing * (len(lines) - 1)
    return font, lines, heights, spacing, total_h

def clean_title_for_card(title: str) -> str:
    if not title:
        return ""
    
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\u2600-\u27BF"
        "]+",
        flags=re.UNICODE
    )
    clean = emoji_pattern.sub('', title)
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()

def extract_title_from_text(text: str) -> str:
    if not text:
        return ""
    
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\u2600-\u27BF"
        "]+",
        flags=re.UNICODE
    )
    clean_text = emoji_pattern.sub('', text).strip()
    
    if '\n' in clean_text:
        lines = clean_text.split('\n')
        title = lines[0].strip()
        if len(title) > 300:
            title = title[:297] + "..."
        return title
    
    if '. ' in clean_text and len(clean_text) > 100:
        parts = clean_text.split('. ', 1)
        title = (parts[0] + '.').strip()
        if len(title) > 300:
            title = title[:297] + "..."
        return title
    
    if len(clean_text) > 300:
        return clean_text[:297] + "..."
    return clean_text

def process_image(img: Image.Image, title_text: str) -> Image.Image:
    try:
        img = crop_to_ratio(img, TARGET_W, TARGET_H)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        draw = ImageDraw.Draw(img)
        margin_x = int(img.width * 0.06)
        margin_bottom = int(img.height * 0.08)
        safe_w = img.width - 2 * margin_x
        title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
        
        clean_title = clean_title_for_card(title_text)
        text = (clean_title or "Без заголовка").strip().upper()
        
        font, lines, heights, spacing, total_h = fit_text_block(
            draw=draw, text=text, safe_w=safe_w,
            max_block_h=title_max_h, max_lines=6,
            start_size=int(img.height * 0.11), min_size=16
        )
        
        line_height = font.size
        total_text_height = len(lines) * line_height + (len(lines) - 1) * 2
        y = img.height - margin_bottom - total_text_height
        
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill="white")
            y += line_height + 2
        
        return img
    except Exception as e:
        logger.error(f"❌ Ошибка обработки изображения: {e}")
        return img

def process_photo_bytes(photo_bytes: bytes, title_text: str) -> BytesIO:
    try:
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        img = process_image(img, title_text)
        output = BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {e}")
        return BytesIO(photo_bytes)

# ==================== ОБРАБОТКА ВИДЕО ====================

def process_video_frame(frame: np.ndarray, title_text: str) -> np.ndarray:
    try:
        img = Image.fromarray(frame).convert("RGB")
        img = process_image(img, title_text)
        return np.array(img)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки кадра: {e}")
        return frame

def process_video_bytes(video_bytes: bytes, title_text: str) -> BytesIO:
    temp_input = None
    temp_output = None
    
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(video_bytes)
            temp_input = f.name
        
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            temp_output = f.name
        
        logger.info(f"📹 Загрузка видео...")
        video = VideoFileClip(temp_input)
        logger.info(f"📹 Видео загружено: {video.duration}с, {video.size}")
        
        def process_frame(frame):
            return process_video_frame(frame, title_text)
        
        processed_video = video.fl_image(process_frame)
        
        if video.audio is not None:
            try:
                processed_video = processed_video.set_audio(video.audio)
                logger.info(f"✅ Оригинальное аудио сохранено")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения аудио: {e}")
        
        processed_video.write_videofile(
            temp_output,
            codec='libx264',
            audio_codec='aac',
            fps=video.fps,
            bitrate='5000k',
            threads=4,
            preset='medium',
            logger=None
        )
        
        video.close()
        processed_video.close()
        
        with open(temp_output, 'rb') as f:
            result_bytes = f.read()
        
        logger.info(f"✅ Видео обработано! Размер: {len(result_bytes) / (1024*1024):.2f} MB")
        
        output = BytesIO()
        output.write(result_bytes)
        output.seek(0)
        return output
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке видео: {e}")
        traceback.print_exc()
        output = BytesIO(video_bytes)
        output.seek(0)
        return output
    
    finally:
        try:
            if temp_input and os.path.exists(temp_input):
                os.unlink(temp_input)
            if temp_output and os.path.exists(temp_output):
                os.unlink(temp_output)
        except:
            pass

# ==================== СКАЧИВАНИЕ МЕДИА ====================

async def download_media(bot: Bot, file_id: str) -> Optional[bytes]:
    try:
        file = await bot.get_file(file_id)
        result = await file.download_as_bytearray()
        return bytes(result)
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания: {e}")
        return None

def get_text_from_message(message) -> str:
    return message.text or message.caption or ""

# ==================== ФУНКЦИИ СТАТУСОВ ====================

async def send_status_notification(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str, post_id: str = None):
    """Отправка уведомления о статусе обработки"""
    status_messages = {
        "received": "📥 Пост получен! Начинаю обработку...",
        "downloading": "⬇️ Скачиваю медиафайлы...",
        "processing": "🔄 Обрабатываю контент... (это может занять некоторое время)",
        "ready": "✅ Готово! Отправляю в канал...",
        "sent": "📤 Пост успешно опубликован!",
        "error": "❌ Произошла ошибка при обработке"
    }
    
    try:
        if update and update.message and update.message.from_user:
            await update.message.reply_text(
                f"{status_messages.get(status, status)}",
                parse_mode="HTML"
            )
        
        if post_id:
            post_statuses[post_id] = {
                "status": status,
                "timestamp": datetime.now(),
                "message": status_messages.get(status, status)
            }
    except Exception as e:
        logger.error(f"❌ Ошибка отправки статуса: {e}")

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now() - stats['started_at']
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60
    
    await update.message.reply_text(
        f"🤖 <b>Бот для репоста с оформлением ЧП ВМ</b>\n\n"
        f"📢 Каналы-источники:\n"
        f"  • <code>{SOURCE_CHANNEL_ID}</code>\n"
        f"  • <code>{SOURCE_CHANNEL_2_ID or 'не указан'}</code>\n"
        f"📢 Целевой канал: <code>{TARGET_CHANNEL_ID}</code>\n"
        f"📊 Обработано: {stats['processed']}\n"
        f"❌ Ошибок: {stats['errors']}\n"
        f"⏱ Работает: {hours}ч {minutes}м\n"
        f"📌 Последний пост: {stats['last_post'] or 'нет'}\n"
        f"🔄 Ожидает обработки: {stats['pending_posts']}\n\n"
        f"💡 <b>Как использовать:</b>\n"
        f"• Отправьте боту пост (фото/видео/текст) - он обработает и опубликует\n"
        f"• Посты из каналов-источников обрабатываются автоматически\n"
        f"• Команда /stats - статистика\n"
        f"• Команда /test - проверка подключения\n"
        f"• Команда /status - текущие статусы постов\n\n"
        f"✅ <b>Бот работает!</b>",
        parse_mode="HTML"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now() - stats['started_at']
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60
    
    await update.message.reply_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"⏱ <b>Время работы:</b> {hours}ч {minutes}м\n"
        f"📨 <b>Обработано постов:</b> {stats['processed']}\n"
        f"❌ <b>Ошибок:</b> {stats['errors']}\n"
        f"🔄 <b>В очереди:</b> {stats['pending_posts']}\n"
        f"⚙️ <b>Статус:</b> {'🔴 Обработка...' if stats['processing'] else '🟢 Ожидание'}\n"
        f"📅 <b>Запущен:</b> {stats['started_at'].strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"📌 <b>Последний пост:</b> {stats['last_post'] or 'нет'}\n"
        f"📢 <b>Каналы-источники:</b>\n"
        f"  • <code>{SOURCE_CHANNEL_ID}</code>\n"
        f"  • <code>{SOURCE_CHANNEL_2_ID or 'не указан'}</code>\n"
        f"📢 <b>Целевой канал:</b> <code>{TARGET_CHANNEL_ID}</code>\n"
        f"🐍 <b>Python:</b> {sys.version.split()[0]}\n\n"
        f"✅ <b>Бот работает</b> 🟢",
        parse_mode="HTML"
    )

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки подключения к каналам"""
    try:
        bot = context.bot
        
        source_statuses = []
        for idx, channel_id in enumerate([SOURCE_CHANNEL_ID, SOURCE_CHANNEL_2_ID], 1):
            if not channel_id:
                source_statuses.append(f"⚠️ Канал {idx} не указан")
                continue
            try:
                source = await bot.get_chat(channel_id)
                source_statuses.append(f"✅ {source.title} (ID: {channel_id})")
            except Exception as e:
                source_statuses.append(f"❌ Ошибка: {e}")
        
        try:
            target = await bot.get_chat(TARGET_CHANNEL_ID)
            target_status = f"✅ {target.title} (ID: {TARGET_CHANNEL_ID})"
        except Exception as e:
            target_status = f"❌ Ошибка: {e}"
        
        me = await bot.get_me()
        
        await update.message.reply_text(
            f"🔍 <b>Проверка подключения</b>\n\n"
            f"🤖 <b>Бот:</b> @{me.username}\n"
            f"📢 <b>Канал-источник 1:</b> {source_statuses[0]}\n"
            f"📢 <b>Канал-источник 2:</b> {source_statuses[1] if len(source_statuses) > 1 else '⚠️ Не указан'}\n"
            f"📢 <b>Целевой канал:</b> {target_status}\n"
            f"📊 <b>Обработано:</b> {stats['processed']}\n"
            f"❌ <b>Ошибок:</b> {stats['errors']}\n"
            f"🔄 <b>В очереди:</b> {stats['pending_posts']}\n\n"
            f"🔄 <b>Статус:</b> {'✅ Все работает' if stats['processed'] > 0 else '⏳ Ожидание постов'}",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка проверки: {e}")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not post_statuses:
        await update.message.reply_text("📭 Нет постов в обработке")
        return
    
    status_text = "📊 <b>Статусы постов:</b>\n\n"
    for post_id, data in list(post_statuses.items())[-10:]:
        status_text += f"🆔 {post_id[:8]}...\n"
        status_text += f"📌 Статус: {data['message']}\n"
        status_text += f"⏱ {data['timestamp'].strftime('%H:%M:%S')}\n\n"
    
    await update.message.reply_text(status_text, parse_mode="HTML")

# ==================== ОБРАБОТКА ПОСТОВ ====================

async def process_post(message, context: ContextTypes.DEFAULT_TYPE, source: str = "channel"):
    try:
        post_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{stats['processed']}"
        
        stats['pending_posts'] += 1
        stats['processing'] = True
        
        current_update = context.user_data.get('current_update') if source == "user" else None
        
        if source == "user" and current_update:
            try:
                await current_update.message.reply_text("📥 Пост получен! Начинаю обработку...")
            except:
                pass
        
        text = get_text_from_message(message)
        title = extract_title_from_text(text)
        
        logger.info(f"📝 Заголовок: {title[:50] if title else 'нет'}")
        
        if source == "user" and current_update:
            try:
                await current_update.message.reply_text("⬇️ Скачиваю медиафайлы...")
            except:
                pass
        
        # Обработка фото
        if hasattr(message, 'photo') and message.photo:
            logger.info(f"📸 Обработка фото")
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("🔄 Обрабатываю изображение...")
                except:
                    pass
            
            photo = message.photo[-1]
            photo_bytes = await download_media(context.bot, photo.file_id)
            
            if not photo_bytes:
                logger.error("❌ Не удалось скачать фото")
                stats['errors'] += 1
                if source == "user" and current_update:
                    try:
                        await current_update.message.reply_text("❌ Не удалось скачать фото")
                    except:
                        pass
                return
            
            processed = process_photo_bytes(photo_bytes, title)
            caption = text[:1024] if text else ""
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("✅ Готово! Отправляю в канал...")
                except:
                    pass
            
            await context.bot.send_photo(
                chat_id=TARGET_CHANNEL_ID,
                photo=BytesIO(processed.getvalue()),
                caption=caption,
                parse_mode="HTML"
            )
            stats['processed'] += 1
            stats['last_post'] = f"Фото в {datetime.now().strftime('%H:%M:%S')}"
            logger.info(f"✅ Фото отправлено в канал {TARGET_CHANNEL_ID}")
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("📤 Пост успешно опубликован!")
                except:
                    pass
            
            return
        
        # Обработка видео
        if hasattr(message, 'video') and message.video:
            logger.info(f"📹 Обработка видео")
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("🔄 Обрабатываю видео... (это может занять несколько минут)")
                except:
                    pass
            
            video_bytes = await download_media(context.bot, message.video.file_id)
            
            if not video_bytes:
                logger.error("❌ Не удалось скачать видео")
                stats['errors'] += 1
                if source == "user" and current_update:
                    try:
                        await current_update.message.reply_text("❌ Не удалось скачать видео")
                    except:
                        pass
                return
            
            processed = process_video_bytes(video_bytes, title)
            caption = text[:1024] if text else ""
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("✅ Готово! Отправляю в канал...")
                except:
                    pass
            
            await context.bot.send_video(
                chat_id=TARGET_CHANNEL_ID,
                video=BytesIO(processed.getvalue()),
                caption=caption,
                parse_mode="HTML",
                width=TARGET_W,
                height=TARGET_H
            )
            stats['processed'] += 1
            stats['last_post'] = f"Видео в {datetime.now().strftime('%H:%M:%S')}"
            logger.info(f"✅ Видео отправлено в канал {TARGET_CHANNEL_ID}")
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("📤 Пост успешно опубликован!")
                except:
                    pass
            
            return
        
        # Текстовый пост
        if text:
            logger.info(f"📝 Текстовый пост")
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("📝 Отправляю текстовый пост...")
                except:
                    pass
            
            await context.bot.send_message(
                chat_id=TARGET_CHANNEL_ID,
                text=text,
                parse_mode="HTML"
            )
            stats['processed'] += 1
            stats['last_post'] = f"Текст в {datetime.now().strftime('%H:%M:%S')}"
            logger.info(f"✅ Текст отправлен в канал {TARGET_CHANNEL_ID}")
            
            if source == "user" and current_update:
                try:
                    await current_update.message.reply_text("📤 Пост успешно опубликован!")
                except:
                    pass
            
            return
        
        logger.info("ℹ️ Пост пустой, пропускаем")
        if source == "user" and current_update:
            try:
                await current_update.message.reply_text("⚠️ Пост пустой, пропускаю")
            except:
                pass
        
    except Exception as e:
        stats['errors'] += 1
        stats['last_error'] = str(e)
        logger.error(f"❌ Ошибка обработки поста: {e}")
        traceback.print_exc()
        
        if source == "user" and current_update:
            try:
                await current_update.message.reply_text(f"❌ Ошибка при обработке: {str(e)[:200]}")
            except:
                pass
    finally:
        stats['pending_posts'] -= 1
        if stats['pending_posts'] < 0:
            stats['pending_posts'] = 0
        stats['processing'] = stats['pending_posts'] > 0

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        logger.info("❌ Нет сообщения в update")
        return
    
    if message.chat.id not in [SOURCE_CHANNEL_ID, SOURCE_CHANNEL_2_ID]:
        logger.info(f"⏭️ Пропускаем: канал {message.chat.id} не в списке источников")
        return
    
    has_content = (
        (hasattr(message, 'photo') and bool(message.photo)) or
        (hasattr(message, 'video') and bool(message.video)) or
        bool(get_text_from_message(message))
    )
    
    if not has_content:
        logger.info("ℹ️ Пост без контента, пропускаем")
        return
    
    logger.info(f"📨 Новый пост в канале {message.chat.id} ({message.chat.title})")
    stats['pending_posts'] += 1
    
    await process_post(message, context, source="channel")

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    
    if message.text and message.text.startswith('/'):
        return
    
    has_content = (
        (hasattr(message, 'photo') and bool(message.photo)) or
        (hasattr(message, 'video') and bool(message.video)) or
        bool(get_text_from_message(message))
    )
    
    if not has_content:
        await message.reply_text("📭 Отправьте мне фото, видео или текст для обработки и публикации.")
        return
    
    context.user_data['current_update'] = update
    
    await message.reply_text("📥 Пост получен! Начинаю обработку...")
    
    logger.info(f"📨 Новый пост от пользователя {message.from_user.id}")
    stats['pending_posts'] += 1
    
    await process_post(message, context, source="user")

# ==================== ЗАПУСК ====================

async def main():
    logger.info("🚀 Бот для репоста с оформлением ЧП ВМ запускается...")
    logger.info(f"📊 Версия с поддержкой статусов и несколькими источниками")
    
    download_fonts()
    
    app = Application.builder().token(BOT_TOKEN).build()
    bot = Bot(token=BOT_TOKEN)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка удаления webhook: {e}")
    
    source_channels = [SOURCE_CHANNEL_ID]
    if SOURCE_CHANNEL_2_ID:
        source_channels.append(SOURCE_CHANNEL_2_ID)
    
    for idx, channel_id in enumerate(source_channels, 1):
        try:
            source = await bot.get_chat(channel_id)
            logger.info(f"✅ Канал-источник {idx}: {source.title} (ID: {channel_id})")
        except Exception as e:
            logger.error(f"❌ Ошибка доступа к каналу-источнику {idx}: {e}")
            logger.info("💡 Убедитесь, что бот добавлен в канал как администратор")
            return
    
    try:
        target = await bot.get_chat(TARGET_CHANNEL_ID)
        logger.info(f"✅ Целевой канал: {target.title} (ID: {TARGET_CHANNEL_ID})")
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к целевому каналу: {e}")
        logger.info("💡 Убедитесь, что бот добавлен в канал как администратор")
        return
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("status", status_command))
    
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=source_channels),
        handle_channel_post
    ))
    
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_user_message
    ))
    
    logger.info("✅ Обработчики зарегистрированы")
    logger.info(f"📊 Параметры оформления (ЧП ВМ):")
    logger.info(f"  • Размер: {TARGET_W}x{TARGET_H}")
    logger.info(f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}%")
    logger.info(f"  • Затемнение: {int(BRIGHTNESS_FACTOR*100)}%")
    logger.info(f"📊 Количество каналов-источников: {len(source_channels)}")
    
    await app.initialize()
    await app.start()
    
    await app.updater.start_polling(
        allowed_updates=["channel_post", "message"],
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30
    )
    
    logger.info("🟢 Бот запущен и слушает каналы!")
    logger.info("📨 Отправьте пост в канал-источник или репостните боту для теста")
    logger.info("💡 Команды для проверки: /start, /stats, /test, /status")
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        sys.exit(1)
