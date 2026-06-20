"""
Модуль голосовых ответов на базе Edge-TTS (бесплатно, без API-ключа).
Перед озвучкой убирает ремарки-действия (*текст в звёздочках*),
чтобы голос не зачитывал их вслух — иначе звучит неестественно.
"""
import re
import io
import edge_tts

# Голос подбирается под персонажа — мужской/женский, с разной интонацией,
# где это доступно среди голосов Edge-TTS для русского языка.
VOICE_BY_CHARACTER = {
    "aria": "ru-RU-SvetlanaNeural",
    "dexter": "ru-RU-DmitryNeural",
    "brian": "ru-RU-DmitryNeural",
    "debra": "ru-RU-SvetlanaNeural",
    "joe": "ru-RU-DmitryNeural",
    "homelander": "ru-RU-DmitryNeural",
    "mellstroy": "ru-RU-DmitryNeural",
    "heisenberg": "ru-RU-DmitryNeural",
    "eudy": "ru-RU-SvetlanaNeural",
}

DEFAULT_VOICE = "ru-RU-SvetlanaNeural"

# Убирает ремарки вида *затягивается сигаретой*, чтобы их не озвучивать.
REMARK_PATTERN = re.compile(r"\*[^*]+\*")


def strip_remarks(text: str) -> str:
    """Убирает ремарки-действия в звёздочках и лишние пробелы перед озвучкой."""
    cleaned = REMARK_PATTERN.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def get_voice_for_character(character_key: str) -> str:
    return VOICE_BY_CHARACTER.get(character_key, DEFAULT_VOICE)


async def text_to_speech(text: str, character_key: str) -> bytes:
    """
    Генерирует голосовое сообщение (OGG/Opus) из текста.
    Возвращает пустые байты, если после очистки текста для озвучки ничего не осталось.
    """
    cleaned_text = strip_remarks(text)
    if not cleaned_text:
        return b""

    voice = get_voice_for_character(character_key)
    communicate = edge_tts.Communicate(cleaned_text, voice)

    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])

    return buffer.getvalue()
