# Poseidon V4 — Surfsculpt x DeepSeek
# FastAPI + Telegram + DeepSeek-Vision

import os
import re
import json
import logging
import asyncio
from io import BytesIO
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import unquote

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telegram import Update as TgUpdate, Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v4")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")  # Теперь из переменных окружения
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не найден")

if not DEEPSEEK_API_KEY:
    raise ValueError("❌ DEEPSEEK_API_KEY не найден")

# ----------------------------------------------------------
# GLOBALS
# ----------------------------------------------------------
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

# ----------------------------------------------------------
# DEEPSEEK VISION ANALYSIS
# ----------------------------------------------------------
async def analyze_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """Анализирует скриншот через DeepSeek Vision и извлекает данные о волнах"""
    try:
        base64_image = await encode_image_to_base64(image_bytes)
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Проанализируй этот скриншот прогноза серфинга и извлеки точные числовые данные. Верни ТОЛЬКО JSON в формате: {\"wave\": число_в_метрах_или_null, \"period\": число_в_секундах_или_null, \"wind\": число_мс_или_null, \"power\": число_кДж_или_null}. Если данных нет - пиши null. Не добавляй никакого текста кроме JSON."
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
            "temperature": 0.1
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    
                    # Извлекаем JSON из ответа
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group())
                    else:
                        logger.error(f"Не найден JSON в ответе: {content}")
                        return {}
                else:
                    logger.error(f"DeepSeek API error: {response.status}")
                    return {}
                    
    except Exception as e:
        logger.error(f"DeepSeek analysis error: {e}")
        return {}

async def encode_image_to_base64(image_bytes: bytes) -> str:
    """Конвертирует изображение в base64"""
    import base64
    return base64.b64encode(image_bytes).decode('utf-8')

# ----------------------------------------------------------
# DEEPSEEK TEXT GENERATION
# ----------------------------------------------------------
async def generate_sarcastic_comment(data_type: str, value: float, unit: str) -> str:
    """Генерирует саркастичный комментарий для каждого параметра"""
    
    prompts = {
        "wave": {
            "low": f"Волна {value}{unit}? Это не волна, это зевок океана! Даже утки создают больше бульков!",
            "medium": f"Волна {value}{unit} - неплохо для начинающих богов! Можно покататься, если не боишься уснуть от скуки.",
            "high": f"ВОЛНА {value}{unit}! Боги гневаются! Готовь доску и завещание, смертный!"
        },
        "period": {
            "low": f"Период {value}{unit}? Волны как икота - прерывисто и бесполезно!",
            "medium": f"Период {value}{unit} - стабильно, как моё настроение перед кофе!",
            "high": f"Период {value}{unit}! Ровные как стекло - боги одобряют твоё катание!"
        },
        "wind": {
            "low": f"Ветер {value}{unit}? Это не ветер, это вздох младенца!",
            "medium": f"Ветер {value}{unit} - идеально для катания! Не сдует, но и не оставит в штиль.",
            "high": f"ВЕТЕР {value}{unit}! Готовься лететь в Таиланд без билета!"
        },
        "power": {
            "low": f"Мощность {value}{unit}? Это не серфинг, это аквааэробика для пенсионеров!",
            "medium": f"Мощность {value}{unit} - достойно для бога! Можно и порезвиться!",
            "high": f"МОЩНОСТЬ {value}{unit}! Океан решил поиграть в боулинг, а ты - шар!"
        }
    }
    
    # Определяем категорию значения
    thresholds = {
        "wave": {"low": 0.5, "medium": 1.5},
        "period": {"low": 8, "medium": 12},
        "wind": {"low": 3, "medium": 8},
        "power": {"low": 200, "medium": 600}
    }
    
    if data_type in thresholds:
        if value < thresholds[data_type]["low"]:
            category = "low"
        elif value < thresholds[data_type]["medium"]:
            category = "medium"
        else:
            category = "high"
        
        return prompts[data_type].get(category, f"{value}{unit} - Посейдон в раздумьях!")
    
    return f"{value}{unit} - интересно, но я бог, а не калькулятор!"

