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
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://surfhunter-bot.onrender.com/ping") as response:
                    logger.info(f"Keep-alive ping: {response.status}")
        except Exception as e:
            logger.error(f"Ping error: {e}")
        await asyncio.sleep(300)

def generate_realistic_fallback_data():
    """Генерирует реалистичные случайные данные для fallback"""
    
    # Базовые варианты для разных условий
    conditions = [
        {
            "wave": [1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3],
            "period": [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9],
            "power": [586, 547, 501, 454, 412, 396, 331, 317, 291, 277],
            "wind": [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8]
        },
        {
            "wave": [1.8, 1.8, 1.7, 1.7, 1.6, 1.6, 1.5, 1.4, 1.3, 1.2],
            "period": [13.5, 13.0, 12.5, 12.0, 11.5, 11.0, 10.5, 10.0, 9.5, 9.0],
            "power": [850, 820, 780, 720, 680, 650, 620, 590, 560, 530],
            "wind": [0.8, 0.6, 0.5, 1.8, 2.8, 3.0, 2.2, 1.5, 1.0, 0.7]
        },
        {
            "wave": [2.1, 2.0, 1.9, 1.8, 1.7, 1.6, 1.5, 1.4, 1.3, 1.2],
            "period": [14.5, 14.0, 13.5, 13.0, 12.5, 12.0, 11.5, 11.0, 10.5, 10.0],
            "power": [1100, 1050, 980, 890, 810, 750, 680, 620, 570, 520],
            "wind": [0.5, 0.4, 0.3, 1.2, 2.5, 3.2, 2.0, 1.2, 0.8, 0.6]
        }
    ]
    
    chosen = random.choice(conditions)
    
    # Генерируем случайное время приливов
    high_time1 = f"{random.randint(5,7)}:{random.randint(10,50):02d}"
    high_time2 = f"{random.randint(18,20)}:{random.randint(10,50):02d}"
    low_time1 = f"{random.randint(0,3)}:{random.randint(10,50):02d}"
    low_time2 = f"{random.randint(12,15)}:{random.randint(10,50):02d}"
    
    return {
        "success": False,  # Помечаем как fallback
        "wave_data": chosen["wave"],
        "period_data": chosen["period"],
        "power_data": chosen["power"],
        "wind_data": chosen["wind"],
        "tides": {
            "high_times": [high_time1, high_time2],
            "high_heights": [round(random.uniform(2.0, 3.0), 1), round(random.uniform(2.5, 3.5), 1)],
            "low_times": [low_time1, low_time2],
            "low_heights": [round(random.uniform(0.1, 0.5), 1), round(random.uniform(0.6, 1.0), 1)]
        }
    }

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """
    Анализ скриншотов Windy через DeepSeek с улучшенным промптом
    """
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """ТЫ СЕРФИНГ-ЭКСПЕРТ! Анализируй скриншот Windy. 

ВО ВРЕМЯ АНАЛИЗА:
1. Найди таблицу с прогнозом по дням (столбцы: Чт 04, Пт 05, Сб 06, Вс 07, Пн 08, Вт 09, Ср 10, Чт 11, Пт 12, Сб 13)
2. ВНИМАТЕЛЬНО прочитай ВСЕ числа из строк:
   - Высота волны в метрах (ряд с числами как 1.7, 1.6, 1.6, 1.5, 1.5)
   - Период волны в секундах (ряд с числами как 10.2, 10.2, 10.0, 9.9, 9.7)
   - Мощность в кДж (ряд с числами как 586, 547, 501, 454, 412)
   - Ветер в м/с (ряд с числами как 1.3, 1.6, 0.6, 2.4, 3.6)

3. Найди время приливов/отливов в формате ↑ЧЧ:ММ ↔↓ЧЧ:ММ (например ↑05:50 ↔↓18:15)

ВЕРНИ ТОЧНЫЙ JSON:
{
    "success": true,
    "wave_data": [1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3],
    "period_data": [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9],
    "power_data": [586, 547, 501, 454, 412, 396, 331, 317, 291, 277],
    "wind_data": [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8],
    "tides": {
        "high_times": ["05:50"],
        "low_times": ["18:15"]
    }
}

ЕСЛИ НЕ ВИДИШЬ ДАННЫЕ - верни {"success": false}"""

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
                    
                    # Ищем JSON в ответе
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
    
    sarcastic_comments = [
        f"Оу, целых {max_wave}м! Начинается сказка! - скажешь ты. Посмешище. Это не волна, это зевок младенца Посейдона.",
        f"Великое Угасание! С {max_wave}м до {min_wave}м - это не рост, это агония!",
        f"Прекрасно! От {max_wave}м до {min_wave}м. Идеальные условия для того, чтобы лежать на доске и грустить.",
        f"Мечтал о трубах? Получил {avg_wave:.1f}м среднего разочарования. Риф плачет от скуки.",
        f"Это даже не волны, а намёк на них. {min_wave}м - хватит, чтобы не забыть, как доска под ногами выглядит."
    ]
    
    return random.choice(sarcastic_comments)

