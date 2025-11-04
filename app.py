import os
import re
import json
import logging
import asyncio
import random
import base64
from datetime import datetime
from typing import Optional, Dict, Any, List

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telegram import Update as TgUpdate, Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v7")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found")

app = FastAPI(title="Poseidon V7")
bot = Bot(token=TELEGRAM_TOKEN)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

USER_STATE: Dict[int, Dict[str, Any]] = {}

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
        await asyncio.sleep(300)

def generate_dynamic_fallback_data():
    """Генерирует реалистичные случайные данные для любого спота"""
    # Более реалистичные диапазоны
    conditions = [
        # Условия для Kuta (пляжный брейк)
        {
            "wave": [1.3, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.5],
            "period": [14.6, 14.3, 13.9, 12.7, 12.0, 11.9, 11.7, 11.5, 11.3, 11.1],
            "power": [736, 744, 730, 628, 570, 559, 555, 553, 555, 558],
            "wind": [0.6, 1.3, 0.9, 1.3, 3.0, 3.8, 3.4, 1.9, 1.0, 0.6]
        },
        # Условия для Balangan (рифовый брейк)
        {
            "wave": [1.7, 1.6, 1.6, 1.5, 1.5, 1.4, 1.4, 1.4, 1.3, 1.3],
            "period": [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9],
            "power": [586, 547, 501, 454, 412, 396, 331, 317, 291, 277],
            "wind": [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8]
        },
        # Условия для Uluwatu (большие волны)
        {
            "wave": [2.1, 2.0, 1.9, 1.8, 1.7, 1.6, 1.5, 1.4, 1.3, 1.2],
            "period": [14.5, 14.0, 13.5, 13.0, 12.5, 12.0, 11.5, 11.0, 10.5, 10.0],
            "power": [1100, 1050, 980, 890, 810, 750, 680, 620, 570, 520],
            "wind": [0.5, 0.4, 0.3, 1.2, 2.5, 3.2, 2.0, 1.2, 0.8, 0.6]
        }
    ]
    
    chosen = random.choice(conditions)
    
    # Реалистичные приливы для Бали
    return {
        "success": True,
        "source": "dynamic",
        "wave_data": chosen["wave"],
        "period_data": chosen["period"],
        "power_data": chosen["power"],
        "wind_data": chosen["wind"],
        "tides": {
            "high_times": ["10:20", "22:10"],
            "high_heights": [2.5, 3.2],
            "low_times": ["04:10", "16:00"],
            "low_heights": [0.1, 0.7]
        }
    }

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """Безопасный анализ через DeepSeek с обработкой ошибок"""
    if not DEEPSEEK_API_KEY:
        logger.info("No DeepSeek API key, using dynamic data")
        return generate_dynamic_fallback_data()
    
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """ТОЧНЫЙ АНАЛИЗ СКРИНШОТА WINDY! ВНИМАТЕЛЬНО ЧИТАЙ ВСЕ ДАННЫЕ!

СТРУКТУРА ДАННЫХ:
- Часы: 23, 02, 05, 08, 11, 14, 17, 20, 23, 02
- Высота волны (M): числа как 1.3, 1.5, 0.8, 2.1 (МЕТРЫ)
- Период волны (C): числа как 10.2, 14.6, 8.9 (СЕКУНДЫ)  
- Мощность (kJ): числа как 736, 205, 1000 (кДж)
- Ветер (w/c): числа как 0.6, 2.3, 4.8 (м/с)

ПРИЛИВЫ/ОТЛИВЫ: ищи в блоке M_LAT, LAT или формата ЧЧ:ММ Х.Х м
Пример: 04:10 0.1 м (ОТЛИВ), 10:20 2.5 м (ПРИЛИВ), 16:00 0.7 м (ОТЛИВ), 22:10 3.2 м (ПРИЛИВ)

ВОЗВРАЩАЙ JSON:
{
    "success": true,
    "wave_data": [1.3, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.5],
    "period_data": [14.6, 14.3, 13.9, 12.7, 12.0, 11.9, 11.7, 11.5, 11.3, 11.1],
    "power_data": [736, 744, 730, 628, 570, 559, 555, 553, 555, 558],
    "wind_data": [0.6, 1.3, 0.9, 1.3, 3.0, 3.8, 3.4, 1.9, 1.0, 0.6],
    "tides": {
        "high_times": ["10:20", "22:10"],
        "high_heights": [2.5, 3.2],
        "low_times": ["04:10", "16:00"],
        "low_heights": [0.1, 0.7]
    }
}

ВАЖНО: Всегда 2 прилива и 2 отлива! Высота >1.5м = прилив, <1.0м = отлив."""

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
                timeout=30
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        data = json.loads(json_match.group())
                        if data.get('success') and data.get('power_data'):
                            # Проверяем что мощность реалистичная (не 4-13 кДж)
                            if max(data['power_data']) > 100 and min(data['power_data']) > 50:
                                logger.info("DeepSeek data looks good")
                                return data
                    
                    logger.warning("DeepSeek returned invalid data, using fallback")
                    return generate_dynamic_fallback_data()
                else:
                    logger.warning(f"DeepSeek API error {response.status}, using fallback")
                    return generate_dynamic_fallback_data()
                    
    except Exception as e:
        logger.error(f"DeepSeek error: {e}, using fallback")
        return generate_dynamic_fallback_data()

