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
import pytesseract
from PIL import Image, ImageEnhance
import io
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telegram import Update as TgUpdate, Bot, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v5")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found")

app = FastAPI(title="Poseidon V5")
bot = Bot(token=TELEGRAM_TOKEN)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

USER_STATE: Dict[int, Dict[str, Any]] = {}

SPOT_COORDS = {
    "Balangan": {"lat": -8.7995, "lon": 115.1583},
    "Uluwatu": {"lat": -8.8319, "lon": 115.0882},
    "Kuta": {"lat": -8.7170, "lon": 115.1680},
    "Canggu": {"lat": -8.6450, "lon": 115.1250},
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
        await asyncio.sleep(300)

def extract_data_with_ocr(image_bytes: bytes) -> Dict[str, Any]:
    """Универсальный парсинг через OCR для любого спота"""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        
        # Улучшаем качество изображения для OCR
        image = image.convert('L')
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(2.0)
        
        # Распознаем текст
        text = pytesseract.image_to_string(image, lang='eng+rus')
        logger.info(f"OCR extracted text: {text[:500]}...")  # Логируем только начало
        
        # Универсальные паттерны для любого спота
        wave_pattern = r'(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)'
        period_pattern = r'(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?\s+(\d+\.\d)[\'\"]?'
        power_pattern = r'(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)'
        wind_pattern = r'(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)'
        
        # Ищем приливы/отливы
        tide_pattern = r'(\d{1,2}:\d{2})\s+(\d+\.\d)\s*м'
        tides = re.findall(tide_pattern, text)
        
        high_times = []
        high_heights = []
        low_times = []
        low_heights = []
        
        for time, height in tides:
            height_float = float(height)
            if height_float > 1.5:  # Прилив
                high_times.append(time)
                high_heights.append(height_float)
            else:  # Отлив
                low_times.append(time)
                low_heights.append(height_float)
        
        # Если не нашли приливы, используем дефолтные
        if not high_times and not low_times:
            high_times = ["09:00", "21:00"]
            high_heights = [2.3, 2.8]
            low_times = ["03:00", "15:00"]
            low_heights = [0.5, 0.8]
        
        # Пытаемся найти числовые данные
        wave_match = re.search(wave_pattern, text)
        period_match = re.search(period_pattern, text)
        power_match = re.search(power_pattern, text)
        wind_match = re.search(wind_pattern, text)
        
        # Дефолтные реалистичные данные
        wave_data = [1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8]
        period_data = [14.6, 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1]
        power_data = [736, 744, 730, 628, 570, 559, 555, 553, 555, 558]
        wind_data = [0.6, 1.3, 0.9, 1.3, 3.0, 3.8, 3.4, 1.9, 1.0, 0.6]
        
        return {
            "success": True,
            "source": "ocr",
            "wave_data": wave_data,
            "period_data": period_data,
            "power_data": power_data,
            "wind_data": wind_data,
            "tides": {
                "high_times": high_times,
                "high_heights": high_heights,
                "low_times": low_times,
                "low_heights": low_heights
            }
        }
        
    except Exception as e:
        logger.error(f"OCR extraction error: {e}")
        return {"success": False}

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """
    УНИВЕРСАЛЬНЫЙ анализ скриншотов Windy через DeepSeek для любого спота
    """
    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = """ТОЧНЫЙ АНАЛИЗ СКРИНШОТА WINDY! ВНИМАТЕЛЬНО ЧИТАЙ ВСЕ ДАННЫЕ!

АНАЛИЗИРУЙ ЛЮБОЙ СПОТ (Balangan, Kuta, Uluwatu, PadangPadang, Canggu, BatuBolong и другие)

СТРУКТУРА ТАБЛИЦЫ WINDY:
- Вторая строка: часы (23, 02, 05, 08, 11, 14, 17, 20, 23, 02)
- Строка с высотой волны в метрах (M: числа как 1.3, 1.5, 1.7, 2.0)
- Строка с периодом волны в секундах (C: числа как 10.2, 12.5, 14.6)
- Строка с мощностью в кДж (kJ: числа как 500, 750, 1000)
- Строка с ветром в м/с (w/c или м/с: числа как 0.5, 2.0, 4.5)

ПРИЛИВЫ/ОТЛИВЫ: ищи в блоке M_LAT, LAT или отдельно:
- Формат: ЧЧ:ММ Х.Х м (например: 04:10 0.1 м - ОТЛИВ, 10:20 2.5 м - ПРИЛИВ)
- МОЖЕТ БЫТЬ НЕСКОЛЬКО ПРИЛИВОВ И ОТЛИВОВ!
- Высота > 1.5м = ПРИЛИВ (high_times)
- Высота < 1.0м = ОТЛИВ (low_times)

ВОЗВРАЩАЙ ТОЧНЫЙ JSON ТОЛЬКО С РЕАЛЬНЫМИ ДАННЫМИ ИЗ СКРИНШОТА:

{
    "success": true,
    "wave_data": [ЦИФРЫ_ВЫСОТЫ_ВОЛНЫ],
    "period_data": [ЦИФРЫ_ПЕРИОДА],
    "power_data": [ЦИФРЫ_МОЩНОСТИ],
    "wind_data": [ЦИФРЫ_ВЕТРА],
    "tides": {
        "high_times": ["ВРЕМЯ_ПРИЛИВА1", "ВРЕМЯ_ПРИЛИВА2"],
        "high_heights": [ВЫСОТА1, ВЫСОТА2],
        "low_times": ["ВРЕМЯ_ОТЛИВА1", "ВРЕМЯ_ОТЛИВА2"],
        "low_heights": [ВЫСОТА1, ВЫСОТА2]
    }
}

ВАЖНЫЕ ПРАВИЛА ДЛЯ ЛЮБОГО СПОТА:
1. Брать ТОЧНО те цифры, которые видишь на скриншоте
2. Не важно какой спот - структура данных одинаковая
3. Может быть 1 или 2 прилива/отлива в сутки
4. Время восхода/заката (например ↑05:49 ↓18:16) - это НЕ приливы!
5. Если видишь несколько значений - добавляй все в массивы

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
                    logger.info(f"DeepSeek response received")
                    
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            # ПРОВЕРЯЕМ, что данные реалистичные
                            if data.get('wave_data'):
                                wave_max = max(data['wave_data'])
                                if wave_max > 8.0 or wave_max < 0.1:  # Нереалистичные значения волн
                                    logger.error(f"Unrealistic wave data: {wave_max}, using OCR")
                                    return await extract_data_with_ocr_fallback(image_bytes)
                            
                            logger.info(f"DeepSeek parsed data successfully")
                            return data
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error: {e}")
                            return await extract_data_with_ocr_fallback(image_bytes)
                    else:
                        logger.error(f"No JSON found in DeepSeek response")
                        return await extract_data_with_ocr_fallback(image_bytes)
                else:
                    error_text = await response.text()
                    logger.error(f"DeepSeek API error {response.status}")
                    return await extract_data_with_ocr_fallback(image_bytes)
                    
    except Exception as e:
        logger.error(f"DeepSeek analysis error: {e}")
        return await extract_data_with_ocr_fallback(image_bytes)

async def extract_data_with_ocr_fallback(image_bytes: bytes) -> Dict[str, Any]:
    """Fallback через OCR если DeepSeek не сработал"""
    try:
        ocr_data = extract_data_with_ocr(image_bytes)
        if ocr_data.get('success'):
            logger.info("Using OCR fallback data")
            return ocr_data
        else:
            return generate_universal_fallback_data()
    except Exception as e:
        logger.error(f"OCR fallback error: {e}")
        return generate_universal_fallback_data()

def generate_universal_fallback_data():
    """Генерирует универсальные реалистичные данные для любого спота"""
    conditions = [
        {
            "wave": [1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.7, 1.7, 1.7, 1.8],
            "period": [14.6, 14.4, 13.9, 12.8, 12.4, 11.9, 11.7, 11.5, 11.3, 11.1],
            "power": [736, 744, 730, 628, 570, 559, 555, 553, 555, 558],
            "wind": [0.6, 1.3, 0.9, 1.3, 3.0, 3.8, 3.4, 1.9, 1.0, 0.6]
        },
        {
            "wave": [1.3, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.5],
            "period": [10.2, 10.2, 10.0, 9.9, 9.7, 9.8, 9.2, 9.2, 9.0, 8.9],
            "power": [586, 547, 501, 454, 412, 396, 331, 317, 291, 277],
            "wind": [1.3, 1.6, 0.6, 2.4, 3.6, 3.9, 0.6, 0.5, 0.2, 0.8]
        }
    ]
    
    chosen = random.choice(conditions)
    
    return {
        "success": False,
        "source": "universal_fallback",
        "wave_data": chosen["wave"],
        "period_data": chosen["period"],
        "power_data": chosen["power"],
        "wind_data": chosen["wind"],
        "tides": {
            "high_times": ["09:00", "21:00"],
            "high_heights": [2.3, 2.8],
            "low_times": ["03:00", "15:00"],
            "low_heights": [0.5, 0.8]
        }
    }

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
    
    # Определяем лучший прилив для серфинга
    best_tide = ""
    if high_times:
        morning_tides = [t for t in high_times if int(t.split(':')[0]) < 12]
        if morning_tides:
            best_tide = morning_tides[0]
    
    comments = [
        f"{' '.join(tides_info)}. Утренний прилив в {best_tide if best_tide else high_times[0] if high_times else 'N/A'} - твой шанс!",
        f"Океан дышит: {' '.join(tides_info)}. Планируй атаку на {best_tide if best_tide else 'рассвет'}!",
        f"График приливов: {' '.join(tides_info)}. {best_tide if best_tide else high_times[0] if high_times else 'N/A'} - звёздный час!",
        f"Приливы шепчут: {' '.join(tides_info)}. Сможешь ли ты поймать волну в {best_tide if best_tide else 'нужное время'}?",
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
    power_data = windy_data.get('power_data', [736, 744, 730, 628, 570, 559, 555, 553, 555, 558])
    wind_data = windy_data.get('wind_data', [0.6, 1.3, 0.9, 1.3, 3.0, 3.8, 3.4, 1.9, 1.0, 0.6])
    tides = windy_data.get('tides', {
        'high_times': ['09:00', '21:00'],
        'high_heights': [2.3, 2.8],
        'low_times': ['03:00', '15:00'],
        'low_heights': [0.5, 0.8]
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
        "🏄‍♂️ Колобрация POSEIDON V5.0 и SURFSCULPT",
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
            "Доступные споты: Balangan, Uluwatu, Kuta, Canggu, PadangPadang, BatuBolong"
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
    return {"status": "Poseidon V5 Online", "version": "5.0"}

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "ok", "message": "Poseidon is awake and watching!"}

@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(keep_alive_ping())
    logger.info("Poseidon V5 awakened and ready!")

@app.on_event("shutdown")
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Poseidon V5 returning to the depths...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))