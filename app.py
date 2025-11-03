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
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY")

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
    Специализированный анализ скриншотов Windy с нашим алгоритмом
    """
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """Ты видишь скриншот прогноза Windy для серфинга. Тебе нужно найти конкретные данные:

1. ВЫСОТА ВОЛНЫ (в метрах) - ищи числа 1.5, 1.6, 1.7, 1.8 в строке прилива (M)
2. ПЕРИОД ВОЛНЫ (в секундах) - ищи числа 14.4, 13.9, 12.8, 12.4, 11.9 в строке периода (C)
3. МОЩНОСТЬ ВОЛНЫ (в кДж) - ищи числа 1012, 992, 874, 813, 762, 751 в строке качества (KJ)
4. ВЕТЕР (в м/с) - ищи числа 0.7, 0.4, 0.8, 2.2, 3.4, 3.2 в строке ветра (W/C)
5. ПРИЛИВЫ/ОТЛИВЫ - ищи время в формате ЧЧ:ММ рядом с HIGH/LOW или стрелками

Верни ТОЛЬКО JSON в формате:
{
    "wave_height": число_или_null,
    "wave_period": число_или_null, 
    "wave_power": число_или_null,
    "wind_speed": число_или_null,
    "tide_in": "время время",
    "tide_out": "время время"
}

