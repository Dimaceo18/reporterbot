# -*- coding: utf-8 -*-

import os
import re
import logging
import sys
import tempfile
import subprocess
from io import BytesIO
from typing import Optional, List
import traceback
import asyncio

# Проверяем и устанавливаем необходимые библиотеки
try:
    import httpx
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx"])
    import httpx

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

# Правильный импорт moviepy
try:
    from moviepy import VideoFileClip, ImageSequenceClip
    from moviepy.video.fx import resize
    from moviepy.video.compositing.concatenate import concatenate_videoclips
    from moviepy.audio.io.AudioFileClip import AudioFileClip
except ImportError:
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.video.compositing.concatenate import concatenate_videoclips
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        try:
            from moviepy.video.fx import resize
        except:
            resize = None
    except:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy==1.0.3"])
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.video.compositing.concatenate import concatenate_videoclips
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        try:
            from moviepy.video.fx import resize
        except:
            resize = None

try:
    import numpy as np
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy"])
    import numpy as np

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from telegram import Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL_ID = os.getenv("SOURCE_CHANNEL_ID")  # Канал, откуда берем посты
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")  # Канал, куда публикуем

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

# НАСТРОЙКИ СТИЛЯ (ЧП ВМ)
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

# ==================== ФУНКЦИИ ДЛЯ ШРИФТОВ ====================

def download_fonts():
    """Скачивание шрифтов"""
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
    """Загрузка шрифта"""
    try:
        return ImageFont.truetype(font_name, size=size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_FALLBACK, size=size)
        except:
            return ImageFont.load_default()

# ==================== ФУНКЦИИ ОБРАБОТКИ ИЗОБРАЖЕНИЙ ====================