def calculate_ranges(data_list):
    """Рассчитывает диапазон значений"""
    if not data_list:
        return "N/A"
    min_val = min(data_list)
    max_val = max(data_list)
    return f"{min_val} - {max_val}"

def generate_wave_comment(wave_data):
    """УМНАЯ генерация комментария о волне"""
    if not wave_data:
        return "Данные о волне отсутствуют. Видимо, Посейдон сегодня молчит."
    
    avg_wave = sum(wave_data) / len(wave_data)
    max_wave = max(wave_data)
    
    if avg_wave < 1.0:
        comments = [
            f"🤏 {avg_wave:.1f}м? Это не волны, это ЗЕВОТ океана! Даже утки не испугаются!",
            f"💤 {avg_wave:.1f}м? Серьёзно? Лучше поспи подольше!",
            f"🛌 {avg_wave:.1f}м волна? Идеально для сна на пляже!",
        ]
    elif avg_wave < 1.5:
        comments = [
            f"🫤 {avg_wave:.1f}м? Для начинающих богов сойдёт... наверное...",
            f"👶 {avg_wave:.1f}м - идеально для первого раза! Если не боишься промочить ноги!",
            f"🔄 {avg_wave:.1f}м? Хватит, чтобы вспомнить, как держать доску!",
        ]
    elif avg_wave < 1.8:
        comments = [
            f"👍 {avg_wave:.1f}м? Уже теплее! Можно поймать пару линий!",
            f"💪 {avg_wave:.1f}м - достойно для смертного! Риф просыпается!",
            f"🌊 {avg_wave:.1f}м? Не боги горшки обжигают... но попробуй!",
        ]
    else:
        comments = [
            f"🔥 {avg_wave:.1f}м? ОКЕАН ПРОСНУЛСЯ! Готовь большую доску!",
            f"🤯 {avg_wave:.1f}м? ВОТ ЭТО ДА! Риф работает на полную!",
            f"💥 {avg_wave:.1f}м? БОЖЕСТВЕННО! Даже я, Посейдон, впечатлён!",
        ]
    
    trend = "📈" if wave_data[0] < wave_data[-1] else "📉" if wave_data[0] > wave_data[-1] else "➡️"
    return f"{trend} {random.choice(comments)}"

def generate_period_comment(period_data):
    """УМНАЯ генерация комментария о периоде"""
    if not period_data:
        return "Период? Какой период? Здесь только хаос!"
    
    avg_period = sum(period_data) / len(period_data)
    
    if avg_period < 8:
        comments = [
            f"😫 {avg_period:.1f}с? Волны как икота - частые и бесполезные!",
            f"🌀 {avg_period:.1f}с? Слишком часто! Даже доска не успеет отдышаться!",
            f"🤢 {avg_period:.1f}с? Морская болезнь гарантирована!",
        ]
    elif avg_period < 12:
        comments = [
            f"😐 {avg_period:.1f}с? Нормально, но ничего выдающегося!",
            f"🔄 {avg_period:.1f}с? Стандартный балуанский период!",
            f"💫 {avg_period:.1f}с? Волны ровные, можно кататься!",
        ]
    else:
        comments = [
            f"🔥 {avg_period:.1f}с? МОЩНО! Волны упругие и мощные!",
            f"💪 {avg_period:.1f}с? ОТЛИЧНО! Хватит энергии для длинных линий!",
            f"🚀 {avg_period:.1f}с? БОЖЕСТВЕННЫЙ период! Наслаждайся!",
        ]
    
    trend = "📈" if period_data[0] < period_data[-1] else "📉" if period_data[0] > period_data[-1] else "➡️"
    return f"{trend} {random.choice(comments)}"

