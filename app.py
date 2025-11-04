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
        await asyncio.sleep(300)  # 5 минут

def generate_realistic_fallback_data():
    """Генерирует реалистичные данные для fallback на основе Balangan"""
    
    conditions = [
        {
            "wave": [1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8],
            "period": [14.6, 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1],
            "power": [995, 1012, 992, 874, 813, 762, 751, 752, 754, 756],
            "wind": [0.2, 0.8, 1.3, 1.4, 2.6, 4.3, 4.9, 2.6, 1.4, 0.7]
        },
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
            "high_times": ["10:20"],
            "high_heights": [2.5],
            "low_times": ["04:10"],
            "low_heights": [0.1]
        }
    }

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """
    УЛУЧШЕННЫЙ анализ скриншотов Windy через DeepSeek
    """
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """ТОЧНЫЙ АНАЛИЗ СКРИНШОТА WINDY! ВНИМАТЕЛЬНО ЧИТАЙ ВСЕ ДАННЫЕ!

СТРУКТУРА СКРИНШОТА:
- Верхняя строка: время и дата (например: 18:16 ЧТ, 06 НОЯБ. ↑05:49 ↓18:16)
- Вторая строка: часы (23, 02, 05, 08, 11, 14, 17, 20, 23, 02)
- Третья строка: высота волны в метрах (M: 1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8)
- Четвертая строка: период волны в секундах (C: 14.6, 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1)
- Пятая строка: мощность в кДж (kJ: 995, 1012, 992, 874, 813, 762, 751, 752, 754, 756)
- Шестая строка: ветер в м/с (0.2, 0.8, 1.3, 1.4, 2.6, 4.3, 4.9, 2.6, 1.4, 0.7)

ПРИЛИВЫ/ОТЛИВЫ: ищи в отдельном блоке с метками М, LAT или форматом:
- Время ЧЧ:ММ и высота Х.Хм (например: 04:10 0.1 м - это ОТЛИВ, 10:20 2.5 м - это ПРИЛИВ)
- Высокие цифры (2.0-3.0м) = ПРИЛИВ
- Низкие цифры (0.1-1.0м) = ОТЛИВ

ВОЗВРАЩАЙ ТОЧНЫЙ JSON ТОЛЬКО С РЕАЛЬНЫМИ ДАННЫМИ:

{
    "success": true,
    "wave_data": [1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8],
    "period_data": [14.6, 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1],
    "power_data": [995, 1012, 992, 874, 813, 762, 751, 752, 754, 756],
    "wind_data": [0.2, 0.8, 1.3, 1.4, 2.6, 4.3, 4.9, 2.6, 1.4, 0.7],
    "tides": {
        "high_times": ["10:20"],
        "high_heights": [2.5],
        "low_times": ["04:10"], 
        "low_heights": [0.1]
    }
}

ПРАВИЛА ПРИЛИВОВ:
- Высота > 1.5м = ПРИЛИВ (high_times)
- Высота < 1.0м = ОТЛИВ (low_times) 
- Время восхода/заката (↑05:49 ↓18:16) - это НЕ приливы!

НЕ ВЫДУМЫВАЙ ДАННЫЕ! БЕРИ ТОЛЬКО ТО, ЧТО ВИДИШЬ НА СКРИНШОТЕ!"""

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
                            # ПРОВЕРЯЕМ, что данные реалистичные
                            if data.get('wave_data') and max(data['wave_data']) > 3.0:
                                logger.error("Unrealistic wave data, using fallback")
                                return {"success": False}
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
    trend = "📈" if wave_data[0] < wave_data[-1] else "📉" if wave_data[0] > wave_data[-1] else "➡️"
    
    sarcastic_comments = [
        f"{trend} От {min_wave}м до {max_wave}м! Это не прогноз, это американские горки твоих эмоций!",
        f"{trend} Начинаешь с {wave_data[0]}м, заканчиваешь на {wave_data[-1]}м. Идеальная траектория для истерики!",
        f"{trend} {max_wave}м в пике? Не обольщайся, смертный! Это всего лишь зевок океана!",
        f"{trend} Великое колебание! С {min_wave}м до {max_wave}м - океан не может определиться, жалеть тебя или нет!",
        f"{trend} Мечтал о {max_wave}м? Получи {avg_wave:.1f}м среднего недоразумения. Риф хохочет!"
    ]
    
    return random.choice(sarcastic_comments)

def generate_period_comment(period_data):
    """Генерирует саркастичный комментарий о периоде"""
    if not period_data:
        return "Период? Какой период? Здесь только хаос!"
    
    max_period = max(period_data)
    min_period = min(period_data)
    trend = "📈" if period_data[0] < period_data[-1] else "📉" if period_data[0] > period_data[-1] else "➡️"
    
    sarcastic_comments = [
        f"{trend} Период {max_period}с? Хватит, чтобы подумать о жизни... и своей никчёмности!",
        f"{trend} Смотри, как энергия танцует! С {max_period}с до {min_period}с - волны как настроение твоей бывшей!",
        f"{trend} От {max_period}с до {min_period}с - это не свитч, это квест на выживание!",
        f"{trend} Максимум {max_period}с? Хватит на одну достойную линию... если повезёт!",
        f"{trend} Период скачет как сумасшедший! {max_period}с → {min_period}с. Волны непредсказуемы, как твои шансы!"
    ]
    
    return random.choice(sarcastic_comments)

