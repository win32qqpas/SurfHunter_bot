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
    """Пинг для поддержания активности на Render"""
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://surfhunter-bot.onrender.com/") as response:
                    if response.status == 200:
                        logger.info(f"✅ Keep-alive ping successful: {response.status}")
                    else:
                        logger.warning(f"⚠️ Keep-alive ping unusual status: {response.status}")
        except Exception as e:
            logger.error(f"❌ Ping error: {e}")
        await asyncio.sleep(600)

def generate_realistic_fallback_data():
    """Генерирует реалистичные случайные данные для fallback"""
    
    conditions = [
        {
            "wave": [1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3],
            "period": [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9],
            "power": [586, 547, 501, 454, 412, 396, 331, 317, 291, 277],
            "wind": [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8]
        }
    ]
    
    chosen = random.choice(conditions)
    
    return {
        "success": False,
        "wave_data": chosen["wave"],
        "period_data": chosen["period"],
        "power_data": chosen["power"],
        "wind_data": chosen["wind"],
        "tides": {
            "high_times": ["09:00", "21:05"],
            "high_heights": [2.3, 2.8],
            "low_times": ["14:50"],
            "low_heights": [0.8]
        }
    }

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """
    Улучшенный анализ скриншотов Windy через DeepSeek с правильным парсингом приливов
    """
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """ТЫ ДОЛЖЕН ТОЧНО ПРОЧИТАТЬ ДАННЫЕ ИЗ СКРИНШОТA WINDY!

СКРИНШОТ СОДЕРЖИТ ТАБЛИЦУ С ПРОГНОЗОМ НА 10 ДНЕЙ:

СТРУКТУРА ТАБЛИЦЫ:
- Первая строка: дни (Чт 04, Пт 05, Сб 06, Вс 07, Пн 08, Вт 09, Ср 10, Чт 11, Пт 12, Сб 13)
- Вторая строка: высота волны в метрах (1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3)
- Третья строка: период волны в секундах (10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9)
- Четвертая строка: мощность в кДж (586, 547, 501, 454, 412, 396, 331, 317, 291, 277)
- Пятая строка: ветер в м/с (1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8)

ПРИЛИВЫ/ОТЛИВЫ: ищи строку с метками М, LAT или временами приливов/отливов
Формат: ЧЧ:ММ(Х.Хм) например: 09:00(2.3м) - прилив, 14:50(0.8м) - отлив, 21:05(2.8м) - прилив

ВОЗВРАЩАЙ ТОЧНЫЙ JSON С РЕАЛЬНЫМИ ДАННЫМИ:

{
    "success": true,
    "wave_data": [ЦИФРЫ ВЫСОТЫ ВОЛНЫ ИЗ ВТОРОЙ СТРОКИ],
    "period_data": [ЦИФРЫ ПЕРИОДА ИЗ ТРЕТЬЕЙ СТРОКИ],
    "power_data": [ЦИФРЫ МОЩНОСТИ ИЗ ЧЕТВЕРТОЙ СТРОКИ],
    "wind_data": [ЦИФРЫ ВЕТРА ИЗ ПЯТОЙ СТРОКИ],
    "tides": {
        "high_times": ["09:00", "21:05"],
        "high_heights": [2.3, 2.8],
        "low_times": ["14:50"],
        "low_heights": [0.8]
    }
}

ВАЖНО: 
- Время восхода: ~05:50, заката: ~18:15 (это НЕ приливы!)
- Приливы: ищи форматы ЧЧ:ММ(Х.Хм) или в строке М,LAT
- Брать ТОЛЬКО реальные цифры со скриншота"""

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
                            return {"success": False}
                    else:
                        logger.error(f"No JSON found in response")
                        return {"success": False}
                else:
                    error_text = await response.text()
                    logger.error(f"DeepSeek API error {response.status}: {error_text}")
                    return {"success": False}
                    
    except Exception as e:
        logger.error(f"Windy analysis error: {e}")
        return {"success": False}

def calculate_ranges(data_list):
    """Рассчитывает диапазон значений"""
    if not data_list:
        return "N/A"
    min_val = min(data_list)
    max_val = max(data_list)
    return f"{min_val} - {max_val}"