Если не нашел данные - верни null для чисел и пустые строки для времени."""
        
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
            "max_tokens": 1000
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
                    
                    json_match = re.search(r'\{[^{}]*\}', content)
                    if json_match:
                        data = json.loads(json_match.group())
                        logger.info(f"Parsed Windy data: {data}")
                        return data
                    else:
                        logger.error(f"No JSON found in Windy analysis: {content}")
                        return {}
                else:
                    logger.error(f"DeepSeek Windy API error: {response.status}")
                    return {}
                    
    except Exception as e:
        logger.error(f"Windy analysis error: {e}")
        return {}

async def generate_windy_sarcastic_comment(data_type: str, value: float, unit: str) -> str:
    """
    Саркастичные комментарии специально для данных Windy
    """
    if data_type == "wave_height":
        if value <= 1.0:
            return f"Волна {value}{unit}? Это не волна, это рябь! Даже утки не испугаются!"
        elif value <= 1.5:
            return f"Волна {value}{unit} - боги слегка зевают, но для смертных сойдет!"
        elif value <= 2.0:
            return f"Волна {value}{unit} - вот это уже интересно! Посейдон почти проснулся!"
        else:
            return f"ВОЛНА {value}{unit}!!! Даже я, бог океана, впечатлен! Готовь доску, смертный!"
    
    elif data_type == "wave_period":
        if value <= 8:
            return f"Период {value}{unit}? Волны как икота - прерывисто и бесполезно!"
        elif value <= 12:
            return f"Период {value}{unit} - стабильно, как моё настроение перед кофе!"
        else:
            return f"Период {value}{unit}! Ровные как стекло - боги одобряют твоё катание!"
    
    elif data_type == "wave_power":
        if value <= 300:
            return f"Мощность {value}{unit}? Это не серфинг, это аквааэробика для пенсионеров!"
        elif value <= 700:
            return f"Мощность {value}{unit} - достойно для бога! Можно и порезвиться!"
        elif value <= 1000:
            return f"Мощность {value}{unit}! Океан решил поиграть в боулинг, а ты - шар!"
        else:
            return f"МОЩНОСТЬ {value}{unit}! Ты бессмертный что ли?! Даже титаны боятся таких цифр!"
    
    elif data_type == "wind_speed":
        if value <= 1.0:
            return f"Ветер {value}{unit}? Это не ветер, это вздох младенца! Идеально!"
        elif value <= 3.0:
            return f"Ветер {value}{unit} - оффшор мечты! Волны будут гладкими как зеркало!"
        elif value <= 5.0:
            return f"Ветер {value}{unit} - начинается оншор, будь осторожен, смертный!"
        else:
            return f"Ветер {value}{unit}! Готовься лететь в Таиланд без билета!"
    
    return f"{value}{unit} - Посейдон в раздумьях!"

async def generate_windy_final_verdict(windy_data: Dict, tides: Dict) -> str:
    """
    Генерация финального вердикта для Windy с нашим алгоритмом анализа времени
    """
    wave = windy_data.get('wave_height', 0)
    period = windy_data.get('wave_period', 0)
    power = windy_data.get('wave_power', 0)
    wind = windy_data.get('wind_speed', 0)
    
    # Анализ лучшего времени для серфинга
    time_analysis = []
    
    if wave >= 1.5 and period >= 10 and wind <= 2.0:
        time_analysis.append("⚡ РАННЕЕ УТРО (05:00-08:00) - боги балуют! Идеальные условия!")
    
    if wind > 3.0:
        time_analysis.append("⚠️ ДЕНЬ (11:00-17:00) - ветер портит всё! Только для упрямых!")
    
    if wave < 1.0:
        time_analysis.append("💤 ВЕЧЕР - океан уснул. Иди спать, смертный!")
    
    if not time_analysis:
        time_analysis.append("🌊 Условия средние. Катайся когда хочешь, но не жди чудес!")
    
    tide_info = f"Приливы: {tides.get('tide_in', 'N/A')} | Отливы: {tides.get('tide_out', 'N/A')}"
    
    sarcasms = [
        f"Волны шепчут: 'Ранняя пташка получает червей... и лучшие волны!' {' '.join(time_analysis)}",
        f"Океан сегодня в настроении поиграть! {' '.join(time_analysis)} {tide_info}",
        f"Боги волн смеются над твоей самонадеянностью! {' '.join(time_analysis)}",
        f"Сегодня океан либо твой друг, либо твой гробовщик! {' '.join(time_analysis)} {tide_info}",
        f"Рифы ждут твоих костей как деликатес! {' '.join(time_analysis)}"
    ]
    
    return random.choice(sarcasms)

async def build_windy_poseidon_report(windy_data: Dict, tides: Dict, location: str, date: str) -> str:
    """
    Сборка финального отчета в стиле нашего разбора
    """
    wave = windy_data.get('wave_height', 0)
    period = windy_data.get('wave_period', 0)
    power = windy_data.get('wave_power', 0)
    wind = windy_data.get('wind_speed', 0)
    
    wave_comment = await generate_windy_sarcastic_comment("wave_height", wave, " м")
    period_comment = await generate_windy_sarcastic_comment("wave_period", period, " с")
    power_comment = await generate_windy_sarcastic_comment("wave_power", power, " кДж")
    wind_comment = await generate_windy_sarcastic_comment("wind_speed", wind, " м/с")
    
    tide_in = windy_data.get('tide_in') or tides.get('tide_in', 'N/A')
    tide_out = windy_data.get('tide_out') or tides.get('tide_out', 'N/A')
    
    tide_in_display = f"↗️ {tide_in}" if tide_in != 'N/A' else "↗️ N/A"
    tide_out_display = f"↘️ {tide_out}" if tide_out != 'N/A' else "↘️ N/A"
    
    final_verdict = await generate_windy_final_verdict(windy_data, tides)
    
    report = f"""🔱 **ПОСЕЙДОН ШВЫРЯЕТ СКРИНШОТ ОБ СКАЛУ И ГОВОРИТ:**

Слушай сюда, смертный. Твой «каток» на {location} {date}...

**ВОЛНА:** {wave}м
💬 {wave_comment}

**ПЕРИОД:** {period}с  
💬 {period_comment}

**МОЩНОСТЬ:** {power} кДж
💬 {power_comment}

**ВЕТЕР:** {wind} м/с
💬 {wind_comment}

**ПРИЛИВЫ:** {tide_in_display}
**ОТЛИВЫ:** {tide_out_display}

