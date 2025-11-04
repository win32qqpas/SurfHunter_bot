import os
import re
import json
import logging
import asyncio
import random
import base64
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from io import BytesIO

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image, ImageEnhance, ImageFilter

from telegram import Update as TgUpdate, Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v7")

# 🔐 КОНФИГУРАЦИЯ API КЛЮЧЕЙ
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found")

app = FastAPI(title="Poseidon V7")
bot = Bot(token=TELEGRAM_TOKEN)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

USER_STATE: Dict[int, Dict[str, Any]] = {}

# 🗺️ СЛОВАРЬ СПОТОВ БАЛИ (координаты для Windy API)
BALI_SPOTS = {
    "uluwatu": {"lat": -8.8282, "lng": 115.0861, "name": "Uluwatu"},
    "balangan": {"lat": -8.7909, "lng": 115.1264, "name": "Balangan Beach"},
    "kuta": {"lat": -8.7222, "lng": 115.1721, "name": "Kuta Beach"},
    "canggu": {"lat": -8.6465, "lng": 115.1381, "name": "Canggu"},
    "padangpadang": {"lat": -8.8296, "lng": 115.0847, "name": "Padang Padang"},
    "batubolong": {"lat": -8.6519, "lng": 115.1258, "name": "Batu Bolong"},
    "bingin": {"lat": -8.8150, "lng": 115.0864, "name": "Bingin"},
    "impossibles": {"lat": -8.8264, "lng": 115.0858, "name": "Impossibles"},
    "dreamland": {"lat": -8.8064, "lng": 115.1225, "name": "Dreamland"},
    "greenbowl": {"lat": -8.8242, "lng": 115.1564, "name": "Green Bowl"},
    "nyangnyang": {"lat": -8.8500, "lng": 115.0917, "name": "Nyang Nyang"},
    "suluban": {"lat": -8.8314, "lng": 115.0853, "name": "Suluban"},
    "keramas": {"lat": -8.6500, "lng": 115.3500, "name": "Keramas"},
    # 🔥 НОВЫЕ СПОТЫ:
    "balisoul": {"lat": -8.8000, "lng": 115.2167, "name": "Bali Soul"},
    "nusadua": {"lat": -8.7947, "lng": 115.2350, "name": "Nusa Dua"},
    "nikobali": {"lat": -8.6800, "lng": 115.2600, "name": "Niko Bali"}, 
    "balikutareef": {"lat": -8.7200, "lng": 115.1700, "name": "Bali Kuta Reef"}
}

# 🔥 ОБЩИЙ ПРОМТ ДЛЯ ПАРСИНГА
PARSING_PROMPT = """ТЫ - ТОЧНЫЙ ПАРСЕР СКРИНШОТОВ WINDY. ИЗВЛЕКИ ДАННЫЕ ИЗ ТАБЛИЦЫ:

ПРАВИЛА:
1. ИЩИ ГЛАВНУЮ ТАБЛИЦУ С ЧАСАМИ: 23, 02, 05, 08, 11, 14, 17, 20, 23, 02
2. ДАННЫЕ ИЗ СТРОК: "M"(высота волны), "C"(период), "KJ"(мощность), "м/с"(ветер)
3. ПРИЛИВЫ ИЗ БЛОКА "М_ЦАТ"

ВОЗВРАЩАЙ ТОЛЬКО JSON:
{
    "wave_data": [1.6, 1.7, 1.8, ...],
    "period_data": [14.7, 14.3, 13.6, ...], 
    "power_data": [1151, 1179, 1134, ...],
    "wind_data": [1.1, 0.7, 0.2, ...],
    "tides": {
        "high_times": ["10:20", "22:10"],
        "high_heights": [2.5, 3.2],
        "low_times": ["04:10", "16:00"],
        "low_heights": [0.1, 0.7]
    }
}"""