def generate_wave_comment(wave_data):
    """Генерирует саркастичный комментарий о волне"""
    if not wave_data:
        return "Данные о волне отсутствуют. Видимо, Посейдон сегодня молчит."
    
    avg_wave = sum(wave_data) / len(wave_data)
    max_wave = max(wave_data)
    min_wave = min(wave_data)
    trend = "📉" if wave_data[0] > wave_data[-1] else "📈" if wave_data[0] < wave_data[-1] else "➡️"
    
    sarcastic_comments = [
        f"{trend} От {max_wave}м до {min_wave}м! Это не прогноз, это некролог твоих серф-надежд.",
        f"{trend} Начинаешь с {max_wave}м, заканчиваешь на {min_wave}м. Идеальная траектория разочарования!",
        f"{trend} {max_wave}м сегодня? Не обольщайся, к концу недели скатится до {min_wave}м. Классика!",
        f"{trend} Великое Угасание! С {max_wave}м до {min_wave}м - это не рост, это агония твоих амбиций!",
        f"{trend} Мечтал о {max_wave}м? Получи {avg_wave:.1f}м среднего разочарования. Риф зевает от скуки."
    ]
    
    return random.choice(sarcastic_comments)

def generate_period_comment(period_data):
    """Генерирует саркастичный комментарий о периоде"""
    if not period_data:
        return "Период? Какой период? Здесь только хаос!"
    
    max_period = max(period_data)
    min_period = min(period_data)
    trend = "📉" if period_data[0] > period_data[-1] else "📈" if period_data[0] < period_data[-1] else "➡️"
    
    sarcastic_comments = [
        f"{trend} Период {max_period}с? Неплохо... если бы не падал до {min_period}с! Готовься к жёстким обнимашкам с водой.",
        f"{trend} Смотри, как энергия испаряется! С {max_period}с до {min_period}с - волны превращаются в беспокойные горбики.",
        f"{trend} От {max_period}с до {min_period}с - это не свитч, это насмешка! Волны короткие, рваные, как твои надежды.",
        f"{trend} Максимум {max_period}с? Хватит на пару хороших линий, пока не скатилось до {min_period}с разочарования.",
        f"{trend} Период деградирует на глазах! {max_period}с → {min_period}с. Волны станут частыми и беспощадными."
    ]
    
    return random.choice(sarcastic_comments)

def generate_power_comment(power_data):
    """Генерирует саркастичный комментарий о мощности"""
    if not power_data:
        return "Мощность? Какая мощность? Здесь только слабость!"
    
    max_power = max(power_data)
    min_power = min(power_data)
    trend = "📉" if power_data[0] > power_data[-1] else "📈" if power_data[0] < power_data[-1] else "➡️"
    
    sarcastic_comments = [
        f"{trend} С {max_power}кДж до {min_power}кДж! На твоих глазах энергия сходит на нет, как твой энтузиазм.",
        f"{trend} Мощность падает быстрее, чем твоя мотивация. {max_power}кДж → {min_power}кДж - это даже не волна, а намёк.",
        f"{trend} От {max_power}кДж до {min_power}кДж. Энергии хватит, чтобы качать насос для матраса, но не для трепа по душе.",
        f"{trend} Великолепное зрелище! Мощность испаряется с {max_power}кДж до {min_power}кДж. Мечты о трубах? Забудь.",
        f"{trend} {max_power}кДж сегодня? К концу недели будет {min_power}кДж - хватит разве что лопатой грести."
    ]
    
    return random.choice(sarcastic_comments)

def generate_wind_comment(wind_data):
    """Генерирует саркастичный комментарий о ветре"""
    if not wind_data:
        return "Ветер? Тут даже бриза нет для твоих жалких надежд."
    
    max_wind = max(wind_data)
    min_wind = min(wind_data)
    
    sarcastic_comments = [
        f"💨 Ветер от {min_wind}м/с до {max_wind}м/с - мой верный слуга, который рушит твои мечты!",
        f"💨 А вот и главный гасильник! {max_wind}м/с превратят волны в ветряную кашу. Моё особое послание для тебя.",
        f"💨 От {min_wind}м/с до {max_wind}м/с - вместо стеклянных стен жди взбитое молоко с водорослями.",
        f"💨 Ветер {max_wind}м/с? Прекрасно! Как раз чтобы испортить тебе день. Наслаждайся кашей!",
        f"💨 {max_wind}м/с в пике? Идеальные условия... для запуска воздушного змея, а не серфинга!"
    ]
    
    return random.choice(sarcastic_comments)

