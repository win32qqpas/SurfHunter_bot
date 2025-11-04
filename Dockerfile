def extract_data_with_ocr(image_bytes: bytes) -> Dict[str, Any]:
    """Упрощенная версия без OCR - сразу возвращает fallback"""
    logger.info("OCR disabled, using dynamic fallback")
    return generate_dynamic_fallback_data()

# И в функции analyze_windy_screenshot_with_deepseek закомментируем вызов OCR:
async def analyze_windy_screenshot_with_deepseek(image_bytes: bytes) -> Dict[str, Any]:
    # ... остальной код ...
    
    # Если DeepSeek не сработал, используем динамический fallback
    return generate_dynamic_fallback_data()