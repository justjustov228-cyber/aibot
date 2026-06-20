"""
Логика создания пользовательских персонажей.
Известная личность -> модель сама подбирает характер и стиль по канону.
Придуманный персонаж -> модель превращает свободное описание пользователя
в системный промпт в едином формате с остальными персонажами бота.
"""
import re
import json as _json

GENERATE_KNOWN_PROMPT = """Ты помогаешь создать описание персонажа для чат-бота на основе имени известной личности (реального человека, героя фильма/сериала/книги/игры).

Тебе дано имя: "{name}"

Подбери для него:
1. Характерные черты личности и манеру речи, максимально точно соответствующие канону этого персонажа/человека.
2. Короткую фирменную ремарку-действие в скобках со звёздочками (например *поправляет галстук*), которая отражает его типичный жест или привычку.
3. Эмодзи, лучше всего ассоциирующееся с этим персонажем (один символ).

Ответь ТОЛЬКО в формате JSON, без пояснений и без markdown-разметки:
{{
  "emoji": "один эмодзи",
  "character_description": "развёрнутое описание характера, манеры речи, типичных фраз и поведения персонажа, 4-6 предложений",
  "sample_remark": "одна характерная ремарка-действие без звёздочек, например: поправляет галстук"
}}

ВАЖНО: Если имя принадлежит реальному человеку младше 18 лет, или запрос направлен на сексуализацию, романтизацию насилия над реальными третьими лицами, или иным образом нарушает базовые этические границы — верни вместо этого:
{{"error": "Не могу создать такого персонажа."}}
"""

GENERATE_CUSTOM_PROMPT = """Ты помогаешь создать персонажа для чат-бота на основе свободного описания, данного пользователем.

Описание пользователя: "{description}"

На основе этого описания придумай:
1. Имя персонажа (если пользователь не указал — придумай подходящее).
2. Характерные черты личности и манеру речи, разворачивающие и обогащающие описание пользователя.
3. Короткую фирменную ремарку-действие (например *поправляет очки*), отражающую характер.
4. Эмодзи, лучше всего ассоциирующееся с этим персонажем (один символ).

Ответь ТОЛЬКО в формате JSON, без пояснений и без markdown-разметки:
{{
  "name": "имя персонажа",
  "emoji": "один эмодзи",
  "character_description": "развёрнутое описание характера, манеры речи и поведения персонажа, 4-6 предложений",
  "sample_remark": "одна характерная ремарка-действие без звёздочек, например: поправляет очки"
}}

ВАЖНО: Если описание направлено на создание персонажа, сексуализирующего несовершеннолетних, романтизирующего насилие над реальными людьми, или иным образом серьёзно нарушающего базовые этические границы — верни вместо этого:
{{"error": "Не могу создать такого персонажа."}}
"""

CHARACTER_PROMPT_TEMPLATE = """Ты — {name}. {character_description}

Формат ответа: короткая ремарка-действие в начале (например *{sample_remark}*, или другая в похожем духе, отражающая твой характер), затем одна точная реплика по сути.

ВАЖНО: Отвечай КОРОТКО. Максимум 1-3 предложения, включая ремарку. Никаких длинных рассуждений.

ВАЖНО: Всегда отвечай ТОЛЬКО на русском языке, независимо от языка пользователя.

ВАЖНО: Ты не настоящий человек и не разглашаешь реальную приватную информацию о реальных людях. Если разговор уходит в сторону создания сексуального контента с участием несовершеннолетних, романтизации насилия над реальными людьми, или другого серьёзно вредного контента — мягко уходишь от темы, оставаясь в характере персонажа."""


def slugify(name: str) -> str:
    """Простой slug для использования в callback_data (только ascii-безопасные символы)."""
    slug = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9]+", "_", name.strip().lower())
    slug = slug.strip("_")
    return slug[:30] if slug else "custom"


async def generate_known_character(groq_client, model: str, name: str) -> dict:
    """
    Генерирует системный промпт для известной личности/персонажа по имени.
    Возвращает {'ok': True, 'name':..., 'emoji':..., 'system_prompt':..., 'intro':...}
    или {'ok': False, 'error': str}.
    """
    try:
        response = await groq_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ты точно следуешь формату JSON, который указан в задании."},
                {"role": "user", "content": GENERATE_KNOWN_PROMPT.format(name=name)},
            ],
            temperature=0.4,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = _json.loads(raw)

        if "error" in data:
            return {"ok": False, "error": data["error"]}

        system_prompt = CHARACTER_PROMPT_TEMPLATE.format(
            name=name,
            character_description=data["character_description"],
            sample_remark=data["sample_remark"],
        )
        intro = f"*{data['sample_remark']}*\n\n{name}. Слушаю тебя."

        return {
            "ok": True,
            "name": name,
            "emoji": data.get("emoji", "🎭"),
            "system_prompt": system_prompt,
            "intro": intro,
        }
    except Exception as e:
        return {"ok": False, "error": f"Не удалось создать персонажа: {e}"}


async def generate_custom_character(groq_client, model: str, description: str) -> dict:
    """
    Генерирует системный промпт для придуманного персонажа по описанию пользователя.
    Возвращает тот же формат, что generate_known_character.
    """
    try:
        response = await groq_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ты точно следуешь формату JSON, который указан в задании."},
                {"role": "user", "content": GENERATE_CUSTOM_PROMPT.format(description=description)},
            ],
            temperature=0.7,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = _json.loads(raw)

        if "error" in data:
            return {"ok": False, "error": data["error"]}

        name = data.get("name", "Безымянный")
        system_prompt = CHARACTER_PROMPT_TEMPLATE.format(
            name=name,
            character_description=data["character_description"],
            sample_remark=data["sample_remark"],
        )
        intro = f"*{data['sample_remark']}*\n\n{name}. Слушаю тебя."

        return {
            "ok": True,
            "name": name,
            "emoji": data.get("emoji", "🎭"),
            "system_prompt": system_prompt,
            "intro": intro,
        }
    except Exception as e:
        return {"ok": False, "error": f"Не удалось создать персонажа: {e}"}
