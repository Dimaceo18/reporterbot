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
from telegram import Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

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
except ValueError:
    raise ValueError("❌ SOURCE_CHANNEL_ID и TARGET_CHANNEL_ID должны быть числами!")

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
    "last_error": None
}

# ==================== ШРИФТЫ ====================

def download_fonts():
    fonts_urls = {
        "Montserrat-Black.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Black.ttf",
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
                    logger.info(f"✅ Шрифт {font_name} скачан")
            except Exception as e:
                logger.error(f"❌ Ошибка скачивания {font_name}: {e}")

def load_font(font_name: str, size: int):
    try:
        return ImageFont.truetype(font_name, size=size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_FALLBACK, size=size)
        except:
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
    text = (text or "").strip()
    if not text:
        text = " "
    
    size = start_size
    while size >= min_size:
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
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    if '. ' in clean_text and len(clean_text) > 100:
        parts = clean_text.split('. ', 1)
        title = (parts[0] + '.').strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    if len(clean_text) > 200:
        return clean_text[:197] + "..."
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

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now() - stats['started_at']
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60
    
    await update.message.reply_text(
        f"🤖 <b>Бот для репоста с оформлением ЧП ВМ</b>\n\n"
        f"📢 Канал-источник: <code>{SOURCE_CHANNEL_ID}</code>\n"
        f"📢 Целевой канал: <code>{TARGET_CHANNEL_ID}</code>\n"
        f"📊 Обработано: {stats['processed']}\n"
        f"❌ Ошибок: {stats['errors']}\n"
        f"⏱ Работает: {hours}ч {minutes}м\n"
        f"📌 Последний пост: {stats['last_post'] or 'нет'}\n\n"
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
        f"📅 <b>Запущен:</b> {stats['started_at'].strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"📌 <b>Последний пост:</b> {stats['last_post'] or 'нет'}\n"
        f"📢 <b>Канал-источник:</b> <code>{SOURCE_CHANNEL_ID}</code>\n"
        f"📢 <b>Целевой канал:</b> <code>{TARGET_CHANNEL_ID}</code>\n"
        f"🐍 <b>Python:</b> {sys.version.split()[0]}\n\n"
        f"✅ <b>Бот работает</b> 🟢",
        parse_mode="HTML"
    )

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки подключения к каналам"""
    try:
        bot = context.bot
        
        # Проверяем канал-источник
        try:
            source = await bot.get_chat(SOURCE_CHANNEL_ID)
            source_status = f"✅ {source.title} (ID: {SOURCE_CHANNEL_ID})"
        except Exception as e:
            source_status = f"❌ Ошибка: {e}"
        
        # Проверяем целевой канал
        try:
            target = await bot.get_chat(TARGET_CHANNEL_ID)
            target_status = f"✅ {target.title} (ID: {TARGET_CHANNEL_ID})"
        except Exception as e:
            target_status = f"❌ Ошибка: {e}"
        
        # Проверяем бота
        me = await bot.get_me()
        
        await update.message.reply_text(
            f"🔍 <b>Проверка подключения</b>\n\n"
            f"🤖 <b>Бот:</b> @{me.username}\n"
            f"📢 <b>Канал-источник:</b> {source_status}\n"
            f"📢 <b>Целевой канал:</b> {target_status}\n"
            f"📊 <b>Обработано:</b> {stats['processed']}\n"
            f"❌ <b>Ошибок:</b> {stats['errors']}\n\n"
            f"🔄 <b>Статус:</b> {'✅ Все работает' if stats['processed'] > 0 else '⏳ Ожидание постов'}",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка проверки: {e}")

# ==================== ОБРАБОТКА ПОСТОВ ====================

async def process_post(message, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = get_text_from_message(message)
        title = extract_title_from_text(text)
        
        # Обработка фото
        if hasattr(message, 'photo') and message.photo:
            logger.info(f"📸 Обработка фото")
            photo = message.photo[-1]
            photo_bytes = await download_media(context.bot, photo.file_id)
            
            if not photo_bytes:
                logger.error("❌ Не удалось скачать фото")
                stats['errors'] += 1
                return
            
            processed = process_photo_bytes(photo_bytes, title)
            caption = text[:1024] if text else ""
            
            await context.bot.send_photo(
                chat_id=TARGET_CHANNEL_ID,
                photo=BytesIO(processed.getvalue()),
                caption=caption,
                parse_mode="HTML"
            )
            stats['processed'] += 1
            stats['last_post'] = f"Фото в {datetime.now().strftime('%H:%M:%S')}"
            logger.info(f"✅ Фото отправлено в канал {TARGET_CHANNEL_ID}")
            return
        
        # Обработка видео
        if hasattr(message, 'video') and message.video:
            logger.info(f"📹 Обработка видео")
            video_bytes = await download_media(context.bot, message.video.file_id)
            
            if not video_bytes:
                logger.error("❌ Не удалось скачать видео")
                stats['errors'] += 1
                return
            
            processed = process_video_bytes(video_bytes, title)
            caption = text[:1024] if text else ""
            
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
            return
        
        # Текстовый пост
        if text:
            logger.info(f"📝 Текстовый пост")
            await context.bot.send_message(
                chat_id=TARGET_CHANNEL_ID,
                text=text,
                parse_mode="HTML"
            )
            stats['processed'] += 1
            stats['last_post'] = f"Текст в {datetime.now().strftime('%H:%M:%S')}"
            logger.info(f"✅ Текст отправлен в канал {TARGET_CHANNEL_ID}")
            return
        
        logger.info("ℹ️ Пост пустой, пропускаем")
        
    except Exception as e:
        stats['errors'] += 1
        stats['last_error'] = str(e)
        logger.error(f"❌ Ошибка обработки поста: {e}")
        traceback.print_exc()

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик постов из канала-источника"""
    message = update.channel_post
    if not message:
        logger.info("❌ Нет сообщения в update")
        return
    
    # Проверка ID канала
    logger.info(f"📨 ПОСТ ПОЛУЧЕН! ID канала: {message.chat.id}, SOURCE: {SOURCE_CHANNEL_ID}")
    
    if message.chat.id != SOURCE_CHANNEL_ID:
        logger.info(f"⏭️ Пропускаем: канал {message.chat.id} не равен {SOURCE_CHANNEL_ID}")
        return
    
    logger.info(f"📨 Новый пост в канале {SOURCE_CHANNEL_ID}")
    await process_post(message, context)

# ==================== ЗАПУСК ====================

async def main():
    logger.info("🚀 Бот для репоста с оформлением ЧП ВМ запускается...")
    
    # Скачиваем шрифты
    download_fonts()
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    bot = Bot(token=BOT_TOKEN)
    
    # Удаляем webhook
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка удаления webhook: {e}")
    
    # Проверяем доступ к каналам
    try:
        source = await bot.get_chat(SOURCE_CHANNEL_ID)
        logger.info(f"✅ Канал-источник: {source.title} (ID: {SOURCE_CHANNEL_ID})")
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к каналу-источнику: {e}")
        logger.info("💡 Убедитесь, что бот добавлен в канал как администратор")
        return
    
    try:
        target = await bot.get_chat(TARGET_CHANNEL_ID)
        logger.info(f"✅ Целевой канал: {target.title} (ID: {TARGET_CHANNEL_ID})")
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к целевому каналу: {e}")
        logger.info("💡 Убедитесь, что бот добавлен в канал как администратор")
        return
    
    # Регистрируем команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("test", test_command))
    
    # Регистрируем обработчик постов из канала
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=SOURCE_CHANNEL_ID),
        handle_channel_post
    ))
    
    logger.info("✅ Обработчики зарегистрированы")
    logger.info(f"📊 Параметры оформления (ЧП ВМ):")
    logger.info(f"  • Размер: {TARGET_W}x{TARGET_H}")
    logger.info(f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}%")
    logger.info(f"  • Затемнение: {int(BRIGHTNESS_FACTOR*100)}%")
    
    # Запускаем бота
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
    
    logger.info("🟢 Бот запущен и слушает канал!")
    logger.info("📨 Отправьте пост в канал-источник для теста")
    logger.info("💡 Команды для проверки: /start, /stats, /test")
    
    # Бесконечный цикл
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