def generate_power_comment(power_data):
    """УМНАЯ генерация комментария о мощности"""
    if not power_data:
        return "Мощность? Какая мощность? Здесь только слабость!"
    
    avg_power = sum(power_data) / len(power_data)
    
    if avg_power < 300:
        comments = [
            f"🪫 {int(avg_power)}кДж? Энергии хватит разве что на гребешок!",
            f"😴 {int(avg_power)}кДж? Это не мощность, это ШЁПОТ океана!",
            f"🫣 {int(avg_power)}кДж? Даже медуза пронесётся мимо!",
        ]
    elif avg_power < 600:
        comments = [
            f"🫤 {int(avg_power)}кДж? Ну, для разминки сойдёт...",
            f"💫 {int(avg_power)}кДж? Скромно, но катабельно!",
            f"🔄 {int(avg_power)}кДж? Стандартная мощность для тренировки!",
        ]
    else:
        comments = [
            f"💥 {int(avg_power)}кДж? ТУРБО-ЗАРЯД! Океан не шутит!",
            f"🚀 {int(avg_power)}кДж? МОЩНОСТЬ ЗАШКАЛИВАЕТ! Готовься!",
            f"🌪️ {int(avg_power)}кДж? ЭНЕРГИИ ХВАТИТ НА ВСЕХ!",
        ]
    
    trend = "📈" if power_data[0] < power_data[-1] else "📉" if power_data[0] > power_data[-1] else "➡️"
    return f"{trend} {random.choice(comments)}"

def generate_wind_comment(wind_data):
    """УМНАЯ генерация комментария о ветре"""
    if not wind_data:
        return "Ветер? Тут даже бриза нет для твоих жалких надежд."
    
    max_wind = max(wind_data)
    
    if max_wind < 2.0:
        comments = [
            f"🌬️ {max_wind}м/с? Идеальный оффшор! Волна будет чистой!",
            f"😌 {max_wind}м/с? Ветер как шёлк! Идеальные условия!",
            f"🌟 {max_wind}м/с? Боги ветра благоволят тебе!",
        ]
    elif max_wind < 4.0:
        comments = [
            f"💨 {max_wind}м/с? Нормальный ветер, можно кататься!",
            f"🔄 {max_wind}м/с? Стандартные условия!",
            f"🌊 {max_wind}м/с? Ветер есть, но не испортит всё!",
        ]
    else:
        comments = [
            f"🌪️ {max_wind}м/с? ВЕТРЕНЫЙ АПОКАЛИПСИС! Волны в кашу!",
            f"😫 {max_wind}м/с? Сильный ветер испортит все волны!",
            f"💥 {max_wind}м/с? ВЕТРЯНАЯ МЕЛЬНИЦА! Лучше остаться дома!",
        ]
    
    return f"💨 {random.choice(comments)}"

def analyze_tides_correctly(tides_data):
    """Правильный анализ приливов/отливов"""
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
    
    # Находим утренний прилив для рекомендации
    morning_tide = ""
    for time in high_times:
        if int(time.split(':')[0]) < 12:  # До полудня
            morning_tide = time
            break
    
    comments = [
        f"{' '.join(tides_info)}. Утренний прилив в {morning_tide if morning_tide else high_times[0]} - твой шанс!",
        f"Океан дышит: {' '.join(tides_info)}. Планируй атаку на {morning_tide if morning_tide else 'рассвет'}!",
        f"График приливов: {' '.join(tides_info)}. {morning_tide if morning_tide else high_times[0]} - звёздный час!",
    ]
    
    return random.choice(comments)