async def generate_final_verdict(spot_data: Dict, tides: Dict) -> str:
    """Генерирует финальный вердикт с сарказмом"""
    
    wave = spot_data.get('wave', 0)
    period = spot_data.get('period', 0)
    wind = spot_data.get('wind', 0)
    
    # Анализ времени для катания
    tide_in = tides.get('tide_in', '').split()
    tide_out = tides.get('tide_out', '').split()
    
    day_tides = []
    for tide in tide_in + tide_out:
        if tide and ':' in tide:
            hour = int(tide.split(':')[0])
            if 6 <= hour <= 20:  # Дневное время
                day_tides.append(tide)
    
    if not day_tides:
        time_advice = "Ночные приливы? Серьёзно? Ты собираешься кататься с фонариком на лбу? БЕССМЫСЛЕННО!"
    elif len(day_tides) >= 2:
        time_advice = f"Лучшее время: {', '.join(day_tides[:2])} - боги благословляют дневные сессии!"
    else:
        time_advice = f"Попробуй в {day_tides[0]} - лучше чем ничего, смертный!"
    
    # Общая оценка условий
    conditions = []
    if wave >= 1.5:
        conditions.append("волны достойные")
    if period >= 10:
        conditions.append("период стабильный") 
    if wind <= 10:
        conditions.append("ветер норм")
    
    if conditions:
        assessment = f"Условия: {', '.join(conditions)}. {time_advice}"
    else:
        assessment = f"Условия так себе. {time_advice}"
    
    sarcasms = [
        "Океан сегодня в настроении поиграть с тобой в салочки!",
        "Волны шепчут: 'Катайся, если осмелишься, смертный!'",
        "Рифы ждут твоих костей как деликатес!",
        "Сегодня океан либо твой друг, либо твой гробовщик!",
        "Боги волн смеются над твоей самонадеянностью!",
        "Приготовь свою лучшую доску и завещание!"
    ]
    
    return f"{assessment}\n\n😈Сарказм Посейдона: {random.choice(sarcasms)}"

# ----------------------------------------------------------
# EXTERNAL DATA
# ----------------------------------------------------------
async def get_windy_forecast(lat: float, lon: float) -> Dict[str, Optional[float]]:
    try:
        url = f"https://node.windy.com/meteogram/api?lat={lat}&lon={lon}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as r:
                if r.status != 200:
                    return {}
                data = await r.json()
                waves = data.get("waves") or {}
                return {
                    "wave": waves.get("height"),
                    "period": waves.get("period"),
                    "wind": data.get("wind", {}).get("speed")
                }
    except Exception as e:
        logger.debug("Windy fetch failed: %s", e)
        return {}

async def fetch_stormglass_tides(lat: float, lon: float, date: str) -> Dict[str, Any]:
    if not STORMGLASS_API_KEY:
        return {}
    url = "https://api.stormglass.io/v2/tide/extremes/point"
    params = {"lat": lat, "lng": lon, "start": date, "end": date}
    headers = {"Authorization": STORMGLASS_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=10) as r:
                if r.status != 200:
                    return {}
                data = await r.json()
                tide_in, tide_out = [], []
                for d in data.get("data", []):
                    t = d.get("time", "")
                    tt = t[11:16] if t else ""
                    tide_type = d.get("tide", {}).get("type")
                    if tide_type == "high":
                        tide_in.append(tt)
                    elif tide_type == "low":
                        tide_out.append(tt)
                return {"tide_in": " ".join(tide_in), "tide_out": " ".join(tide_out)}
    except Exception as e:
        logger.debug("Stormglass fetch failed: %s", e)
        return {}

