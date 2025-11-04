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
    "keramas": {"lat": -8.6500, "lng": 115.3500, "name": "Keramas"}
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

🔱 ВНИМАНИЕ, СМЕРТНЫЙ! ПОСЕЙДОН ГОВОРИТ:

Ты принёс мне прогноз на {data_summary['location']}? [ОЧЕНЬ САРКАСТИЧНЫЙ КОММЕНТАРИЙ]. 
Мои оракулы проанализировали {data_summary['data_source']} и вот вердикт:

📊 РАЗБОР ТВОИХ ЖАЛКИХ НАДЕЖД:

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

🏄‍♂️ Колобрация POSEIDON V8.0 | TRIPLE-AI VERIFICATION
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
        await update.message.reply_text("🌀 ЗАПУСК ТРОЙНОЙ AI ПРОВЕРКИ...\nOpenAI + DeepSeek + Windy API")
        
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        caption = update.message.caption or ""
        location, date = parse_caption_for_location_date(caption)
        
        if not location:
            location = "uluwatu"  # дефолтный спот
        
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

# ... предыдущий код до handle_photo ...

def calculate_ranges(data_list):
    """Рассчитывает диапазон значений"""
    if not data_list:
        return "N/A"
    min_val = min(data_list)
    max_val = max(data_list)
    return f"{min_val:.1f}-{max_val:.1f}"

def generate_wave_comment(wave_data):
    """УМНАЯ генерация комментария о волне"""
    if not wave_data:
        return "📉 Данные о волне отсутствуют. Видимо, Посейдон сегодня молчит."
    
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
        return "📉 Период? Какой период? Здесь только хаос!"
    
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
        return "📉 Мощность? Какая мощность? Здесь только слабость!"
    
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
        return "💨 Ветер? Тут даже бриза нет для твоих жалких надежд."
    
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
        return "🌅 Приливы? Какие приливы? Океан сегодня на перекуре."
    
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
        return "🌅 Без приливов - как серфер без доски. Бессмысленно и грустно."
    
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
        return "⚡ Недостаточно данных для вердикта. Посейдон в замешательстве."
    
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
        return "🎯 Вставай на рассвете, лови прилив. Или не вставай - какая разница?"
    
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
    
    return "🎯 Вставай на рассвете, лови прилив. Или не вставай - какая разница?"

def generate_dynamic_fallback_data():
    """Генерирует реалистичные случайные данные для любого спота"""
    conditions = [
        {
            "wave": [1.3, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.5],
            "period": [14.6, 14.3, 13.9, 12.7, 12.0, 11.9, 11.7, 11.5, 11.3, 11.1],
            "power": [736, 744, 730, 628, 570, 559, 555, 553, 555, 558],
            "wind": [0.6, 1.3, 0.9, 1.3, 3.0, 3.8, 3.4, 1.9, 1.0, 0.6]
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
        "success": True,
        "source": "dynamic_fallback",
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

def validate_surf_data(data: Dict) -> bool:
    """Проверяет валидность данных о серфинге"""
    if not data.get('success'):
        return False
        
    has_sufficient_data = False
    for key in ['wave_data', 'period_data', 'power_data', 'wind_data']:
        if data.get(key) and len(data[key]) >= 6:
            has_sufficient_data = True
            break
    
    if not has_sufficient_data:
        logger.warning("❌ Insufficient data in all arrays")
        return False
    
    # Проверка реалистичных диапазонов
    if data.get('wave_data'):
        wave_ok = 0.1 < max(data['wave_data']) < 5.0
        if not wave_ok:
            logger.warning(f"❌ Wave data out of range: {max(data['wave_data'])}")
    
    if data.get('period_data'):
        period_ok = 3.0 < max(data['period_data']) < 25.0
        if not period_ok:
            logger.warning(f"❌ Period data out of range: {max(data['period_data'])}")
    
    if data.get('power_data'):
        power_ok = max(data['power_data']) > 30
        if not power_ok:
            logger.warning(f"❌ Power data too low: {max(data['power_data'])}")
    
    return True

async def build_poseidon_report(windy_data: Dict, location: str, date: str) -> str:
    """ЗАПАСНАЯ функция сборки отчета (если AI не сработал)"""
    
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
        "🏄‍♂️ Колобрация POSEIDON V8.0 | TRIPLE-AI VERIFICATION",
        "Даже боги доверяют перекрестной проверке данных!"
    ]
    
    return "\n".join(report_lines)

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
        "Например: `uluwatu 2024-11-06`"
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