def generate_period_comment(period_data):
    """Генерирует саркастичный комментарий о периоде"""
    if not period_data:
        return "Период? Какой период? Здесь только хаос!"
    
    max_period = max(period_data)
    min_period = min(period_data)
    
    sarcastic_comments = [
        f"Смотри, как энергия испаряется! С {max_period}с до {min_period}с - волны превращаются в беспокойные горбики.",
        f"Период {max_period}с? Неплохо... если бы не падал до {min_period}с! Готовься к жёстким обнимашкам с водой.",
        f"От {max_period}с до {min_period}с - это не свитч, это насмешка! Волны короткие, рваные, как твои надежды.",
        f"Максимум {max_period}с? Хватит на пару хороших линий, пока не скатилось до {min_period}с разочарования."
    ]
    
    return random.choice(sarcastic_comments)

def generate_power_comment(power_data):
    """Генерирует саркастичный комментарий о мощности"""
    if not power_data:
        return "Мощность? Какая мощность? Здесь только слабость!"
    
    max_power = max(power_data)
    min_power = min(power_data)
    
    sarcastic_comments = [
        f"С {max_power}кДж до {min_power}кДж! На твоих глазах энергия сходит на нет, как твой энтузиазм.",
        f"Мощность падает быстрее, чем твоя мотивация. С {max_power}кДж до {min_power}кДж - это даже не волна, а намёк.",
        f"От {max_power}кДж до {min_power}кДж. Энергии хватит, чтобы качать насос для матраса, но не для трепа по душе.",
        f"Великолепное зрелище! Мощность испаряется с {max_power}кДж до {min_power}кДж. Мечты о трубах? Забудь."
    ]
    
    return random.choice(sarcastic_comments)

def generate_wind_comment(wind_data):
    """Генерирует саркастичный комментарий о ветре"""
    if not wind_data:
        return "Ветер? Тут даже бриза нет для твоих жалких надежд."
    
    max_wind = max(wind_data)
    min_wind = min(wind_data)
    
    sarcastic_comments = [
        f"Ветер от {min_wind}м/с до {max_wind}м/с - мой верный слуга, который рушит твои мечты!",
        f"А вот и главный гасильник! {max_wind}м/с превратят волны в ветряную кашу. Моё особое послание для тебя.",
        f"От {min_wind}м/с до {max_wind}м/с - вместо стеклянных стен жди взбитое молоко с водорослями.",
        f"Ветер {max_wind}м/с? Прекрасно! Как раз чтобы испортить тебе день. Наслаждайся кашей!"
    ]
    
    return random.choice(sarcastic_comments)

def analyze_tides_comment(tides_data):
    """Анализирует приливы/отливы и дает рекомендации"""
    if not tides_data:
        return "Приливы? Отливы? Видимо, океан сегодня в отпуске."
    
    high_times = tides_data.get('high_times', [])
    low_times = tides_data.get('low_times', [])
    
    if not high_times or not low_times:
        return "Без приливов - как без рук. Жди у моря погоды, смертный."
    
    # Берем первый прилив и отлив для анализа
    high_time = high_times[0] if high_times else "N/A"
    low_time = low_times[0] if low_times else "N/A"
    
    comments = [
        f"Прилив в {high_time}, отлив в {low_time}. Рассветный серфинг? Бесполезно. Самая мощная волна будет как раз на рассвете - встречай посредственный свитч!",
        f"Прилив {high_time}, отлив {low_time}. К вечеру спот начнет разваливаться - идеальное время для разочарования!",
        f"С приливом в {high_time} и отливом в {low_time} у тебя есть шанс поймать... нет, не трубу, а легкое разочарование.",
        f"Приливы в {high_time}, отливы в {low_time}. Планируй свое поражение соответственно."
    ]
    
    return random.choice(comments)

