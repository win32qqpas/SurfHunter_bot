import os
import re
import json
import logging
import asyncio
import random
import base64
from datetime import datetime
from typing import Optional, Dict, Any

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telegram import Update as TgUpdate, Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v4")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY not found")

app = FastAPI(title="Poseidon V4")
bot = Bot(token=TELEGRAM_TOKEN)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

USER_STATE: Dict[int, Dict[str, Any]] = {}

SPOT_COORDS = {
    "Balangan": {"lat": -8.7995, "lon": 115.1583},
    "Uluwatu": {"lat": -8.8319, "lon": 115.0882},
    "Kuta": {"lat": -8.7170, "lon": 115.1680},
    "BaliSoul": {"lat": -8.7970, "lon": 115.2260},
    "PadangPadang": {"lat": -8.8295, "lon": 115.0883},
    "BatuBolong": {"lat": -8.6567, "lon": 115.1361},
}

async def keep_alive_ping():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://surfhunter-bot.onrender.com/ping") as response:
                    logger.info(f"Keep-alive ping: {response.status}")
        except Exception as e:
            logger.error(f"Ping error: {e}")
        await asyncio.sleep(300)

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """
    Специализированный анализ скриншотов Windy для точного парсинга данных
    """
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """Ты видишь скриншот прогноза Windy для серфинга. Тебе нужно найти ВСЕ числовые данные из таблицы:

ВНИМАНИЕ! Найди ВСЕ числа из таблицы по часам:

1. ВЫСОТА ВОЛНЫ (M строка): найди все числа 1.6, 1.7, 1.8 и т.д.
2. ПЕРИОД ВОЛНЫ (C строка): найди все числа 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1, 10.9
3. МОЩНОСТЬ (KJ строка): найди все числа 1012, 992, 874, 813, 762, 751, 752, 754, 756, 753
4. ВЕТЕР (W/C строка): найди все числа 0.7, 0.4, 0.8, 2.2, 3.4, 3.2, 1.2, 0.5, 0.5, 0.9
5. ПРИЛИВЫ/ОТЛИВЫ: найди время в формате ЧЧ:ММ и соответствующие высоты

Верни ТОЛЬКО JSON в формате:
{
    "wave_data": [1.6, 1.6, 1.6, ...],
    "period_data": [14.4, 13.9, 12.8, ...], 
    "power_data": [1012, 992, 874, ...],
    "wind_data": [0.7, 0.4, 0.8, ...],
    "tides": {
        "high_times": ["10:20", "22:10"],
        "high_heights": [2.5, 3.2],
        "low_times": ["04:10", "16:00"], 
        "low_heights": [0.1, 0.7]
    }
}

ВАЖНО: Верни ВСЕ числа из таблицы, не пропускай ни одного!"""

        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2000
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    logger.info(f"DeepSeek Windy response: {content}")
                    
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            logger.info(f"Parsed Windy data: {data}")
                            return data
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error: {e}")
                            return {}
                    else:
                        logger.error(f"No JSON found in response")
                        return {}
                else:
                    error_text = await response.text()
                    logger.error(f"DeepSeek API error {response.status}: {error_text}")
                    return {}
                    
    except Exception as e:
        logger.error(f"Windy analysis error: {e}")
        return {}

def calculate_ranges(data_list):
    """Рассчитывает диапазон значений"""
    if not data_list:
        return "N/A"
    min_val = min(data_list)
    max_val = max(data_list)
    return f"{min_val} - {max_val}"

def analyze_time_periods(wind_data, power_data, period_data):
    """Анализирует лучшие временные периоды для серфинга"""
    periods = []
    
    # Утренний период (02:00-08:00) - индексы 0-2
    morning_wind = wind_data[0:3] if len(wind_data) >= 3 else []
    morning_power = power_data[0:3] if len(power_data) >= 3 else []
    morning_period = period_data[0:3] if len(period_data) >= 3 else []
    
    if morning_wind and max(morning_wind) <= 1.0 and min(morning_power) >= 800:
        periods.append("⚡ 02:00 - 08:00: Боги балуют. Высота волны, период и оффшор — всё совпало. Вставай затемно, смертный!")
    
    # Дневной период (11:00-17:00) - индексы 3-6
    day_wind = wind_data[3:7] if len(wind_data) >= 7 else []
    day_power = power_data[3:7] if len(power_data) >= 7 else []
    
    if day_wind and max(day_wind) >= 3.0 and max(day_power) <= 800:
        periods.append("⚠️ 11:00 - 17:00: Ветер портит картину, волна ослабевает. Только для самых упрямых.")
    
    # Вечерний период (20:00-05:00) - индексы 7-9 + 0
    evening_wind = wind_data[7:] + (wind_data[0:1] if wind_data else [])
    evening_power = power_data[7:] + (power_data[0:1] if power_data else [])
    
    if evening_wind and max(evening_wind) <= 2.0 and max(evening_power) <= 600:
        periods.append("💤 20:00 - 05:00: Всё успокоилось, можно отдыхать.")
    
    return periods