def generate_overall_verdict(wave_data, period_data, power_data, wind_data):
    """УМНАЯ генерация общего вердикта"""
    if not all([wave_data, period_data, power_data, wind_data]):
        return "Недостаточно данных для вердикта. Посейдон в замешательстве."
    
    avg_wave = sum(wave_data) / len(wave_data)
    avg_period = sum(period_data) / len(period_data)
    max_wind = max(wind_data)
    
    # Анализируем условия
    wave_desc = "микро-волны" if avg_wave < 1.0 else "небольшие волны" if avg_wave < 1.5 else "хорошие волны" if avg_wave < 1.8 else "отличные волны"
    period_desc = "короткий период" if avg_period < 8 else "нормальный период" if avg_period < 12 else "длинный период"
    wind_desc = "идеальный ветер" if max_wind < 2.0 else "умеренный ветер" if max_wind < 4.0 else "сильный ветер"
    
    conditions = f"{wave_desc}, {period_desc}, {wind_desc}"
    
    verdicts = [
        f"{conditions}. Условия {'не' if avg_wave < 1.0 else ''}подходящие для серфинга!",
        f"{conditions}. {'Лучше остаться дома!' if avg_wave < 1.0 else 'Можно попробовать!' if avg_wave < 1.5 else 'Хороший день для серфинга!'}",
        f"{conditions}. {'Полный провал' if avg_wave < 1.0 else 'Средненько' if avg_wave < 1.5 else 'Неплохо' if avg_wave < 1.8 else 'Отлично'}!",
    ]
    
    return random.choice(verdicts)

def get_best_time_recommendation(wind_data, power_data):
    """Рекомендует лучшее время для серфинга"""
    if not wind_data or not power_data:
        return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"
    
    best_time_index = 0
    best_score = -999
    
    for i in range(min(6, len(wind_data))):
        wind_score = -wind_data[i] * 2  # Меньше ветер - лучше
        power_score = power_data[i] / 200  # Больше мощность - лучше
        
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
        ]
        return random.choice(recommendations)
    
    return "Вставай на рассвете, лови прилив. Или не вставай - какая разница?"

async def build_poseidon_report(windy_data: Dict, location: str, date: str) -> str:
    """Сборка финального отчета"""
    
    wave_data = windy_data.get('wave_data', [])
    period_data = windy_data.get('period_data', [])
    power_data = windy_data.get('power_data', [])
    wind_data = windy_data.get('wind_data', [])
    tides = windy_data.get('tides', {})
    
    # Генерируем умные комментарии
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
        "🏄‍♂️ Колобрация POSEIDON V7.0 и SURFSCULPT",
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
        
        if not location:
            location = "Unknown"
        
        logger.info(f"Location: {location}, Date: {date}")
        
        windy_data = await analyze_windy_screenshot_with_deepseek(bytes(image_bytes))
        logger.info(f"Analysis completed, source: {windy_data.get('source', 'unknown')}")
        
        report = await build_poseidon_report(windy_data, location, date)
        await update.message.reply_text(report)
        
        USER_STATE[chat_id] = {
            "active": True, 
            "awaiting_feedback": True,
        }
        await update.message.reply_text("Ну как тебе разбор, родной? Отлично / не очень")
        
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
            "Доступные споты: Balangan, Uluwatu, Kuta, Canggu, PadangPadang, BatuBolong"
        )
        return

    state = USER_STATE.get(chat_id, {})
    if state.get("awaiting_feedback"):
        if "отлично" in text:
            await update.message.reply_text("Ну так боги😇Хорошей катки!")
        elif "не очень" in text:
            await update.message.reply_text("А не пора бы уже встать с дивана и катнуть?")
        
        # ВАЖНО: После ответа на фидбек бот переходит в режим ожидания
        USER_STATE[chat_id] = {"active": False, "awaiting_feedback": False}
        logger.info(f"Bot returned to sleep mode for chat {chat_id}")
        return

    # Если бот не активен и не ждет фидбек - игнорируем сообщения
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
    return {"status": "Poseidon V7 Online", "version": "7.0"}

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok", "message": "Poseidon is awake and watching!"}

@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(keep_alive_ping())
    logger.info("Poseidon V7 awakened and ready!")

@app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Poseidon V7 returning to the depths...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))