Понял! Собираю весь финальный код со всеми улучшениями. Вот полный комплект:

🌊 app.py (полная версия)

```python
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

# =========================
# CONFIG
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v4")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN not found")
if not DEEPSEEK_API_KEY:
    raise ValueError("❌ DEEPSEEK_API_KEY not found")

# =========================
# GLOBALS
# =========================
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

# =========================
# KEEP-ALIVE SYSTEM
# =========================
async def keep_alive_ping():
    """Ping service to keep Render awake every 5 minutes"""
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://surfhunter-bot.onrender.com/ping") as response:
                    logger.info(f"🔄 Keep-alive ping: {response.status}")
        except Exception as e:
            logger.error(f"❌ Ping error: {e}")
        await asyncio.sleep(300)  # 5 minutes

# =========================
# DEEPSEEK VISION ANALYSIS
# =========================
async def analyze_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """Analyze surf forecast screenshot via DeepSeek Vision"""
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
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
                            "text": "Analyze this surf forecast screenshot. Extract ONLY JSON with: wave (height in meters), period (seconds), wind (m/s), power (kJ). Format: {\"wave\": number, \"period\": number, \"wind\": number, \"power\": number}. Use null if no data."
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
            "max_tokens": 500
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
                    
                    json_match = re.search(r'\{[^{}]*\}', content)
                    if json_match:
                        return json.loads(json_match.group())
                    else:
                        logger.error(f"❌ No JSON found: {content}")
                        return {}
                else:
                    logger.error(f"❌ DeepSeek API error: {response.status}")
                    return {}
                    
    except Exception as e:
        logger.error(f"❌ DeepSeek analysis error: {e}")
        return {}

# =========================
# SARCASTIC TEXT GENERATION
# =========================
async def generate_sarcastic_comment(data_type: str, value: float, unit: str) -> str:
    """Generate sarcastic comments based on values"""
    
    # WILD TEXT FOR WAVES ABOVE 2M
    if data_type == "wave" and value > 2:
        wild_texts = [
            f"🌊 ВОЛНА {value}{unit}!!! Посейдон со дна тебя доставать не будет! Готовь завещание, смертный!",
            f"🌊 {value}{unit} ВОЛНЫ! Океан решил поиграть в боулинг, а ты - шар! Прощайся с близкими!",
            f"🌊 ВОЛНА {value}{unit} - боги гневаются! Я уже заказываю похоронную команду для тебя!",
            f"🌊 {value}{unit} ВОЛНЫ! Даже я, бог океана, боюсь сегодня плавать! Ты бессмертный что ли?!"
        ]
        return random.choice(wild_texts)
    
    # MAXIMUM MOCKERY FOR POWER ABOVE 1500 kJ
    if data_type == "power" and value > 1500:
        power_texts = [
            f"💪 МОЩНОСТЬ {value}{unit}! Ты бессмертный что ли?! Кто ты, воин?! Океан тебя перемолотит в фарш!",
            f"💪 {value}{unit} МОЩНОСТИ! Даже титаны боятся таких цифр! Ты точно готов стать кормом для рыб?",
            f"💪 МОЩНОСТЬ {value}{unit} - это не серфинг, это самоубийство с доской! Ты воин или просто сумасшедший?!",
            f"💪 {value}{unit} кДж! Океан сегодня настроен убивать! Кто ты, смертный, чтобы бросать ему вызов?!"
        ]
        return random.choice(power_texts)
    
    # NORMAL COMMENTS
    prompts = {
        "wave": {
            "low": f"🌊 Волна {value}{unit}? Это не волна, это зевок океана! Даже утки создают больше бульков!",
            "medium": f"🌊 Волна {value}{unit} - неплохо для начинающих богов! Можно покататься, если не боишься уснуть от скуки.",
            "high": f"🌊 Волна {value}{unit} - боги одобряют! Можно и порезвиться, смертный!"
        },
        "period": {
            "low": f"⌛ Период {value}{unit}? Волны как икота - прерывисто и бесполезно!",
            "medium": f"⌛ Период {value}{unit} - стабильно, как моё настроение перед кофе!",
            "high": f"⌛ Период {value}{unit}! Ровные как стекло - боги одобряют твоё катание!"
        },
        "wind": {
            "low": f"💨 Ветер {value}{unit}? Это не ветер, это вздох младенца!",
            "medium": f"💨 Ветер {value}{unit} - идеально для катания! Не сдует, но и не оставит в штиль.",
            "high": f"💨 Ветер {value}{unit}! Готовься лететь в Таиланд без билета!"
        },
        "power": {
            "low": f"💪 Мощность {value}{unit}? Это не серфинг, это аквааэробика для пенсионеров!",
            "medium": f"💪 Мощность {value}{unit} - достойно для бога! Можно и порезвиться!",
            "high": f"💪 Мощность {value}{unit}! Океан решил поиграть в боулинг, а ты - шар!"
        }
    }
    
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
    """Generate final verdict with maximum sarcasm"""
    
    wave = spot_data.get('wave', 0)
    power = spot_data.get('power', 0)
    
    # EXTREME CONDITIONS TEXT
    if wave > 2 and power > 1500:
        extreme_texts = [
            "😈 ТЫ РЕШИЛ СЫГРАТЬ В РУССКУЮ РУЛЕТКУ С ОКЕАНОМ?! Волны выше 2м и мощность за 1500 кДж - это не серфинг, это битва с титанами! Я уже заказываю подводные похороны! Ты либо бессмертный герой, либо самый глупый смертный за всю историю!",
            "😈 ОКЕАН СЕГОДНЯ В РЕЖИМЕ 'УБИЙСТВО СМЕРТНЫХ'! Волны как скалы, мощность как у цунами! Ты точно хочешь стать легендарным идиотом, которого будут вспоминать у костра? Даже я, бог океана, сегодня останусь на берегу!",
            "😈 ЭТО НЕ УСЛОВИЯ ДЛЯ СЕРФИНГА, ЭТО КАСТИНГ В ДАРВИНОВСКИЕ ПРЕМИИ! Волны 2м+ и мощность 1500+ кДж - океан решил проредить стадо смертных! Ты хочешь стать статистикой? Я уже вижу твое имя на мемориальной доске!"
        ]
        return random.choice(extreme_texts)
    
    # NORMAL CONDITIONS
    tide_in = tides.get('tide_in', '').split()
    tide_out = tides.get('tide_out', '').split()
    
    day_tides = []
    for tide in tide_in + tide_out:
        if tide and ':' in tide:
            hour = int(tide.split(':')[0])
            if 6 <= hour <= 20:
                day_tides.append(tide)
    
    if not day_tides:
        time_advice = "🌙 Ночные приливы? Серьёзно? Ты собираешься кататься с фонариком на лбу? БЕССМЫСЛЕННО!"
    elif len(day_tides) >= 2:
        time_advice = f"🌅 Лучшее время: {', '.join(day_tides[:2])} - боги благословляют дневные сессии!"
    else:
        time_advice = f"⏰ Попробуй в {day_tides[0]} - лучше чем ничего, смертный!"
    
    sarcasms = [
        f"🌊 Океан сегодня в настроении поиграть с тобой в салочки! {time_advice}",
        f"🌊 Волны шепчут: 'Катайся, если осмелишься, смертный!' {time_advice}",
        f"🌊 Рифы ждут твоих костей как деликатес! {time_advice}",
        f"🌊 Сегодня океан либо твой друг, либо твой гробовщик! {time_advice}",
        f"🌊 Боги волн смеются над твоей самонадеянностью! {time_advice}"
    ]
    
    return random.choice(sarcasms)

# =========================
# EXTERNAL APIs
# =========================
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

# =========================
# MESSAGE BUILDING
# =========================
async def build_poseidon_report(spot_data: Dict, tides: Dict, location: str, date: str) -> str:
    """Build complete Poseidon report"""
    
    wave_comment = await generate_sarcastic_comment("wave", spot_data.get('wave', 0), " м") if spot_data.get('wave') else "❌ Данные отсутствуют"
    period_comment = await generate_sarcastic_comment("period", spot_data.get('period', 0), " с") if spot_data.get('period') else "❌ Данные отсутствуют"
    wind_comment = await generate_sarcastic_comment("wind", spot_data.get('wind', 0), " м/с") if spot_data.get('wind') else "❌ Данные отсутствуют"
    power_comment = await generate_sarcastic_comment("power", spot_data.get('power', 0), " кДж") if spot_data.get('power') else "❌ Данные отсутствуют"
    
    final_verdict = await generate_final_verdict(spot_data, tides)
    
    report = f"""🔱 Посейдонский разбор — {location}, {date}

🌊 Волна: {spot_data.get('wave', 'N/A')} м - 💬 {wave_comment}
⌛ Период: {spot_data.get('period', 'N/A')} с - 💬 {period_comment}
💪 Мощность: {spot_data.get('power', 'N/A')} кДж - 💬 {power_comment}
💨 Ветер: {spot_data.get('wind', 'N/A')} м/с - 💬 {wind_comment}
🌗 Прилив: ↗️ {tides.get('tide_in', 'N/A')}
🌘 Отлив: ↘️ {tides.get('tide_out', 'N/A')}

{final_verdict}

⚠️ Берегите ваши #опки, риф - в режиме маскировки.
🏄‍♂️ Колоборация POSEIDON V4.0 и SURFSCULPT"""
    
    return report

# =========================
# SLEEP TIMER
# =========================
async def sleep_timer(chat_id: int):
    """2-minute sleep timer"""
    await asyncio.sleep(120)
    if chat_id in USER_STATE:
        USER_STATE[chat_id]["active"] = False
        logger.info(f"😴 Bot sleeping for chat {chat_id}")

# =========================
# TELEGRAM HANDLERS
# =========================
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
                f"⚠️ Не могу найти координаты для '{location}'. "
                f"Доступные споты: Balangan, Uluwatu, Kuta, BaliSoul, PadangPadang, BatuBolong"
            )
            return
            
        coords = SPOT_COORDS[location]

        deepseek_data = await analyze_screenshot_with_deepseek(bytes(image_bytes))
        
        windy_task = asyncio.create_task(get_windy_forecast(coords["lat"], coords["lon"]))
        storm_task = asyncio.create_task(fetch_stormglass_tides(coords["lat"], coords["lon"], date))
        
        windy_data, storm_data = await asyncio.gather(windy_task, storm_task)

        merged_data = deepseek_data.copy()
        for key in ['wave', 'period', 'wind']:
            if not merged_data.get(key) and windy_data.get(key):
                merged_data[key] = windy_data[key]
                
        if deepseek_data.get('power'):
            merged_data['power'] = deepseek_data['power']

        report = await build_poseidon_report(merged_data, storm_data, location, date)
        await update.message.reply_text(report)
        
        USER_STATE[chat_id] = {
            "active": True, 
            "awaiting_feedback": True,
            "sleep_time": asyncio.get_event_loop().time() + 120
        }
        await update.message.reply_text("Ну как тебе разбор, родной? Отлично / не очень")
        
        asyncio.create_task(sleep_timer(chat_id))

    except Exception as e:
        logger.error(f"❌ Error in handle_photo: {e}")
        await update.message.reply_text("🔱 Посейдон в ярости! Что-то пошло не так. Попробуй ещё раз.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").lower().strip()

    if "посейдон на связь" in text.lower():
        USER_STATE[chat_id] = {"active": True}
        await update.message.reply_text(
            "🔱 Посейдон тут, смертный!\n\n"
            "Давай свой скриншот прогноза с подписью в формате:\n"
            "`Uluwatu 2025-12-15`\n\n"
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

# =========================
# BOT SETUP
# =========================
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# =========================
# FASTAPI ROUTES
# =========================
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = TgUpdate.de_json(data, bot)
        await bot_app.process_update(update)
        return JSONResponse(content={"ok": True})
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse(status_code=500, content={"ok": False})

@app.get("/")
async def root():
    return {"status": "🌊 Poseidon V4 Online", "version": "4.0"}

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Poseidon is awake and watching!"}

# =========================
# STARTUP/SHUTDOWN
# =========================
@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(keep_alive_ping())
    logger.info("🌊 Poseidon V4 awakened and ready!")

@app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("🌊 Poseidon V4 returning to the depths...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
```

Продолжение с остальными файлами в следующем сообщении...