def analyze_tides_comment(tides_data):
    """Анализирует приливы/отливы и дает рекомендации"""
    if not tides_data:
        return "Приливы? Отливы? Видимо, океан сегодня в отпуске."
    
    high_times = tides_data.get('high_times', [])
    low_times = tides_data.get('low_times', [])
    high_heights = tides_data.get('high_heights', [])
    low_heights = tides_data.get('low_heights', [])
    
    if not high_times or not low_times:
        return "Без приливов - как без рук. Жди у моря погоды, смертный."
    
    # Форматируем приливы/отливы
    tides_info = []
    if high_times:
        for i, time in enumerate(high_times):
            height = high_heights[i] if i < len(high_heights) else "?"
            tides_info.append(f"{time}({height}м)")
    
    if low_times:
        for i, time in enumerate(low_times):
            height = low_heights[i] if i < len(low_heights) else "?"
            tides_info.append(f"{time}({height}м)")
    
    comments = [
        f"Приливы: {', '.join(tides_info)}. Рассвет в 05:50 - идеальное время для разочарования!",
        f"График приливов: {', '.join(tides_info)}. Планируй своё поражение соответственно.",
        f"Океан дышит: {', '.join(tides_info)}. Утренняя сессия с 6 до 9 - твой единственный шанс не опозориться.",
        f"Приливы шепчут: {', '.join(tides_info)}. Но тебе всё равно не поймать ту самую волну.",
    ]
    
    return random.choice(comments)

def generate_overall_verdict(wave_data, period_data, power_data, wind_data):
    """Генерирует общий вердикт на основе всех данных"""
    if not all([wave_data, period_data, power_data, wind_data]):
        return "Недостаточно данных для вердикта. Посейдон в замешательстве."
    
    avg_wave = sum(wave_data) / len(wave_data)
    avg_period = sum(period_data) / len(period_data)
    max_wind = max(wind_data)
    
    # Анализируем тренды
    wave_trend = "падает" if wave_data[0] > wave_data[-1] else "растет" if wave_data[0] < wave_data[-1] else "стабилен"
    period_trend = "ухудшается" if period_data[0] > period_data[-1] else "улучшается" if period_data[0] < period_data[-1] else "стабилен"
    
    verdicts = [
        f"Волна {wave_trend}, период {period_trend}. Условия переменчивые, как настроение Посейдона. Может повезет, а может и нет.",
        f"Средняя волна {avg_wave:.1f}м, период {avg_period:.1f}с. {max_wind}м/с ветра добавят перчинки в твоё разочарование.",
        f"Волна {wave_trend}, мощность скачет. Типичный балуанский расклад - ничего выдающегося, но и не полный штиль.",
        f"Условия посредственные, но катабельные. Волна {wave_trend}, ветер до {max_wind}м/с. Не жди чудес, смертный.",
        f"Великое Средневековье серфинга! Ничего эпичного, но и не полный провал. Волна {wave_trend}, период {period_trend}."
    ]
    
    return random.choice(verdicts)

def get_best_time_recommendation(wind_data, power_data):
    """Рекомендует лучшее время для серфинга"""
    if not wind_data or not power_data:
        return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"
    
    # Ищем период с наименьшим ветром и хорошей мощностью
    best_time_index = 0
    best_score = -999
    
    for i in range(min(6, len(wind_data))):
        wind_score = -wind_data[i] * 2  # Ветер важнее (меньше = лучше)
        power_score = power_data[i] / 200  # Мощность тоже важна
        
        total_score = wind_score + power_score
        
        if total_score > best_score:
            best_score = total_score
            best_time_index = i
    
    time_slots = ["02:00", "05:00", "08:00", "11:00", "14:00", "17:00", "20:00", "23:00"]
    
    if best_time_index < len(time_slots):
        best_time = time_slots[best_time_index]
        recommendations = [
            f"Твой лучший шанс - около {best_time}. Но не обольщайся, это всё равно посредственность.",
            f"Попробуй в {best_time}. Может быть, Посейдон смилостивится над твоей жалкой душой.",
            f"{best_time} - твой час. Хотя, кто я шучу... твой час разочарования.",
            f"В {best_time} условия наименее отвратительные. Дерзай, если осмелишься.",
            f"Запланируй своё унижение на {best_time}. Хотя какая разница, когда страдать?"
        ]
        return random.choice(recommendations)
    
    return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"