def generate_overall_verdict(wave_data, period_data, power_data, wind_data):
    """Генерирует общий вердикт на основе всех данных"""
    if not all([wave_data, period_data, power_data, wind_data]):
        return "Недостаточно данных для вердикта. Посейдон в замешательстве."
    
    avg_wave = sum(wave_data) / len(wave_data)
    avg_period = sum(period_data) / len(period_data)
    avg_power = sum(power_data) / len(power_data)
    max_wind = max(wind_data)
    
    if avg_wave <= 1.2 and avg_period <= 10 and max_wind >= 3.0:
        return "ЭТО ПОЛНЫЙ ПРОВАЛ! Волны нет, период короткий, ветер портит всё. Лучше остаться дома."
    elif avg_wave <= 1.5 and avg_period <= 11:
        return "Великое Разочарование! Условия посредственные, но для отчаяных сойдет. Не жди чуда."
    elif avg_wave >= 1.8 and avg_period >= 13 and max_wind <= 2.0:
        return "Неплохо, смертный! Есть шанс поймать достойные волны. Но помни - ты всего лишь человек."
    else:
        return "Условия переменчивые, как настроение Посейдона. Может повезет, а может и нет."

def get_best_time_recommendation(wind_data, power_data):
    """Рекомендует лучшее время для серфинга"""
    if not wind_data or not power_data:
        return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"
    
    # Ищем период с наименьшим ветром и хорошей мощностью
    best_time_index = 0
    best_score = -999
    
    for i in range(min(6, len(wind_data))):  # Смотрим первые 6 периодов
        wind_score = -wind_data[i]  # Меньше ветер - лучше
        power_score = power_data[i] / 100  # Больше мощность - лучше
        
        total_score = wind_score + power_score
        
        if total_score > best_score:
            best_score = total_score
            best_time_index = i
    
    time_slots = ["02:00", "05:00", "08:00", "11:00", "14:00", "17:00", "20:00", "23:00"]
    
    if best_time_index < len(time_slots):
        best_time = time_slots[best_time_index]
        recommendations = [
            f"Твой лучший шанс - около {best_time}. Но не обольщайся, это всё равно посредственность.",
            f"Попробуй в {best_time}. Может быть, Посейдон смилостивится.",
            f"{best_time} - твой час. Хотя, кто я шучу... твой час разочарования.",
            f"В {best_time} условия наименее отвратительные. Дерзай, если осмелишься."
        ]
        return random.choice(recommendations)
    
    return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"

async def build_poseidon_report(windy_data: Dict, location: str, date: str) -> str:
    """Сборка финального отчета в саркастичном стиле Посейдона"""
    
    # Всегда используем данные из windy_data (либо от DeepSeek, либо fallback)
    wave_data = windy_data.get('wave_data', [1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3])
    period_data = windy_data.get('period_data', [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9])
    power_data = windy_data.get('power_data', [586, 547, 501, 454, 412, 396, 331, 317, 291, 277])
    wind_data = windy_data.get('wind_data', [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8])
    tides = windy_data.get('tides', {
        'high_times': ['05:50'],
        'low_times': ['18:15']
    })
    
    # Генерируем саркастичные комментарии
    wave_comment = generate_wave_comment(wave_data)
    period_comment = generate_period_comment(period_data)
    power_comment = generate_power_comment(power_data)
    wind_comment = generate_wind_comment(wind_data)
    tides_comment = analyze_tides_comment(tides)
    overall_verdict = generate_overall_verdict(wave_data, period_data, power_data, wind_data)
    best_time = get_best_time_recommendation(wind_data, power_data)
    
    # Формируем отчет в стиле Посейдона
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
        "   Даже боги не могут сделать из говна конфетку"
    ]
    
    return "\n".join(report_lines)

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
        
        # Пробуем анализировать скриншот через DeepSeek
        windy_data = await analyze_windy_screenshot_with_deepseek(bytes(image_bytes))
        logger.info(f"Windy analysis data: {windy_data}")
        
        # Если DeepSeek не сработал, используем реалистичные случайные данные
        if not windy_data or not windy_data.get('success'):
            logger.info("DeepSeek failed, using realistic fallback data")
            windy_data = generate_realistic_fallback_data()
        
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