def crop_to_ratio(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Обрезка изображения под нужное соотношение сторон"""
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
    """Применение градиента снизу"""
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
    """Ширина текста"""
    try:
        bbox = draw.textbbox((0, 0), s, font=font)
        return bbox[2] - bbox[0]
    except:
        return len(s) * font.size // 2

def wrap_text(draw, text: str, font, max_width: int, max_lines: int = 6):
    """Перенос текста по словам"""
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
    """Подбор размера шрифта для текста"""
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
    """Очистка заголовка от эмодзи и лишних пробелов"""
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
    """Извлечение заголовка из текста"""
    if not text:
        return ""
    
    # Удаляем эмодзи
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
    
    # Если есть перенос строки - берем первую строку
    if '\n' in clean_text:
        lines = clean_text.split('\n')
        title = lines[0].strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    # Если есть точка и текст длинный - берем первое предложение
    if '. ' in clean_text and len(clean_text) > 100:
        parts = clean_text.split('. ', 1)
        title = (parts[0] + '.').strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    # Иначе берем весь текст
    if len(clean_text) > 200:
        return clean_text[:197] + "..."
    return clean_text

def process_image(img: Image.Image, title_text: str) -> Image.Image:
    """Обработка изображения: обрезка, затемнение, градиент, текст"""
    try:
        # Обрезка под 4:5
        img = crop_to_ratio(img, TARGET_W, TARGET_H)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        
        # Затемнение
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        
        # Градиент снизу
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        # Нанесение текста
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
    """Обработка фото из байтов"""
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
    """Обработка кадра видео"""
    try:
        img = Image.fromarray(frame).convert("RGB")
        img = process_image(img, title_text)
        return np.array(img)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки кадра: {e}")
        return frame

def process_video_bytes(video_bytes: bytes, title_text: str) -> BytesIO:
    """Обработка видео из байтов"""
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
        
        # Обработка всех кадров
        def process_frame(frame):
            return process_video_frame(frame, title_text)
        
        processed_video = video.fl_image(process_frame)
        
        # Сохраняем аудио
        if video.audio is not None:
            try:
                logger.info(f"🎵 Сохраняем оригинальное аудио...")
                processed_video = processed_video.set_audio(video.audio)
                logger.info(f"✅ Оригинальное аудио сохранено")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения аудио: {e}")
        
        logger.info(f"💾 Сохранение видео...")
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
    """Скачивание медиа по file_id"""
    try:
        file = await bot.get_file(file_id)
        
        logger.info(f"📥 Скачивание, размер: {file.file_size / (1024*1024):.1f} MB" if file.file_size else "📥 Скачивание...")
        result = await file.download_as_bytearray()
        logger.info(f"✅ Скачано: {len(result) / (1024*1024):.1f} MB")
        return bytes(result)
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания: {e}")
        return None

def get_text_from_message(message) -> str:
    """Получение текста из сообщения"""
    return message.text or message.caption or ""

# ==================== ОСНОВНАЯ ЛОГИКА ====================

async def process_post(message, context: ContextTypes.DEFAULT_TYPE):
    """Обработка поста из канала"""
    try:
        logger.info(f"📨 Получен пост из канала")
        
        # Получаем текст
        text = get_text_from_message(message)
        
        # Извлекаем заголовок
        title = extract_title_from_text(text)
        formatted_text = text
        
        # Определяем, что пришло: фото, видео или медиагруппа
        has_photos = hasattr(message, 'photo') and message.photo
        has_video = hasattr(message, 'video') and message.video
        has_media_group = hasattr(message, 'media_group_id') and message.media_group_id
        
        # Если медиагруппа - берем только первое фото
        if has_media_group and has_photos:
            logger.info(f"📦 Медиагруппа, берем только первое фото")
            # Если это медиагруппа, то обрабатываем только первое фото из группы
            # Но в telegram API приходит каждое фото отдельно, поэтому проверяем media_group_id
        
        # Обработка фото
        if has_photos and not has_media_group:
            logger.info(f"📸 Обработка фото")
            
            # Берем самое качественное фото
            photo = message.photo[-1]
            photo_bytes = await download_media(context.bot, photo.file_id)
            
            if not photo_bytes:
                logger.error("❌ Не удалось скачать фото")
                return
            
            # Обрабатываем фото
            processed = process_photo_bytes(photo_bytes, title)
            
            if not processed or len(processed.getvalue()) == 0:
                logger.error("❌ Ошибка обработки фото")
                return
            
            # Отправляем в целевой канал
            caption = formatted_text if formatted_text else ""
            if caption:
                caption = caption[:1024]  # Telegram ограничение
            
            await context.bot.send_photo(
                chat_id=TARGET_CHANNEL_ID,
                photo=BytesIO(processed.getvalue()),
                caption=caption,
                parse_mode="HTML",
                width=TARGET_W,
                height=TARGET_H
            )
            
            logger.info(f"✅ Фото отправлено в канал {TARGET_CHANNEL_ID}")
            return
        
        # Обработка видео
        if has_video:
            logger.info(f"📹 Обработка видео")
            
            video_bytes = await download_media(context.bot, message.video.file_id)
            
            if not video_bytes:
                logger.error("❌ Не удалось скачать видео")
                return
            
            # Обрабатываем видео
            processed = process_video_bytes(video_bytes, title)
            
            if not processed or len(processed.getvalue()) == 0:
                logger.error("❌ Ошибка обработки видео")
                return
            
            # Отправляем в целевой канал
            caption = formatted_text if formatted_text else ""
            if caption:
                caption = caption[:1024]  # Telegram ограничение
            
            await context.bot.send_video(
                chat_id=TARGET_CHANNEL_ID,
                video=BytesIO(processed.getvalue()),
                caption=caption,
                parse_mode="HTML",
                width=TARGET_W,
                height=TARGET_H
            )
            
            logger.info(f"✅ Видео отправлено в канал {TARGET_CHANNEL_ID}")
            return
        
        # Если нет медиа - просто пересылаем текст
        if text and not has_photos and not has_video:
            logger.info(f"📝 Текстовый пост")
            await context.bot.send_message(
                chat_id=TARGET_CHANNEL_ID,
                text=text,
                parse_mode="HTML"
            )
            logger.info(f"✅ Текст отправлен в канал {TARGET_CHANNEL_ID}")
            return
        
        logger.info("ℹ️ Пост не содержит медиа, пропускаем")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки поста: {e}")
        traceback.print_exc()

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик постов из канала-источника"""
    message = update.channel_post
    if not message:
        return
    
    # Проверяем, что пост из нужного канала
    if message.chat.id != SOURCE_CHANNEL_ID:
        return
    
    # Пропускаем служебные сообщения
    if hasattr(message, 'new_chat_members') or hasattr(message, 'left_chat_member'):
        return
    
    logger.info(f"📨 Новый пост в канале {SOURCE_CHANNEL_ID}")
    
    # Обрабатываем пост
    await process_post(message, context)

async def handle_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик медиагрупп"""
    message = update.channel_post
    if not message:
        return
    
    # Проверяем, что пост из нужного канала
    if message.chat.id != SOURCE_CHANNEL_ID:
        return
    
    media_group_id = getattr(message, 'media_group_id', None)
    if not media_group_id:
        return
    
    logger.info(f"📦 Медиагруппа в канале: {media_group_id}")
    
    # Для медиагрупп берем только первое фото или видео
    # Поскольку каждое сообщение в медиагруппе приходит отдельно,
    # мы обрабатываем только первое
    
    # Проверяем, есть ли фото в этом сообщении
    if hasattr(message, 'photo') and message.photo:
        logger.info(f"📸 Обработка первого фото из медиагруппы")
        await process_post(message, context)
    elif hasattr(message, 'video') and message.video:
        logger.info(f"📹 Обработка первого видео из медиагруппы")
        await process_post(message, context)

# ==================== ЗАПУСК ====================

async def main():
    logger.info("🚀 Бот для пересылки постов с оформлением запускается...")
    
    # Скачиваем шрифты
    download_fonts()
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    bot = Bot(token=BOT_TOKEN)
    
    # Проверяем доступ к каналам
    try:
        source_channel = await bot.get_chat(SOURCE_CHANNEL_ID)
        logger.info(f"✅ Подключен к каналу-источнику: {source_channel.title}")
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к каналу-источнику: {e}")
        return
    
    try:
        target_channel = await bot.get_chat(TARGET_CHANNEL_ID)
        logger.info(f"✅ Подключен к целевому каналу: {target_channel.title}")
    except Exception as e:
        logger.error(f"❌ Ошибка доступа к целевому каналу: {e}")
        return
    
    # Удаляем webhook
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка удаления webhook: {e}")
    
    # Регистрируем обработчики
    # Обработчик постов из канала
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=SOURCE_CHANNEL_ID) & filters.ALL,
        handle_channel_post
    ))
    
    logger.info("✅ Обработчики зарегистрированы")
    logger.info(f"📊 Параметры оформления (ЧП ВМ):")
    logger.info(f"  • Размер: {TARGET_W}x{TARGET_H}")
    logger.info(f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}%")
    logger.info(f"  • Затемнение: {int(BRIGHTNESS_FACTOR*100)}%")
    logger.info(f"  • Текст: снизу")
    logger.info(f"📢 Канал-источник: {SOURCE_CHANNEL_ID}")
    logger.info(f"📢 Целевой канал: {TARGET_CHANNEL_ID}")
    
    # Запускаем бота
    await app.initialize()
    await app.start()
    
    await app.updater.start_polling(
        allowed_updates=["channel_post"],
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30
    )
    
    logger.info("🟢 Бот запущен и слушает канал!")
    
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