# ----------------------------------------------------------
# MESSAGE BUILDING
# ----------------------------------------------------------
async def build_poseidon_report(spot_data: Dict, tides: Dict, location: str, date: str) -> str:
    """Строит полный отчет Посейдона"""
    
    # Генерируем комментарии для каждого параметра
    wave_comment = await generate_sarcastic_comment("wave", spot_data.get('wave', 0), "м") if spot_data.get('wave') else "❌ Данные отсутствуют"
    period_comment = await generate_sarcastic_comment("period", spot_data.get('period', 0), "с") if spot_data.get('period') else "❌ Данные отсутствуют"
    wind_comment = await generate_sarcastic_comment("wind", spot_data.get('wind', 0), "м/с") if spot_data.get('wind') else "❌ Данные отсутствуют"
    power_comment = await generate_sarcastic_comment("power", spot_data.get('power', 0), "кДж") if spot_data.get('power') else "❌ Данные отсутствуют"
    
    # Финальный вердикт
    final_verdict = await generate_final_verdict(spot_data, tides)
    
    report = f"""🔱 Посейдонский разбор — {location}, {date}

🌊 Волна: {spot_data.get('wave', 'N/A')} м - 💬 {wave_comment}
⌛ Период: {spot_data.get('period', 'N/A')} с - 💬 {period_comment}
💪 Мощность: {spot_data.get('power', 'N/A')} кДж - 💬 {power_comment}
💨 Ветер: {spot_data.get('wind', 'N/A')} м/с - 💬 {wind_comment}
🌗 Прилив: ↗️ {tides.get('tide_in', 'N/A')}
🌘 Отлив: ↘️ {tides.get('tide_out', 'N/A')}

{final_verdict}

⚠️ Берегите ваши *опки, риф - в режиме маскировки.
🏄‍♂️ Колоборация POSEIDON V4.0 и SURFSCULPT"""
    
    return report

# ----------------------------------------------------------
# HANDLERS
# ----------------------------------------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = USER_STATE.get(chat_id, {})
    
    if not state.get("active"):
        await update.message.reply_text("Чтобы вызвать Посейдона — напиши 'Посейдон на связь'.")
        return

    try:
        await update.message.reply_text("🔱 Анализирую скриншот... Боги видят всё!")
        
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        # Анализ скриншота через DeepSeek Vision
        deepseek_data = await analyze_screenshot_with_deepseek(bytes(image_bytes))
        
        caption = update.message.caption or ""
        location, date = parse_caption_for_location_date(caption)
        
        if not location or location not in SPOT_COORDS:
            await update.message.reply_text(f"⚠️ Не могу найти координаты для '{location}'. Доступные: {', '.join(SPOT_COORDS.keys())}")
            return
            
        coords = SPOT_COORDS[location]

        # Получаем дополнительные данные
        windy_task = asyncio.create_task(get_windy_forecast(coords["lat"], coords["lon"]))
        storm_task = asyncio.create_task(fetch_stormglass_tides(coords["lat"], coords["lon"], date))
        windy_data, storm_data = await asyncio.gather(windy_task, storm_task)

        # Объединяем данные (приоритет DeepSeek, потом Windy)
        merged_data = deepseek_data.copy()
        for key in ['wave', 'period', 'wind']:
            if not merged_data.get(key) and windy_data.get(key):
                merged_data[key] = windy_data[key]

        # Строим отчет
        report = await build_poseidon_report(merged_data, storm_data, location, date)
        await update.message.reply_text(report)
        
        # Сбрасываем состояние
        USER_STATE[chat_id] = {"active": False}

    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await update.message.reply_text("🔱 Посейдон в ярости! Что-то пошло не так. Попробуй ещё раз.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").lower().strip()

    if "посейдон на связь" in text:
        USER_STATE[chat_id] = {"active": True}
        await update.message.reply_text("""🔱 Посейдон слушает! 

Пришли скриншот прогноза с подписью в формате:
`Спот Дата`

Например: `Uluwatu 2024-12-15`

Доступные споты: Balangan, Uluwatu, Kuta, BaliSoul, PadangPadang, BatuBolong""")
        return

    await update.message.reply_text("Напиши 'Посейдон на связь', чтобы начать разбор 🌊")

def parse_caption_for_location_date(caption: Optional[str]):
    if not caption:
        return None, str(datetime.utcnow().date())
    parts = caption.strip().split()
    location = parts[0]
    date = parts[1] if len(parts) > 1 else str(datetime.utcnow().date())
    return location, date

# ----------------------------------------------------------
# BOT SETUP
# ----------------------------------------------------------
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ----------------------------------------------------------
# WEBHOOK ROUTES
# ----------------------------------------------------------
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
    return {"status": "🌊 Poseidon V4 Online", "version": "4.0"}

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Poseidon is watching"}

# ----------------------------------------------------------
# STARTUP
# ----------------------------------------------------------
@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    logger.info("🌊 Poseidon V4 awakened and ready!")

@app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("🌊 Poseidon V4 returning to the depths...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))