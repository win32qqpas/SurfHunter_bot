# app.py — Poseidon v7.2 (Deep Hybrid OCR)
import os
import re
import json
import base64
import asyncio
import logging
from typing import Dict, Any, List, Optional

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ========== НАСТРОЙКА ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poseidon_v7")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN not found")

app = FastAPI(title="Poseidon v7.2 OCR", version="7.2")

# ========== УТИЛИТЫ ==========

def extract_json_from_text(text: str) -> Dict[str, Any]:
    """Извлекает JSON из ответа модели"""
    try:
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            json_str = match.group(0)
            json_str = re.sub(r',\s*([\]}])', r'\1', json_str)
            return json.loads(json_str)
    except Exception as e:
        logger.error(f"JSON extraction error: {e}")
    return {"success": False, "error": "No valid JSON found"}

def validate_surf_data(data: Dict[str, Any]) -> bool:
    """Проверяет корректность и диапазоны данных"""
    if not data.get('success'):
        return False

    valid = any([
        data.get('wave_data'),
        data.get('period_data'),
        data.get('power_data'),
        data.get('wind_data')
    ])
    if not valid:
        return False

    try:
        waves = data.get('wave_data', [])
        if waves and (max(waves) > 7.0 or min(waves) < 0.1):
            logger.warning(f"⚠️ Нереалистичные волны: {waves}")
        return True
    except Exception:
        return True

# ========== DEEPSEEK OCR ==========

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    """Анализирует скриншот Windy и возвращает структурированные данные"""
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
Ты — точный OCR-анализатор Windy. Извлекай ТОЛЬКО реальные числа и подписи с экрана.
Не добавляй ни одного лишнего символа вне JSON.

---
# Инструкции:

1️⃣ Найди почасовые значения (02, 05, 08, 11, 14, 17, 20, 23)
2️⃣ Извлекай только видимые данные:

- "M" или "м" → высота волны (м)
- "C" или "с" → период волны (сек)
- "kJ" → мощность (кДж)
- "m/s" → скорость ветра
- "LAT" или формат "09:45 2.4 м" → приливы/отливы

3️⃣ Не выдумывай. Если чего-то нет — оставь [].
4️⃣ Не комментируй. Возврати чистый JSON.

---
# Формат вывода:

{
  "success": true,
  "wave_data": [1.2, 1.1, 1.3],
  "period_data": [8.9, 9.1, 9.2],
  "power_data": [217, 205, 192],
  "wind_data": [1.0, 0.8, 1.2],
  "tides": {
    "high_times": ["09:45"],
    "high_heights": [2.4],
    "low_times": ["04:10"],
    "low_heights": [0.1]
  }
}
"""

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
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 2000
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json=payload,
                timeout=45
            ) as response:

                if response.status != 200:
                    err = await response.text()
                    logger.error(f"DeepSeek error: {response.status} {err}")
                    return {"success": False, "error": f"API error {response.status}"}

                result = await response.json()
                content = result["choices"][0]["message"]["content"]

                data = extract_json_from_text(content)
                if validate_surf_data(data):
                    return data
                else:
                    return {"success": False, "error": "Invalid surf data"}

    except asyncio.TimeoutError:
        return {"success": False, "error": "DeepSeek timeout"}
    except Exception as e:
        logger.error(f"DeepSeek exception: {e}")
        return {"success": False, "error": str(e)}

# ========== TELEGRAM ==========

@app.post("/webhook")
async def webhook(request: Request):
    try:
        update = await request.json()
        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")

        if not chat_id:
            return JSONResponse({"ok": True})

        if "photo" not in message:
            await send_telegram_message(chat_id, "📸 Отправь скриншот из Windy 🌊")
            return JSONResponse({"ok": True})

        file_id = message["photo"][-1]["file_id"]
        file_info = await get_telegram_file(file_id)
        file_path = file_info["result"]["file_path"]

        image_bytes = await download_telegram_file(file_path)
        await send_telegram_message(chat_id, "🌀 Анализирую скриншот Windy...")

        result = await analyze_windy_screenshot_with_deepseek(image_bytes)

        if result.get("success"):
            report = format_surf_report(result)
            await send_telegram_message(chat_id, report, markdown=True)
        else:
            await send_telegram_message(chat_id, f"❌ Ошибка анализа: {result.get('error')}")

        return JSONResponse({"ok": True})

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse({"ok": False, "error": str(e)})

# ========== TELEGRAM UTILITIES ==========

async def get_telegram_file(file_id: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile"
    params = {"file_id": file_id}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            return await resp.json()

async def download_telegram_file(file_path: str) -> bytes:
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()

async def send_telegram_message(chat_id: int, text: str, markdown: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown" if markdown else None
    }
    async with aiohttp.ClientSession() as session:
        await session.post(url, json=payload)

# ========== ФОРМАТИРОВАНИЕ ==========
def format_surf_report(data: Dict[str, Any]) -> str:
    waves = data.get('wave_data', [])
    periods = data.get('period_data', [])
    powers = data.get('power_data', [])
    winds = data.get('wind_data', [])
    tides = data.get('tides', {})

    report = "🌊 *АНАЛИЗ WINDY:*\n\n"

    if waves:
        report += f"📏 Волны: {min(waves)}–{max(waves)}м\n"
    if periods:
        report += f"⏱ Период: {min(periods)}–{max(periods)}с\n"
    if powers:
        report += f"⚡ Энергия: {min(powers)}–{max(powers)}кДж\n"
    if winds:
        report += f"💨 Ветер: {min(winds)}–{max(winds)}м/с\n"

    if tides.get('high_times'):
        report += "\n🌅 *Приливы:*\n"
        for i, t in enumerate(tides['high_times']):
            h = tides['high_heights'][i] if i < len(tides['high_heights']) else '?'
            report += f"• {t} — {h}м\n"

    report += f"\n✅ Извлечено значений:\n"
    report += f"• Волны: {len(waves)}\n"
    report += f"• Период: {len(periods)}\n"
    report += f"• Энергия: {len(powers)}\n"
    report += f"• Ветер: {len(winds)}"

    return report

# ========== HEALTH ==========
@app.get("/")
async def root():
    return {"status": "Poseidon v7.2 OCR running 🌊"}

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "Poseidon v7.2"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))