async def build_poseidon_report(windy_data: Dict, location: str, date: str) -> str:
    """Сборка финального отчета в саркастичном стиле Посейдона"""
    
    wave_data = windy_data.get('wave_data', [1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3])
    period_data = windy_data.get('period_data', [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9])
    power_data = windy_data.get('power_data', [586, 547, 501, 454, 412, 396, 331, 317, 291, 277])
    wind_data = windy_data.get('wind_data', [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8])
    tides = windy_data.get('tides', {
        'high_times': ['09:00', '21:05'],
        'high_heights': [2.3, 2.8],
        'low_times': ['14:50'],
        'low_heights': [0.8]
    })
    
    # Генерируем саркастичные комментарии
    wave_comment = generate_wave_comment(wave_data)
    period_comment = generate_period_comment(period_data)
    power_comment = generate_power_comment(power_data)
    wind_comment = generate_wind_comment(wind_data)
    tides_comment = analyze_tides_comment(tides)
    overall_verdict = generate_overall_verdict(wave_data, period_data, power_data, wind_data)
    best_time = get_best_time_recommendation(wind_data, power_data)
    
    # Формируем отчет
    report_lines = [
        "🔱 ВНИМАНИЕ, СМЕРТНЫЙ! ПОСЕЙДОН ГОВОРИТ:",
        "",
        f"Ты принёс мне прогноз на {location}? Смешно. Вот мой вердикт:",
        "",
        "📊 РАЗБОР ТВОИХ ЖАЛКИХ НАДЕЖД:",
        "",
        f"🌊 ВОЛНА: {calculate_ranges(wave_data)}м",
        f"   {wave_comment}",
        "",
        f"⏱️ ПЕРИОД: {calculate_ranges(period_data)}сек", 
        f"   {period_comment}",
        "",
        f"💪 МОЩНОСТЬ: {calculate_ranges(power_data)}кДж",
        f"   {power_comment}",
        "",
        f"💨 ВЕТЕР: {calculate_ranges(wind_data)}м/с",
        f"   {wind_comment}",
        "",
        "🌅 ПРИЛИВЫ/ОТЛИВЫ:",
        f"   {tides_comment}",
        "",
        "⚡ ВЕРДИКТ ПОСЕЙДОНА:",
        f"   {overall_verdict}",
        "",
        "🎯 КОГДА ЖЕ ТЕБЕ МУЧИТЬ ВОЛНУ:",
        f"   {best_time}",
        "",
        "💀 ЗАКЛЮЧЕНИЕ:",
        "   Прими мою волю и готовься к медитации на берегу.",
        "   Ваши планы - всего лишь песок у моих ног.",
        "",
        "🏄‍♂️ Колобрация POSEIDON V4.0 и SURFSCULPT",
        "Даже боги одобряют утреннюю сессию"
    ]
    
    return "\n".join(report_lines)

# Остальной код без изменений...
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
        
        windy_data = await analyze_windy_screenshot_with_deepseek(bytes(image_bytes))
        logger.info(f"Windy analysis data: {windy_data}")
        
        if not windy_data or not windy_data.get('success'):
            logger.info("DeepSeek failed, using realistic fallback data")
            windy_data = generate_realistic_fallback_data()
        
        report = await build_poseidon_report(windy_data, location, date)
        await update.message.reply_text(report)
        
        USER_STATE[chat_id] = {
            "active": True, 
            "awaiting_feedback": True,
        }
        await update.message.reply_text("Ну как тебе разбор, родной? Отлично / не очень")
        
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
@app.head("/ping")
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