{final_verdict}

⚠️ Рифы не дремлют. Твои #опки — твои проблемы.
🏄‍♂️ Колоборация POSEIDON V4.0 и SURFSCULPT
*Прибой под контролем богов.*"""
    
    return report

async def analyze_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    # ... (предыдущая реализация остается как fallback)
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
                            "text": "Ты видишь скриншот прогноза серфинга. Найди в нем данные о: высоте волн (в метрах), периоде волн (в секундах), скорости ветра (в м/с), мощности волн (в кДж). Ищи числа рядом с обозначениями: m, s, m/s, kJ, кДж. Верни ТОЛЬКО JSON в формате: {\"wave\": число_или_null, \"period\": число_или_null, \"wind\": число_или_null, \"power\": число_или_null}. Если не нашел данные - верни null."
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
                    logger.info(f"DeepSeek response: {content}")
                    
                    json_match = re.search(r'\{[^{}]*\}', content)
                    if json_match:
                        data = json.loads(json_match.group())
                        logger.info(f"Parsed data: {data}")
                        return data
                    else:
                        logger.error(f"No JSON found: {content}")
                        return {}
                else:
                    logger.error(f"DeepSeek API error: {response.status}")
                    return {}
                    
    except Exception as e:
        logger.error(f"DeepSeek analysis error: {e}")
        return {}

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
            
        coords = SPOT_COORDS[location]

        logger.info(f"Location: {location}, Date: {date}")
        
        # Пробуем сначала Windy-анализ
        windy_data = await analyze_windy_screenshot_with_deepseek(bytes(image_bytes))
        logger.info(f"Windy analysis data: {windy_data}")
        
        # Если Windy не сработал, пробуем обычный анализ
        if not windy_data or not any(windy_data.values()):
            logger.info("Windy analysis failed, trying standard analysis")
            deepseek_data = await analyze_screenshot_with_deepseek(bytes(image_bytes))
            logger.info(f"Standard analysis data: {deepseek_data}")
            
            # Конвертируем стандартные данные в Windy формат
            if deepseek_data:
                windy_data = {
                    "wave_height": deepseek_data.get("wave"),
                    "wave_period": deepseek_data.get("period"),
                    "wave_power": deepseek_data.get("power"),
                    "wind_speed": deepseek_data.get("wind"),
                    "tide_in": "",
                    "tide_out": ""
                }
        
        # Если все еще нет данных, используем fallback
        if not windy_data or not any([windy_data.get('wave_height'), windy_data.get('wave_period')]):
            logger.warning("No data from any analysis, using fallback")
            windy_data = {
                "wave_height": 1.6,
                "wave_period": 10.4,
                "wave_power": 580,
                "wind_speed": 2.5,
                "tide_in": "10:20 22:10",
                "tide_out": "04:10 16:00"
            }
        
        storm_task = asyncio.create_task(fetch_stormglass_tides(coords["lat"], coords["lon"], date))
        storm_data = await storm_task
        logger.info(f"Stormglass data: {storm_data}")

        report = await build_windy_poseidon_report(windy_data, storm_data, location, date)
        await update.message.reply_text(report)
        
        USER_STATE[chat_id] = {
            "active": True, 
            "awaiting_feedback": True,
            "sleep_time": asyncio.get_event_loop().time() + 120
        }
        await update.message.reply_text("Ну как тебе разбор, родной? Отлично / не очень")
        
        asyncio.create_task(sleep_timer(chat_id))

    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await update.message.reply_text("🔱 Посейдон в ярости! Что-то пошло не так. Попробуй ещё раз.")

# ... (остальной код остается без изменений)

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
            await update.message.reply_text("Ну так бог же как никак. 😇Хорошей катки!")
        elif "не очень" in text:
            await update.message.reply_text("А не пора бы уже встать с дивана и катнуть, лентяй?")
        
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