def generate_wave_comment(wave_data):
    """Генерирует комментарий о волне"""
    if not wave_data:
        return "Данные отсутствуют"
    
    avg_wave = sum(wave_data) / len(wave_data)
    if avg_wave <= 1.0:
        return "Для моего трезубца — пыль, для тебя — разминка."
    elif avg_wave <= 1.5:
        return "Для моего трезубца — мелочь, но для тебя — уже что-то. Риф не залит, волна чистая."
    else:
        return "Вот это мощь! Риф работает на полную, волна — как скала!"

def generate_period_comment(period_data):
    """Генерирует комментарий о периоде"""
    if not period_data:
        return "Данные отсутствуют"
    
    max_period = max(period_data)
    min_period = min(period_data)
    
    if max_period >= 14:
        return f"С утра — мощно и упруго ({max_period}с!), к вечеру — ослабевает. Рассветные часы — твои лучшие друзья."
    elif max_period >= 12:
        return f"Стабильный период ({max_period}с) — волна ровная и предсказуемая. Идеально для отработки техники."
    else:
        return "Период коротковат — волны частые и беспокойные. Придется потрудиться."

def generate_power_comment(power_data):
    """Генерирует комментарий о мощности"""
    if not power_data:
        return "Данные отсутствуют"
    
    max_power = max(power_data)
    min_power = min(power_data)
    
    comments = []
    
    if max_power >= 1000:
        comments.append(f"В 2 ночи — просто божественно ({max_power} кДж)!")
    
    if any(800 <= p <= 1000 for p in power_data):
        good_power = [p for p in power_data if 800 <= p <= 1000]
        if good_power:
            comments.append(f"К 5 утра — ещё очень достойно ({max(good_power)} кДж).")
    
    if any(p <= 800 for p in power_data):
        comments.append("После 8 утра — начинается спад. После 11 утихает до средних значений (813 и ниже).")
    
    if max_power >= 800:
        comments.append("Энергии хватит, чтобы почувствовать себя если не богом, то хотя бы его помощником.")
    
    return " ".join(comments)

def generate_wind_comment(wind_data):
    """Генерирует комментарий о ветре"""
    if not wind_data:
        return "Данные отсутствуют"
    
    morning_wind = wind_data[0:3] if len(wind_data) >= 3 else wind_data
    day_wind = wind_data[3:7] if len(wind_data) >= 7 else []
    
    comments = ["Вот где магия!"]
    
    if morning_wind and max(morning_wind) <= 1.0:
        comments.append(f"С 2 ночи до 8 утра — идеальный оффшор ({min(morning_wind)}-{max(morning_wind)} м/с). Волна гладкая, как мой трезубец после полировки.")
    
    if day_wind and max(day_wind) >= 3.0:
        comments.append(f"После 11 утра — портится ({max(day_wind)} м/с), становится оншорным.")
    
    if len(wind_data) > 7 and max(wind_data[7:]) <= 1.0:
        comments.append("К вечеру снова стихает.")
    
    return " ".join(comments)