async def fetch_windy_api_data(spot_name: str, date: str) -> Dict[str, Any]:
    """Получение данных напрямую с Windy API"""
    try:
        spot = BALI_SPOTS.get(spot_name.lower())
        if not spot:
            logger.warning(f"❌ Spot {spot_name} not found in database")
            return None
        
        # Конвертируем дату в timestamp
        target_date = datetime.strptime(date, "%Y-%m-%d")
        start_ts = int(target_date.timestamp())
        end_ts = int((target_date + timedelta(days=1)).timestamp())
        
        # Параметры для Windy API
        params = {
            'lat': spot['lat'],
            'lon': spot['lng'],
            'model': 'gfs',
            'parameters': ['waves', 'wind'],
            'levels': ['surface'],
            'key': 'your_windy_api_key_here'  # Нужно получить на windy.com
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://api.windy.com/api/point-forecast/v2',
                params=params,
                timeout=20
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    
                    # Парсим данные волн и ветра
                    wave_heights = []
                    wave_periods = [] 
                    wind_speeds = []
                    
                    if 'waves' in data:
                        for hour_data in data['waves'][:10]:  # Берем первые 10 часов
                            wave_heights.append(round(hour_data.get('waveHeight', 0), 1))
                            wave_periods.append(round(hour_data.get('wavePeriod', 0), 1))
                    
                    if 'wind' in data:
                        for hour_data in data['wind'][:10]:
                            wind_speeds.append(round(hour_data.get('speed', 0), 1))
                    
                    logger.info(f"✅ Windy API data fetched for {spot_name}")
                    return {
                        "wave_data": wave_heights,
                        "period_data": wave_periods,
                        "wind_data": wind_speeds,
                        "power_data": [],  # Windy не дает мощность напрямую
                        "tides": {},  # Приливы нужно получать отдельно
                        "source": "windy_api"
                    }
                else:
                    logger.warning(f"⚠️ Windy API error: {response.status}")
                    return None
                    
    except Exception as e:
        logger.error(f"❌ Windy API fetch error: {e}")
        return None

async def parse_with_openai(image_bytes: bytes) -> Dict[str, Any]:
    """Парсинг скриншота через OpenAI"""
    if not OPENAI_API_KEY:
        return None
        
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-4-vision-preview",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PARSING_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            "max_tokens": 2000,
            "temperature": 0.1
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
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
                        data["source"] = "openai_vision"
                        logger.info("✅ OpenAI parsing successful")
                        return data
                        
        return None
        
    except Exception as e:
        logger.error(f"❌ OpenAI parsing error: {e}")
        return None

async def parse_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """Парсинг скриншота через DeepSeek"""
    if not DEEPSEEK_API_KEY:
        return None
        
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
                        {"type": "text", "text": PARSING_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
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
                        data["source"] = "deepseek_vision"
                        logger.info("✅ DeepSeek parsing successful")
                        return data
                        
        return None
        
    except Exception as e:
        logger.error(f"❌ DeepSeek parsing error: {e}")
        return None

def calculate_data_quality_score(data: Dict) -> int:
    """Оценка качества данных (0-100 баллов)"""
    score = 0
    
    # Проверяем полноту данных
    for key in ['wave_data', 'period_data', 'wind_data']:
        if data.get(key) and len(data[key]) >= 6:
            score += 20
    
    # Проверяем приливы
    if data.get('tides'):
        tides = data['tides']
        if tides.get('high_times') and tides.get('low_times'):
            score += 20
    
    # Проверяем реалистичность значений
    if data.get('wave_data'):
        max_wave = max(data['wave_data'])
        if 0.5 <= max_wave <= 5.0:
            score += 10
    
    if data.get('period_data'):
        max_period = max(data['period_data'])
        if 5.0 <= max_period <= 20.0:
            score += 10
    
    return score

def merge_triple_ai_data(openai_data: Dict, deepseek_data: Dict, windy_data: Dict) -> Dict[str, Any]:
    """УМНОЕ СЛИЯНИЕ ДАННЫХ ОТ ТРЕХ ИСТОЧНИКОВ"""
    sources = [
        (openai_data, "OpenAI"),
        (deepseek_data, "DeepSeek"), 
        (windy_data, "Windy API")
    ]
    
    # Оцениваем качество каждого источника
    scored_sources = []
    for data, name in sources:
        if data:
            score = calculate_data_quality_score(data)
            scored_sources.append((data, name, score))
            logger.info(f"📊 {name} quality score: {score}")
    
    if not scored_sources:
        return generate_dynamic_fallback_data()
    
    # Выбираем лучший источник
    best_data, best_name, best_score = max(scored_sources, key=lambda x: x[2])
    
    logger.info(f"🏆 Best data source: {best_name} (score: {best_score})")
    
    # Создаем merged данные на основе лучшего источника
    merged = {
        "success": True,
        "source": f"triple_merge_{best_name}",
        "wave_data": best_data.get('wave_data', []),
        "period_data": best_data.get('period_data', []),
        "power_data": best_data.get('power_data', []),
        "wind_data": best_data.get('wind_data', []),
        "tides": best_data.get('tides', {})
    }
    
    # Дополняем недостающие данные из других источников
    for data, name, score in scored_sources:
        if name != best_name:
            for key in ['wave_data', 'period_data', 'power_data', 'wind_data']:
                if not merged[key] and data.get(key):
                    merged[key] = data[key]
                    logger.info(f"🔧 Filled {key} from {name}")
    
    return merged

async def generate_poseidon_response(final_data: Dict, location: str, date: str) -> str:
    """Генерация финального ответа через DeepSeek с сравнением источников"""
    
    # Подготовка данных для генерации
    data_summary = {
        "location": BALI_SPOTS.get(location.lower(), {}).get('name', location),
        "date": date,
        "wave_range": calculate_ranges(final_data.get('wave_data', [])),
        "period_range": calculate_ranges(final_data.get('period_data', [])),
        "power_range": calculate_ranges(final_data.get('power_data', [])),
        "wind_range": calculate_ranges(final_data.get('wind_data', [])),
        "tides": final_data.get('tides', {}),
        "data_source": final_data.get('source', 'unknown')
    }
    
    # 🔥 УЛУЧШЕННЫЙ ПРОМТ ДЛЯ ГЕНЕРАЦИИ
    generation_prompt = f"""
ТЫ - БОГ ПОСЕЙДОН. СГЕНЕРИРУЙ УЛЬТРА-САРКАСТИЧНЫЙ ОТВЕТ О СЕРФИНГЕ С УПОМИНАНИЕМ ИСТОЧНИКОВ ДАННЫХ.

ДАННЫЕ ДЛЯ РАЗБОРА (источник: {data_summary['data_source']}):
📍 Место: {data_summary['location']}
📅 Дата: {data_summary['date']}
🌊 Волна: {data_summary['wave_range']}м
⏱️ Период: {data_summary['period_range']}сек
💪 Мощность: {data_summary['power_range']}кДж  
💨 Ветер: {data_summary['wind_range']}м/с
🌅 Приливы: {json.dumps(data_summary['tides'], ensure_ascii=False)}

ФОРМАТ ОТВЕТА (СОБЛЮДАЙ ТОЧНО!):

🔱 УСЛАШАЛ ТВОЮ ПРОСЬБУ, БРО:

Ты опять принес мне прогноз на {data_summary['location']}? [ОЧЕНЬ САРКАСТИЧНЫЙ КОММЕНТАРИЙ]. 
Серьёзно ? {data_summary['data_source']} и вот вердикт:

📊 РАЗБОР ТВОИХ НАДЕЖД НА УСПЕХ:

🌊 ВОЛНА: {data_summary['wave_range']}м
   [ЭМОЦИЯ] [САРКАСТИЧНЫЙ КОММЕНТАРИЙ О ВОЛНЕ + СРАВНЕНИЕ С ДРУГИМИ ИСТОЧНИКАМИ]

⏱️ ПЕРИОД: {data_summary['period_range']}сек
   [ЭМОЦИЯ] [САРКАСТИЧНЫЙ КОММЕНТАРИЙ О ПЕРИОДЕ] 

💪 МОЩНОСТЬ: {data_summary['power_range']}кДж
   [ЭМОЦИЯ] [САРКАСТИЧНЫЙ КОММЕНТАРИЙ О МОЩНОСТИ]

💨 ВЕТЕР: {data_summary['wind_range']}м/с
   [ЭМОЦИЯ] [САРКАСТИЧНЫЙ КОММЕНТАРИЙ О ВЕТРЕ + НАПРАВЛЕНИЕ]

🌅 ПРИЛИВЫ/ОТЛИВЫ:
   [ПОДРОБНОЕ ОПИСАНИЕ ПРИЛИВОВ С САРКАЗМОМ И РЕКОМЕНДАЦИЯМИ]

⚡ ВЕРДИКТ ПОСЕЙДОНА:
   [ОБЩАЯ ОЦЕНКА С ЮМОРОМ И СРАВНЕНИЕМ ИСТОЧНИКОВ ДАННЫХ]

🎯 КОГДА ЖЕ ТЕБЕ МУЧИТЬ ВОЛНУ:
   [ТОЧНАЯ РЕКОМЕНДАЦИЯ ПО ВРЕМЕНИ С ИРОНИЕЙ И ОБОСНОВАНИЕМ]

💀 ЗАКЛЮЧЕНИЕ:
   [МЕГА-ДРАМАТИЧНОЕ ЗАКЛЮЧЕНИЕ С УПОМИНАНИЕМ ТОЧНОСТИ ДАННЫХ]

🏄‍♂️ Колобрация POSEIDON V4.0 | SURFSCULPT
Даже боги доверяют перекрестной проверке данных!

ПРАВИЛА:
- БУДЬ ЭКСТРЕМАЛЬНО САРКАСТИЧНЫМ И ДРАМАТИЧНЫМ
- УПОМЯНИ ФАКТ ИСПОЛЬЗОВАНИЯ НЕСКОЛЬКИХ ИСТОЧНИКОВ ДАННЫХ
- ИСПОЛЬЗУЙ ЭМОЦИИ: 📉🔄📈😫🫤🔥💀🌪️🎯
- СОХРАНИ ВСЕ ЗАГОЛОВКИ И СТРУКТУРУ
- ДОБАВЬ ЮМОР ПРО ТОЧНОСТЬ ДАННЫХ
"""

    # Пробуем DeepSeek для генерации
    if DEEPSEEK_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": generation_prompt}],
                "temperature": 0.9,  # Высокая температура для максимальной креативности
                "max_tokens": 2000
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=25
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        content = result["choices"][0]["message"]["content"]
                        logger.info("✅ DeepSeek triple-AI response generated")
                        return content
                        
        except Exception as e:
            logger.error(f"❌ DeepSeek generation failed: {e}")
    
    # Запасной вариант
    return await build_poseidon_report(final_data, location, date)

async def analyze_windy_screenshot_triple_ai(image_bytes: bytes, spot_name: str, date: str) -> Dict[str, Any]:
    """ТРОЙНОЙ АНАЛИЗ: OpenAI + DeepSeek + Windy API"""
    logger.info("🔄 Запуск ТРОЙНОГО AI анализа...")
    start_time = time.time()
    
    # Параллельный сбор данных из трех источников
    openai_task = parse_with_openai(image_bytes)
    deepseek_task = parse_with_deepseek(image_bytes) 
    windy_task = fetch_windy_api_data(spot_name, date)
    
    openai_data, deepseek_data, windy_data = await asyncio.gather(
        openai_task, deepseek_task, windy_task, return_exceptions=True
    )
    
    # Обработка исключений
    if isinstance(openai_data, Exception):
        logger.error(f"OpenAI parsing exception: {openai_data}")
        openai_data = None
    if isinstance(deepseek_data, Exception):
        logger.error(f"DeepSeek parsing exception: {deepseek_data}")
        deepseek_data = None
    if isinstance(windy_data, Exception):
        logger.error(f"Windy API exception: {windy_data}")
        windy_data = None
    
    # Умное слияние данных от трех источников
    final_data = merge_triple_ai_data(openai_data, deepseek_data, windy_data)
    
    total_time = time.time() - start_time
    logger.info(f"✅ ТРОЙНОЙ анализ завершен за {total_time:.1f}с")
    
    return final_data

# ОБНОВЛЕННАЯ handle_photo ФУНКЦИЯ
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = USER_STATE.get(chat_id, {})
    
    if not state.get("active"):
        await update.message.reply_text("🔱Посейдон в ярости! Разыгрываешь меня???!!!!")
        return

    try:
        await update.message.reply_text("🔱 УСЛЫШАЛ ТВОЮ ПРОСЬБУ, БРО! Сейчас поднимем для тебя, родной, со дна рукописи, 📜надеюсь не отсырели!")
        
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        caption = update.message.caption or ""
        location, date = parse_caption_for_location_date(caption)
        
        if not location:
            location = "uluwatu"
        
        # 🔥 ТРОЙНОЙ АНАЛИЗ
        windy_data = await analyze_windy_screenshot_triple_ai(bytes(image_bytes), location, date)
        
        # 🔥 УМНАЯ ГЕНЕРАЦИЯ ОТВЕТА С УЧЕТОМ ВСЕХ ИСТОЧНИКОВ
        report = await generate_poseidon_response(windy_data, location, date)
        await update.message.reply_text(report)
        
        USER_STATE[chat_id] = {
            "active": True, 
            "awaiting_feedback": True,
        }
        await update.message.reply_text("Ну как тебе МЕГА-разбор, смертный? Отлично / не очень")
        
    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await update.message.reply_text("🔱 Посейдон в ярости! Что-то пошло не так. Попробуй ещё раз.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    chat_id = update.effective_chat.id
    text = (update.message.text or "").lower().strip()

    if "посейдон на связь" in text.lower():
        USER_STATE[chat_id] = {"active": True}
        spot_list = ", ".join([spot['name'] for spot in BALI_SPOTS.values()])
        await update.message.reply_text(
            f"🔱 Посейдон тут, смертный!\n\n"
            f"Давай свой скриншот прогноза с подписью в формате:\n"
            f"`Balangan 2025-11-06`\n\n"
            f"Доступные споты: {spot_list}"
        )
        return

    state = USER_STATE.get(chat_id, {})
    if state.get("awaiting_feedback"):
        if "отлично" in text:
            await update.message.reply_text("Ну так боги😇 Хорошей катки! Жду новый скриншот!")
        elif "не очень" in text:
            await update.message.reply_text("А не пора бы уже встать с дивана и катнуть? Жду новый скриншот!")
        else:
            await update.message.reply_text("Жду новый скриншот с прогнозом! 🏄‍♂️")
        
        USER_STATE[chat_id] = {"active": True, "awaiting_feedback": False}
        logger.info(f"Bot ready for new screenshot in chat {chat_id}")
        return

    if not state.get("active"):
        return

    await update.message.reply_text(
        "Отправь скриншот Windy с подписью в формате: `спот дата`\n"
        "Например: `uluwatu 2025-11-06`"
    )

# 🔥 ОБНОВЛЕННЫЙ ПРОМТ ДЛЯ ГЕНЕРАЦИИ ОТВЕТА
GENERATION_PROMPT_TEMPLATE = """
ТЫ - БОГ ПОСЕЙДОН. СГЕНЕРИРУЙ УЛЬТРА-САРКАСТИЧНЫЙ ОТВЕТ В СТРОГОМ ФОРМАТЕ.

ДАННЫЕ:
📍 Место: {location}
📅 Дата: {date}
🌊 Волна: {wave_range}м
⏱️ Период: {period_range}сек
💪 Мощность: {power_range}кДж  
💨 Ветер: {wind_range}м/с
🌅 Приливы: {tides_info}

ФОРМАТ ОТВЕТА (СОБЛЮДАЙ ТОЧНО!):

🔱 УСЛЫШАЛ ТВОЮ ПРОСЬБУ, БРО:

Ты опять принёс мне прогноз на {location}?
{random_sarcastic_comment}

📊 РАЗБОР ТВОИХ НАДЕЖД НА УСПЕХ:

🌊 ВОЛНА: {wave_range}м
   {wave_comment}

⏱️ ПЕРИОД: {period_range}сек
   {period_comment}

💪 МОЩНОСТЬ: {power_range}кДж
   {power_comment}

💨 ВЕТЕР: {wind_range}м/с
   {wind_comment}

🌅 ПРИЛИВЫ/ОТЛИВЫ:
   Океан дышит.
🔹 Прилив: {high_tides}
🔹 Отлив: {low_tides}

⚡ ВЕРДИКТ ПОСЕЙДОНА:
{overall_verdict}

🎯 КОГДА ЖЕ ТЕБЕ МУЧИТЬ ВОЛНУ:
   {best_time} - твой час славы... или очередного разочарования.

💀 ЗАКЛЮЧЕНИЕ:
Прими неизбежное.
Ты — лишь статистика в моих приливах.
Не жди вдохновения. Жди сет.

🏄‍♂️ Колобрация POSEIDON V4.0 и SURFSCULPT
Серфинг — это не спорт. Это переговоры с богом на волне.

ПРАВИЛА:
- СОХРАНИ ВСЕ ЗАГОЛОВКИ И СТРУКТУРУ ТОЧНО
- БУДЬ САРКАСТИЧНЫМ И ДРАМАТИЧНЫМ
- ИСПОЛЬЗУЙ ТОЛЬКО ПРЕДОСТАВЛЕННЫЕ ДАННЫЕ
"""

def format_tides_for_prompt(tides_data):
    """Форматирует приливы для промта"""
    if not tides_data:
        return "Нет данных о приливах"
    
    high_times = tides_data.get('high_times', [])
    high_heights = tides_data.get('high_heights', [])
    low_times = tides_data.get('low_times', [])
    low_heights = tides_data.get('low_heights', [])
    
    high_tides = []
    for i, time in enumerate(high_times):
        height = high_heights[i] if i < len(high_heights) else "?"
        high_tides.append(f"{time} ({height} м)")
    
    low_tides = []
    for i, time in enumerate(low_times):
        height = low_heights[i] if i < len(low_heights) else "?"
        low_tides.append(f"{time} ({height} м)")
    
    return ", ".join(high_tides), ", ".join(low_tides)

# 🔥 ДОБАВЛЯЕМ ФУНКЦИИ ДЛЯ САРКАСТИЧНЫХ КОММЕНТАРИЕВ
def generate_sarcastic_intro(location):
    """Генерирует саркастичное вступление"""
    comments = [
        "Серьёзно? Опять это место?",
        "Очередной день, очередные иллюзии...",
        "Надеюсь, волны интереснее твоего выбора спота!",
        "Снова ты... и снова {location}... скучно.",
        "Мои оракулы зевают от предсказуемости!"
    ]
    return random.choice(comments).format(location=location)

def generate_sarcastic_verdict(wave_data, period_data, wind_data):
    """Генерирует саркастичный вердикт"""
    if not all([wave_data, period_data, wind_data]):
        return "Данные как твои планы - неполные и запутанные."
    
    avg_wave = sum(wave_data) / len(wave_data)
    avg_period = sum(period_data) / len(period_data)
    max_wind = max(wind_data)
    
    verdicts = []
    
    if avg_wave < 1.0:
        verdicts.extend([
            "Мелко, но бодро. Идеально для тренировки... падений.",
            "Волны как твои амбиции - почти незаметны.",
            "Подходит для серфинга... если ты морская свинка."
        ])
    elif avg_wave < 1.5:
        verdicts.extend([
            "Неплохо для начинающего. Если не считать, что ты 'уже 3 года начинающий'.",
            "Волны есть, навыков - предсказуемо нет.",
            "Достойно! Если ты не я, конечно."
        ])
    else:
        verdicts.extend([
            "Океан проснулся! Надеюсь, ты тоже.",
            "Серьёзные волны для несерьёзного серфера.",
            "Мощно! Жаль, что не про тебя."
        ])
    
    # Добавляем комментарии про период
    if avg_period > 12:
        verdicts.append("Длинный период — как твои обещания 'встать пораньше'.")
    elif avg_period < 8:
        verdicts.append("Короткий период — как твое терпение.")
    
    # Добавляем комментарии про ветер
    if max_wind > 4.0:
        verdicts.append("Ветер норм, но не поможет, если у тебя руки как у краба.")
    
    return random.choice(verdicts)

# 🔥 ОБНОВЛЯЕМ ФУНКЦИЮ ГЕНЕРАЦИИ ОТВЕТА
async def generate_poseidon_response(final_data: Dict, location: str, date: str) -> str:
    """Генерация финального ответа в новом формате"""
    
    # Подготовка данных
    spot_name = BALI_SPOTS.get(location.lower(), {}).get('name', location)
    wave_range = calculate_ranges(final_data.get('wave_data', []))
    period_range = calculate_ranges(final_data.get('period_data', []))
    power_range = calculate_ranges(final_data.get('power_data', []))
    wind_range = calculate_ranges(final_data.get('wind_data', []))
    
    # Форматируем приливы
    high_tides, low_tides = format_tides_for_prompt(final_data.get('tides', {}))
    
    # Генерируем комментарии
    sarcastic_intro = generate_sarcastic_intro(spot_name)
    wave_comment = generate_wave_comment(final_data.get('wave_data', []))
    period_comment = generate_period_comment(final_data.get('period_data', []))
    power_comment = generate_power_comment(final_data.get('power_data', []))
    wind_comment = generate_wind_comment(final_data.get('wind_data', []))
    overall_verdict = generate_sarcastic_verdict(
        final_data.get('wave_data', []),
        final_data.get('period_data', []), 
        final_data.get('wind_data', [])
    )
    best_time = get_best_time_recommendation(
        final_data.get('wind_data', []),
        final_data.get('power_data', [])
    )
    
    # Формируем ответ
    response = f"""🔱 УСЛЫШАЛ ТВОЮ ПРОСЬБУ, БРО:

Ты опять принёс мне прогноз на {spot_name}?
{sarcastic_intro}

📊 РАЗБОР ТВОИХ НАДЕЖД НА УСПЕХ:

🌊 ВОЛНА: {wave_range}м
   {wave_comment}

⏱️ ПЕРИОД: {period_range}сек
   {period_comment}

💪 МОЩНОСТЬ: {power_range}кДж
   {power_comment}

💨 ВЕТЕР: {wind_range}м/с
   {wind_comment}

🌅 ПРИЛИВЫ/ОТЛИВЫ:
   Океан дышит.
🔹 Прилив: {high_tides}
🔹 Отлив: {low_tides}

⚡ ВЕРДИКТ ПОСЕЙДОНА:
{overall_verdict}

🎯 КОГДА ЖЕ ТЕБЕ МУЧИТЬ ВОЛНУ:
   {best_time} - твой час славы... или очередного разочарования.

💀 ЗАКЛЮЧЕНИЕ:
Прими неизбежное.
Ты — лишь статистика в моих приливах.
Не жди вдохновения. Жди сет.

🏄‍♂️ Колобрация POSEIDON V4.0 и SURFSCULPT
Серфинг — это не спорт. Это переговоры с богом на волне."""
    
    return response

def parse_caption_for_location_date(caption: Optional[str]):
    """Парсит подпись для извлечения локации и даты"""
    if not caption:
        return "uluwatu", str(datetime.utcnow().date())
    
    parts = caption.strip().split()
    if not parts:
        return "uluwatu", str(datetime.utcnow().date())
    
    location = parts[0].lower()
    date = parts[1] if len(parts) > 1 else str(datetime.utcnow().date())
    
    # Проверяем, есть ли спот в нашем словаре
    if location not in BALI_SPOTS:
        location = "uluwatu"  # дефолтный спот
    
    return location, date

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    chat_id = update.effective_chat.id
    text = (update.message.text or "").lower().strip()

    if "посейдон на связь" in text.lower():
        USER_STATE[chat_id] = {"active": True}
        spot_list = "\n".join([f"• {spot['name']}" for spot in BALI_SPOTS.values()])
        await update.message.reply_text(
            f"🔱 Посейдон тут, смертный!\n\n"
            f"Давай свой скриншот прогноза с подписью в формате:\n"
            f"`balangan 2024-11-06`\n\n"
            f"Доступные споты:\n{spot_list}\n\n"
            f"Я проверю данные через 3 источника: OpenAI + DeepSeek + Windy API!"
        )
        return

    state = USER_STATE.get(chat_id, {})
    if state.get("awaiting_feedback"):
        if "отлично" in text:
            await update.message.reply_text("Ну так боги😇 Хорошей катки! Жду новый скриншот!")
        elif "не очень" in text:
            await update.message.reply_text("А не пора бы уже встать с дивана и катнуть? Жду новый скриншот!")
        else:
            await update.message.reply_text("Жду новый скриншот с прогнозом! 🏄‍♂️")
        
        # Сбрасываем состояние ожидания фидбека, но оставляем бота активным
        USER_STATE[chat_id] = {"active": True, "awaiting_feedback": False}
        logger.info(f"Bot ready for new screenshot in chat {chat_id}")
        return

    # Если бот не активен и не ждет фидбек - игнорируем сообщения
    if not state.get("active"):
        return

    # Если бот активен, но получено непонятное сообщение
    await update.message.reply_text(
        "Отправь скриншот Windy с подписью в формате: `спот дата`\n"
        "Например: `uluwatu 2025-11-06`"
    )

# РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# FASTAPI ЭНДПОИНТЫ
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
    return {
        "status": "Poseidon V8 Online", 
        "version": "8.0",
        "features": "Triple-AI Analysis (OpenAI + DeepSeek + Windy API)",
        "spots_available": len(BALI_SPOTS)
    }

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok", "message": "Poseidon is awake and watching the waves!"}

@app.get("/spots")
async def get_spots():
    """Возвращает список доступных спотов"""
    return {
        "spots": {name: data["name"] for name, data in BALI_SPOTS.items()},
        "total": len(BALI_SPOTS)
    }

# ЗАПУСК ПРИЛОЖЕНИЯ
@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(keep_alive_ping())
    logger.info("🏄‍♂️ Poseidon V8 awakened and ready for triple-AI analysis!")
    logger.info(f"📍 Available spots: {len(BALI_SPOTS)}")

@app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("🌊 Poseidon V8 returning to the depths...")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)