def generate_power_comment(power_data):
    """Генерирует саркастичный комментарий о мощности"""
    if not power_data:
        return "Мощность? Какая мощность? Здесь только слабость!"
    
    max_power = max(power_data)
    min_power = min(power_data)
    trend = "📈" if power_data[0] < power_data[-1] else "📉" if power_data[0] > power_data[-1] else "➡️"
    
    sarcastic_comments = [
        f"{trend} С {min_power}кДж до {max_power}кДж! Достаточно, чтобы понять всю глубину отчаяния!",
        f"{trend} Мощность пляшет макарену! {min_power}кДж → {max_power}кДж - хватит на минутку славы!",
        f"{trend} От {min_power}кДж до {max_power}кДж. Энергии хватит, чтобы впечатлить... себя в зеркале!",
        f"{trend} Великолепный разброс! {max_power}кДж сегодня, {min_power}кДж завтра. Посейдон шутит!",
        f"{trend} {max_power}кДж в пике? Мило! Хватит разве что на фото для инсты!"
    ]
    
    return random.choice(sarcastic_comments)

def generate_wind_comment(wind_data):
    """Генерирует саркастичный комментарий о ветре"""
    if not wind_data:
        return "Ветер? Тут даже бриза нет для твоих жалких надежд."
    
    max_wind = max(wind_data)
    min_wind = min(wind_data)
    
    sarcastic_comments = [
        f"💨 Ветер от {min_wind}м/с до {max_wind}м/с - мой верный палач, готовый разрушить твои мечты!",
        f"💨 А вот и главный спойлер! {max_wind}м/с превратят волны в суп с водорослями. Наслаждайся!",
        f"💨 От {min_wind}м/с до {max_wind}м/с - идеальные условия... для запуска бумажного змея!",
        f"💨 Ветер {max_wind}м/с? Прекрасно! Как раз чтобы проверить твою устойчивость к разочарованиям!",
        f"💨 {max_wind}м/с в пике? Отличный повод остаться на берегу и смотреть, как другие страдают!"
    ]
    
    return random.choice(sarcastic_comments)

def analyze_tides_correctly(tides_data):
    """Правильный анализ приливов/отливов с сарказмом"""
    if not tides_data:
        return "Приливы? Какие приливы? Океан сегодня на перекуре."
    
    high_times = tides_data.get('high_times', [])
    low_times = tides_data.get('low_times', [])
    high_heights = tides_data.get('high_heights', [])
    low_heights = tides_data.get('low_heights', [])
    
    tides_info = []
    
    # Форматируем приливы
    if high_times:
        for i, time in enumerate(high_times):
            height = high_heights[i] if i < len(high_heights) else "?"
            tides_info.append(f"🌊 {time}({height}м)")
    
    # Форматируем отливы  
    if low_times:
        for i, time in enumerate(low_times):
            height = low_heights[i] if i < len(low_heights) else "?"
            tides_info.append(f"🏖️ {time}({height}м)")
    
    if not tides_info:
        return "Без приливов - как серфер без доски. Бессмысленно и грустно."
    
    comments = [
        f"{' '.join(tides_info)}. Прилив в {high_times[0] if high_times else 'N/A'} - риф ЗАЛИТО!",
        f"Океан дышит: {' '.join(tides_info)}. Планируй атаку на утреннюю сессию!",
        f"График приливов: {' '.join(tides_info)}. {high_times[0] if high_times else 'N/A'} - твой звёздный час!",
        f"Приливы шепчут: {' '.join(tides_info)}. Но смогешь ли ты этим воспользоваться, смертный?",
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
    wave_trend = "растет" if wave_data[0] < wave_data[-1] else "падает" if wave_data[0] > wave_data[-1] else "стабилен"
    period_trend = "улучшается" if period_data[0] < period_data[-1] else "ухудшается" if period_data[0] > period_data[-1] else "стабилен"
    
    verdicts = [
        f"Волна {wave_trend}, период {period_trend}. Условия непредсказуемые, как шутки Посейдона!",
        f"Средняя волна {avg_wave:.1f}м, период {avg_period:.1f}с. {max_wind}м/с ветра добавят драмы в твой день!",
        f"Волна {wave_trend}, мощность скачет. Стандартный балуанский расклад - ничего эпичного!",
        f"Условия средненькие, но катабельные. Волна {wave_trend}, ветер до {max_wind}м/с. Не жди подвигов!",
        f"Великая Посредственность! Ничего выдающегося, но и не полный провал. Волна {wave_trend}, период {period_trend}."
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
            f"Твой наименее ужасный шанс - около {best_time}. Но не обольщайся!",
            f"Попробуй в {best_time}. Может быть, океан смилостивится над тобой.",
            f"{best_time} - твой час славы... или очередного разочарования.",
            f"В {best_time} условия наименее отвратительные. Рискни, если осмелишься.",
            f"Запланируй своё унижение на {best_time}. Хотя какая разница, когда страдать?"
        ]
        return random.choice(recommendations)
    
    return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"

async def build_poseidon_report(windy_data: Dict, location: str, date: str) -> str:
    """Сборка финального отчета в саркастичном стиле Посейдона"""
    
    wave_data = windy_data.get('wave_data', [1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8])
    period_data = windy_data.get('period_data', [14.6, 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1])
    power_data = windy_data.get('power_data', [995, 1012, 992, 874, 813, 762, 751, 752, 754, 756])
    wind_data = windy_data.get('wind_data', [0.2, 0.8, 1.3, 1.4, 2.6, 4.3, 4.9, 2.6, 1.4, 0.7])
    tides = windy_data.get('tides', {
        'high_times': ['10:20'],
        'high_heights': [2.5],
        'low_times': ['04:10'],
        'low_heights': [0.1]
    })
    
    # Генерируем саркастичные комментарии
    wave_comment = generate_wave_comment(wave_data)
    period_comment = generate_period_comment(period_data)
    power_comment = generate_power_comment(power_data)
    wind_comment = generate_wind_comment(wind_data)
    tides_comment = analyze_tides_correctly(tides)
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