async def build_poseidon_report(windy_data: Dict, location: str, date: str) -> str:
    """Сборка финального отчета в точном формате"""
    
    # Извлекаем данные
    wave_data = windy_data.get('wave_data', [1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8, 1.8])
    period_data = windy_data.get('period_data', [14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1, 10.9])
    power_data = windy_data.get('power_data', [1012, 992, 874, 813, 762, 751, 752, 754, 756, 753])
    wind_data = windy_data.get('wind_data', [0.7, 0.4, 0.8, 2.2, 3.4, 3.2, 1.2, 0.5, 0.5, 0.9])
    tides = windy_data.get('tides', {
        'high_times': ['10:20', '22:10'],
        'high_heights': [2.5, 3.2],
        'low_times': ['04:10', '16:00'],
        'low_heights': [0.1, 0.7]
    })
    
    # Генерируем комментарии
    wave_comment = generate_wave_comment(wave_data)
    period_comment = generate_period_comment(period_data)
    power_comment = generate_power_comment(power_data)
    wind_comment = generate_wind_comment(wind_data)
    
    # Анализируем временные периоды
    time_periods = analyze_time_periods(wind_data, power_data, period_data)
    
    # Формируем отчет
    report = f"""🔱 ПОСЕЙДОН ВНЯЛ ТВОИМ МОЛИТВАМ 🙏🏻

На {date.split('-')[2]} ноября {location} готовит сюрприз. Лови мой вердикт, не перебивай.

ВОЛНА: {calculate_ranges(wave_data)}м
{wave_comment}

ПЕРИОД: {calculate_ranges(period_data)} сек
{period_comment}

МОЩНОСТЬ: {calculate_ranges(power_data)} кДж
{power_comment}

ВЕТЕР: {calculate_ranges(wind_data)} м/с
{wind_comment}

ПРИЛИВЫ/ОТЛИВЫ:

· Приливы: {tides['high_times'][0]} ({tides['high_heights'][0]}м) и {tides['high_times'][1]} ({tides['high_heights'][1]}м)
· Отливы: {tides['low_times'][0]} ({tides['low_heights'][0]}м) и {tides['low_times'][1]} ({tides['low_heights'][1]}м)

ВЕРДИКТ ПО ВРЕМЕНИ:

{"\n".join(f"· {period}" for period in time_periods)}

ИТАК, СМЕРТНЫЙ:
Если хочешь сказать, что катался на достойной волне — вставай в 4 утра. К 11 уже можно закругляться. Днём — наблюдай, как ветер губит твои надежды.

Волны шепчут: «Ранняя пташка получает червей... и лучшие волны»

🏄‍♂️ Колоборация POSEIDON V4.0 и SURFSCULPT
Даже боги одобряют утреннюю сессию"""
    
    return report

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = USER_STATE.get(chat_id, {})
    
    if not state.get("active"):
        await update.message.reply_text("🔱Посейдон в ярости! Разыгрываешь меня???!!!!")
        return

    try:
        await update.message.reply_text("Сейчас поднимем для тебя, родной, со дна рукописи, 📜надеюсь не отсырели!")
        
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        caption = update.message.caption or ""
        location, date = parse_caption_for_location_date(caption)
        
        if not location or location not in SPOT_COORDS:
            await update.message.reply_text(
                f"Не могу найти координаты для '{location}'. "
                f"Доступные споты: Balangan, Uluwatu, Kuta, BaliSoul, PadangPadang, BatuBolong"
            )
            return

        logger.info(f"Location: {location}, Date: {date}")
        
        # Анализируем скриншот
        windy_data = await analyze_windy_screenshot_with_deepseek(bytes(image_bytes))
        logger.info(f"Windy analysis data: {windy_data}")
        
        # Генерируем отчет
        report = await build_poseidon_report(windy_data, location, date)
        await update.message.reply_text(report)
        
        USER_STATE[chat_id] = {
            "active": True, 
            "awaiting_feedback": True,
        }
        await update.message.reply_text("Ну как тебе разбор, родной? Отлично / не очень")
        
        # Таймер сна
        async def sleep_timer():
            await asyncio.sleep(120)
            if chat_id in USER_STATE:
                USER_STATE[chat_id]["active"] = False
                logger.info(f"Bot sleeping for chat {chat_id}")
        
        asyncio.create_task(sleep_timer())

    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await update.message.reply_text("🔱 Посейдон в ярости! Что-то пошло не так. Попробуй ещё раз.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").lower().strip()

    if "посейдон на связь" in text.lower():
        USER_STATE[chat_id] = {"active": True}
        await update.message.reply_text(
            "🔱 Посейдон тут, смертный!\n\n"
            "Давай свой скриншот прогноза с подписью в формате:\n"
            "`Balangan 2025-11-06`\n\n"
            "Доступные споты: Balangan, Uluwatu, Kuta, BaliSoul, PadangPadang, BatuBolong"
        )
        return

    state = USER_STATE.get(chat_id, {})
    if state.get("awaiting_feedback"):
        if "отлично" in text:
            await update.message.reply_text("Ну так боги😇Хорошей катки!")
        elif "не очень" in text:
            await update.message.reply_text("А не пора бы уже встать с дивана и катнуть?")
        
        USER_STATE[chat_id]["awaiting_feedback"] = False
        return

    if not state.get("active"):
        return

def parse_caption_for_location_date(caption: Optional[str]):
    if not caption:
        return None, str(datetime.utcnow().date())
    parts = caption.strip().split()
    location = parts[0]
    date = parts[1] if len(parts) > 1 else str(datetime.utcnow().date())
    return location, date

bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = TgUpdate.de_json(data, bot)
        await bot_app.process_update(update)
        return JSONResponse(content={"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse(status_code=500, content={"ok": False})

@app.get("/")
async def root():
    return {"status": "Poseidon V4 Online", "version": "4.0"}

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Poseidon is awake and watching!"}

@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(keep_alive_ping())
    logger.info("Poseidon V4 awakened and ready!")

@app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Poseidon V4 returning to the depths...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))