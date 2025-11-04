# app.py — Poseidon v7 (DeepSeek OCR-оптимизированный)
import os
import re
import json
import base64
import asyncio
import requests
from io import BytesIO
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

app = FastAPI()

# ========== УТИЛИТЫ ==========

def extract_json_from_text(text):
    """Извлекает чистый JSON из ответа модели"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {"success": False, "error": "Invalid JSON"}
    return {"success": False, "error": "No JSON found"}

# ========== DEEPSEEK OCR ==========

async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes):
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
Ты — точная OCR-система, которая извлекает данные только из СКРИНШОТОВ Windy.App.

❗Твоя задача: ВЫТАЩИ ВСЕ ВИДИМЫЕ ЦИФРЫ и ПОДПИСИ с экрана и структурируй их в JSON.
❗Не догадывайся и не интерпретируй — бери только то, что явно написано.
❗Если значения нет — оставь массив пустым [].
❗Не добавляй текст, эмодзи, пояснения, только JSON.

---

# Что искать на скриншоте:
- ВОЛНЫ (M, м)
- ПЕРИОД (C, с)
- МОЩНОСТЬ (kJ, кДж)
- ВЕТЕР (м/с, m/s)
- ПРИЛИВЫ (LAT, м LAT, M_LAT и время)

---

# Формат вывода:

{
  "success": true,
  "wave_data": [...],
  "period_data": [...],
  "power_data": [...],
  "wind_data": [...],
  "tides": {
    "high_times": [...],
    "high_heights": [...],
    "low_times": [...],
    "low_heights": [...]
  }
}

---

# RULES (ENGLISH):

1. Extract exact numbers visible in the screenshot — never invent.
2. Use OCR-like behavior: take only digits, decimals, and units (m, s, kJ, m/s).
3. Return only one JSON object.
4. Do not include any explanation before or after JSON.
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
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]
            }
        ],
        "temperature": 0.0,
        "max_tokens": 1500,
        "presence_penalty": 0,
        "frequency_penalty": 0
    }

    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )

    if response.status_code != 200:
        return {"success": False, "error": f"DeepSeek API error: {response.status_code}"}

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return extract_json_from_text(content)
    except Exception as e:
        return {"success": False, "error": str(e)}

# ========== TELEGRAM WEBHOOK ==========

@app.post("/webhook")
async def webhook(request: Request):
    try:
        update = await request.json()
        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")

        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            file_info = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
            ).json()
            file_path = file_info["result"]["file_path"]

            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            image_bytes = requests.get(file_url).content

            await send_message(chat_id, "🌀 Анализирую скриншот Windy...")

            result = await analyze_windy_screenshot_with_deepseek(image_bytes)

            if result.get("success"):
                waves = result.get("wave_data", [])
                periods = result.get("period_data", [])
                powers = result.get("power_data", [])
                winds = result.get("wind_data", [])

                msg = f"🌊 *Windy OCR Data Extracted:*\n\n"
                msg += f"🌊 Волна: {waves}\n"
                msg += f"⏱ Период: {periods}\n"
                msg += f"⚡️ Энергия: {powers}\n"
                msg += f"💨 Ветер: {winds}\n"

                tides = result.get("tides", {})
                if tides:
                    msg += f"\n🌅 Приливы:\n{json.dumps(tides, ensure_ascii=False, indent=2)}"

                await send_message(chat_id, msg, markdown=True)
            else:
                await send_message(chat_id, f"❌ Ошибка анализа: {result.get('error')}")

        else:
            await send_message(chat_id, "Отправь мне скриншот из Windy 🌊")

        return JSONResponse({"ok": True})

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

# ========== ОТПРАВКА В TELEGRAM ==========

async def send_message(chat_id, text, markdown=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown" if markdown else None
    }
    requests.post(url, json=payload)

# ========== HEALTH CHECK ==========

@app.get("/")
async def root():
    return {"status": "Poseidon v7 running 🌊"}