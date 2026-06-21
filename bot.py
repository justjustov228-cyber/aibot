import asyncio
import os
import time
import urllib.parse
from dotenv import load_dotenv
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile, BotCommand
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiohttp
from db import (
    init_db, get_history, save_history, clear_history,
    get_profile, update_profile_name, add_facts, clear_profile,
    get_character, set_character,
    touch_streak, get_users_missed_yesterday,
    set_referrer_if_new, get_referral_count,
    get_custom_character_limit, get_custom_characters, get_custom_character_count,
    add_custom_character, get_custom_character_by_slug, delete_custom_character,
)
from characters import CHARACTERS, DEFAULT_CHARACTER, get_character as get_character_data
from custom_characters import (
    generate_known_character, generate_custom_character, slugify,
)
import voice
import games

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
CHANNEL_USERNAME = "@ariaaich"
ADMIN_ID = 8275553438
CARD_NUMBER = "+79303346635"
VIP_PRICE = "500₽"
BOT_USERNAME = os.getenv("BOT_USERNAME", "")  # без @, например "AriaAiBot" — для реф-ссылок

FREE_GUIDE_PATH = "guides/free_guide.pdf"
VIP_GUIDE_PATH = "guides/vip_guide.pdf"

CHARACTERS_BUTTON_TEXT = "🎭 Персонажи"

# ===================== АНТИ-СПАМ / РЕЙТ-ЛИМИТ =====================
# Простое ограничение в памяти процесса: не более RATE_LIMIT_COUNT сообщений
# за RATE_LIMIT_WINDOW секунд на пользователя. Этого достаточно для одного
# инстанса на Render free tier (WEB_CONCURRENCY=1).
RATE_LIMIT_WINDOW = 10  # секунд
RATE_LIMIT_COUNT = 5    # сообщений за окно
MIN_GAP_SECONDS = 1.2   # минимальный промежуток между двумя последовательными сообщениями

user_message_timestamps = {}  # user_id -> [timestamps]
user_last_message_time = {}   # user_id -> timestamp последнего сообщения

# Последний текстовый ответ персонажа на пользователя — для озвучки по запросу.
# user_id -> {"text": str, "character_key": str}
last_reply_by_user = {}

VOICE_REQUEST_TRIGGERS = [
    "озвучь", "озвучить", "скажи голосом", "голосом скажи",
    "озвучь голосом", "голосовое сообщение", "пришли голосом",
    "скажи это голосом", "произнеси", "озвучка",
]


def detect_voice_request(text: str) -> bool:
    lowered = text.lower().strip()
    return any(trigger in lowered for trigger in VOICE_REQUEST_TRIGGERS)

pending_vip_payments = {}

bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

openrouter_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)


# ===================== FSM ДЛЯ СОЗДАНИЯ ПЕРСОНАЖА =====================

class CreateCharacterStates(StatesGroup):
    choosing_type = State()
    waiting_known_name = State()
    waiting_custom_description = State()


# ===================== АНТИ-СПАМ =====================

def is_rate_limited(user_id: int) -> bool:
    """Возвращает True, если пользователь сейчас должен быть притормозен."""
    now = time.time()

    last_time = user_last_message_time.get(user_id, 0)
    if now - last_time < MIN_GAP_SECONDS:
        return True

    timestamps = user_message_timestamps.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    timestamps.append(now)
    user_message_timestamps[user_id] = timestamps
    user_last_message_time[user_id] = now

    return len(timestamps) > RATE_LIMIT_COUNT


RATE_LIMIT_REPLIES = [
    "*приподнимает бровь*\n\nНе спеши так. Дай мне хоть слово сказать.",
    "*выдыхает дым*\n\nПридержи коней. Секунду.",
    "*смотрит устало*\n\nОдно сообщение за раз, ладно?",
]


async def handle_rate_limit(message: Message):
    import random
    await message.answer(random.choice(RATE_LIMIT_REPLIES))


def detect_image_request(text: str):
    lowered = text.lower().strip()
    triggers = [
        "нарисуй", "сгенерируй картинку", "сгенерируй изображение", "сгенерируй фото",
        "сгенерируй", "сделай картинку", "сделай изображение", "сделай фото",
        "сгенери", "генерируй", "draw ", "картинку с", "изображение с",
    ]
    for trigger in triggers:
        if trigger in lowered:
            idx = lowered.find(trigger)
            prompt = text[idx + len(trigger):].strip()
            return prompt
    return None


async def generate_image(prompt: str) -> bytes:
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true&enhance=true"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            resp.raise_for_status()
            return await resp.read()


from groq import AsyncGroq
groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

GROQ_MODEL = "llama-3.3-70b-versatile"
OPENROUTER_MODEL = "openrouter/free"


async def call_ai(messages: list) -> str:
    """Groq — основной (быстрый), OpenRouter — fallback при лимите."""
    try:
        response = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
        )
        return response.choices[0].message.content
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "rate_limit" in err or "quota" in err:
            try:
                response = await openrouter_client.chat.completions.create(
                    model=OPENROUTER_MODEL,
                    messages=messages,
                )
                return response.choices[0].message.content
            except Exception as e2:
                err2 = str(e2).lower()
                if "429" in err2 or "rate_limit" in err2 or "quota" in err2:
                    raise Exception("rate_limit")
                raise
        raise


EXTRACT_FACTS_PROMPT = """Извлеки из сообщения пользователя факты о нём самом (имя, профессия, интересы, важные детали жизни).
Отвечай ТОЛЬКО в формате JSON, без пояснений:
{"name": "имя или пустая строка", "facts": ["факт 1", "факт 2"]}

Если в сообщении нет личной информации о пользователе — верни {"name": "", "facts": []}.
Факты должны быть короткими, по одному на строку, без лишних слов. Не выдумывай ничего, бери только то, что явно есть в тексте."""


async def extract_user_facts(user_text: str) -> dict:
    """Лёгкий вызов модели для извлечения фактов о пользователе. Безопасно проваливается в пустой результат при ошибке."""
    import json as _json
    try:
        response = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": EXTRACT_FACTS_PROMPT},
                {"role": "user", "content": user_text},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = _json.loads(raw)
        return {
            "name": data.get("name", "").strip(),
            "facts": [f.strip() for f in data.get("facts", []) if f.strip()],
        }
    except Exception:
        return {"name": "", "facts": []}


def build_profile_context(profile: dict) -> str:
    """Формирует текст для подмешивания в системный промпт."""
    if not profile["name"] and not profile["facts"]:
        return ""

    lines = ["\n\nИнформация о пользователе, с которым ты говоришь:"]
    if profile["name"]:
        lines.append(f"- Имя: {profile['name']}")
    for fact in profile["facts"]:
        lines.append(f"- {fact}")
    lines.append("\nИспользуй эту информацию естественно, не перечисляй её напрямую, а вплетай в разговор там, где это уместно.")
    return "\n".join(lines)


def main_reply_keyboard():
    """Постоянная клавиатура внизу с кнопкой выбора персонажа."""
    builder = ReplyKeyboardBuilder()
    builder.button(text=CHARACTERS_BUTTON_TEXT)
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def main_menu_keyboard():
    """Главное inline-меню: основные разделы бота, по 2 кнопки в ряд."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎭 Персонажи", callback_data="menu_characters")
    builder.button(text="➕ Создать своего", callback_data="menu_createcharacter")
    builder.button(text="🎮 Игры", callback_data="menu_games")
    builder.button(text="💎 Looksmax", callback_data="menu_looksmax")
    builder.button(text="🔗 Реферал", callback_data="menu_referral")
    builder.button(text="🔥 Серия", callback_data="menu_streak")
    builder.button(text="🧹 Очистить чат", callback_data="menu_clear")
    builder.button(text="🙈 Забыть меня", callback_data="menu_forget")
    builder.adjust(2)
    return builder.as_markup()


async def characters_inline_keyboard(user_id: int, current: str):
    """Inline-меню: встроенные персонажи + кастомные персонажи этого пользователя."""
    builder = InlineKeyboardBuilder()
    for key, data in CHARACTERS.items():
        label = data["button_label"]
        if key == current:
            label = f"✅ {label}"
        builder.button(text=label, callback_data=f"setchar_{key}")

    custom_list = await get_custom_characters(user_id)
    for c in custom_list:
        custom_key = f"custom:{c['slug']}"
        label = f"{c['emoji']} {c['name']}"
        if current == custom_key:
            label = f"✅ {label}"
        builder.button(text=label, callback_data=f"setcustom_{c['slug']}")

    builder.button(text="➕ Создать своего персонажа", callback_data="create_character_start")
    builder.adjust(1)
    return builder.as_markup()


async def resolve_character(user_id: int, character_key: str) -> dict:
    """
    Возвращает данные персонажа (встроенного или кастомного) по ключу из профиля.
    Кастомные хранятся как 'custom:<slug>'.
    """
    if character_key.startswith("custom:"):
        slug = character_key.split(":", 1)[1]
        custom = await get_custom_character_by_slug(user_id, slug)
        if custom:
            return {
                "name": custom["name"],
                "emoji": custom["emoji"],
                "system_prompt": custom["system_prompt"],
                "intro": custom["intro"],
            }
        # кастомный персонаж был удалён — откатываемся на дефолт
        return get_character_data(DEFAULT_CHARACTER)
    return get_character_data(character_key)


# ===================== ПОДПИСКА НА КАНАЛ =====================

async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def subscribe_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")
    builder.button(text="✅ Я подписался", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()


async def require_subscription(message: Message) -> bool:
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "*прищуривается, оценивающе глядя*\n\n"
            "Прежде чем мы поговорим — подпишись на канал. Таковы правила этого балагана.",
            reply_markup=subscribe_keyboard()
        )
        return False
    return True


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.edit_text(
            "*кивает, выпуская дым*\n\nХорошо. Теперь говори, что у тебя на уме."
        )
        await callback.message.answer(
            "Используй кнопку ниже, если хочешь сменить собеседника.",
            reply_markup=main_reply_keyboard()
        )
    else:
        await callback.answer("Пока не вижу тебя в подписчиках. Попробуй ещё раз.", show_alert=True)


# ===================== КОМАНДЫ =====================

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    user_id = message.from_user.id

    # Обработка реферальной ссылки: /start ref_<id>
    args = command.args
    if args and args.startswith("ref_"):
        try:
            referrer_id = int(args[4:])
            was_new = await set_referrer_if_new(user_id, referrer_id)
            if was_new:
                try:
                    new_count = await get_referral_count(referrer_id)
                    await bot.send_message(
                        referrer_id,
                        f"*довольно усмехается*\n\nПо твоей ссылке зашёл новый человек. "
                        f"Рефералов теперь: {new_count}. Открыт ещё один слот для своего персонажа — /createcharacter."
                    )
                except Exception:
                    pass
        except ValueError:
            pass

    if not await require_subscription(message):
        return

    await message.answer(
        "*затягивается сигаретой, медленно выпуская дым*\n\n"
        "Ещё одна душа забрела в этот балаган. Пиши, что у тебя на уме.",
        reply_markup=main_reply_keyboard()
    )
    await message.answer(
        "Выбирай раздел:",
        reply_markup=main_menu_keyboard()
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "*раскладывает на столе колоду меню*\n\nВыбирай раздел:",
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data == "menu_characters")
async def menu_open_characters(callback: CallbackQuery):
    await callback.answer()
    current = await get_character(callback.from_user.id)
    await callback.message.answer(
        "*на столе раскладывается колода масок*\n\nС кем хочешь поговорить?",
        reply_markup=await characters_inline_keyboard(callback.from_user.id, current)
    )


@dp.callback_query(F.data == "menu_createcharacter")
async def menu_open_createcharacter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _start_create_character_flow(callback.message, callback.from_user.id, state)


@dp.callback_query(F.data == "menu_games")
async def menu_open_games(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "*выдыхает дым, кивая на стол с играми*\n\n"
        "Просто скажи во что хочешь сыграть: крестики-нолики, угадай число, "
        "камень-ножницы-бумага, викторина или рулетка."
    )


@dp.callback_query(F.data == "menu_looksmax")
async def menu_open_looksmax(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "*стряхивает пепел, оценивающе глядя*\n\n"
        "Looksmaxing. Выбирай — бесплатная основа или полное VIP обучение.",
        reply_markup=looksmax_keyboard()
    )


@dp.callback_query(F.data == "menu_referral")
async def menu_open_referral(callback: CallbackQuery):
    await callback.answer()
    await _send_referral_info(callback.message, callback.from_user.id)


@dp.callback_query(F.data == "menu_streak")
async def menu_open_streak(callback: CallbackQuery):
    await callback.answer()
    profile = await get_profile(callback.from_user.id)
    streak = profile["streak_count"]
    await callback.message.answer(
        f"*затягивается, прикидывая в уме*\n\nТекущая серия: {streak} {'день' if streak == 1 else 'дней'} подряд."
    )


@dp.callback_query(F.data == "menu_clear")
async def menu_open_clear(callback: CallbackQuery):
    await callback.answer()
    current = await get_character(callback.from_user.id)
    await clear_history(callback.from_user.id, current)
    await callback.message.answer("*щелчком отправляет окурок в пепельницу*\n\nЧистый лист. Начинаем заново.")


@dp.callback_query(F.data == "menu_forget")
async def menu_open_forget(callback: CallbackQuery):
    await callback.answer()
    await clear_profile(callback.from_user.id)
    await callback.message.answer("*выдыхает дым, глядя сквозь тебя*\n\nЗабыла всё, что знала о тебе. Будто познакомились впервые.")


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not await require_subscription(message):
        return
    current = await get_character(message.from_user.id)
    await clear_history(message.from_user.id, current)
    await message.answer("*щелчком отправляет окурок в пепельницу*\n\nЧистый лист. Начинаем заново.")


@dp.message(Command("forget"))
async def cmd_forget(message: Message):
    if not await require_subscription(message):
        return
    await clear_profile(message.from_user.id)
    await message.answer("*выдыхает дым, глядя сквозь тебя*\n\nЗабыла всё, что знала о тебе. Будто познакомились впервые.")


@dp.message(Command("games"))
async def cmd_games(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "*выдыхает дым, кивая на стол с играми*\n\n"
        "Просто скажи во что хочешь сыграть: крестики-нолики, угадай число, "
        "камень-ножницы-бумага, викторина или рулетка."
    )


# ===================== РЕФЕРАЛЬНАЯ ПРОГРАММА =====================

async def _send_referral_info(message: Message, user_id: int):
    count = await get_referral_count(user_id)

    if BOT_USERNAME:
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        link_line = f"Твоя ссылка:\n`{link}`"
    else:
        link_line = f"Твоя ссылка:\n`/start ref_{user_id}` (попроси друга открыть бота и ввести эту команду)"

    await message.answer(
        f"*кладёт на стол колоду карт*\n\n"
        f"Приведи друга — получи слот для своего персонажа.\n\n"
        f"{link_line}\n\n"
        f"Рефералов сейчас: {count}. Доступно слотов для кастомных персонажей: {count}.",
        parse_mode="Markdown"
    )


@dp.message(Command("referral"))
async def cmd_referral(message: Message):
    if not await require_subscription(message):
        return
    await _send_referral_info(message, message.from_user.id)


# ===================== СОЗДАНИЕ КАСТОМНОГО ПЕРСОНАЖА =====================

def create_character_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Известная личность/персонаж", callback_data="createchar_known")
    builder.button(text="✏️ Придумать своего", callback_data="createchar_custom")
    builder.adjust(1)
    return builder.as_markup()


async def _start_create_character_flow(message: Message, user_id: int, state: FSMContext):
    limit = await get_custom_character_limit(user_id)
    current_count = await get_custom_character_count(user_id)

    if limit == 0:
        await message.answer(
            "*качает головой*\n\n"
            "Создание своего персонажа открывается за рефералов. Приведи друга — /referral."
        )
        return

    if current_count >= limit:
        await message.answer(
            f"*разводит руками*\n\n"
            f"Слоты закончились: {current_count}/{limit}. Приведи ещё одного друга — /referral."
        )
        return

    await message.answer(
        f"*раскладывает чистый холст*\n\n"
        f"Слотов доступно: {current_count}/{limit}. Кого создаём?",
        reply_markup=create_character_type_keyboard()
    )
    await state.set_state(CreateCharacterStates.choosing_type)


@dp.message(Command("createcharacter"))
async def cmd_create_character(message: Message, state: FSMContext):
    if not await require_subscription(message):
        return
    await _start_create_character_flow(message, message.from_user.id, state)


@dp.callback_query(F.data == "create_character_start")
async def handle_create_character_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _start_create_character_flow(callback.message, callback.from_user.id, state)


@dp.callback_query(F.data == "createchar_known")
async def handle_createchar_known(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "*приготовился слушать*\n\nНапиши имя и фамилию — известного человека или персонажа фильма/сериала/игры."
    )
    await state.set_state(CreateCharacterStates.waiting_known_name)


@dp.callback_query(F.data == "createchar_custom")
async def handle_createchar_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "*приготовился слушать*\n\nОпиши характер своего персонажа: кто он, как говорит, какой у него стиль и настроение."
    )
    await state.set_state(CreateCharacterStates.waiting_custom_description)


@dp.message(CreateCharacterStates.waiting_known_name)
async def handle_known_name_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("*приподнимает бровь*\n\nИмя слишком короткое. Попробуй ещё раз.")
        return

    await state.clear()
    await bot.send_chat_action(message.chat.id, "typing")
    await message.answer("*задумывается, прикуривая*\n\nДай мне минуту, изучаю личность.")

    result = await generate_known_character(groq_client, GROQ_MODEL, name)
    await _finalize_character_creation(message, user_id, result)


@dp.message(CreateCharacterStates.waiting_custom_description)
async def handle_custom_description_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    description = message.text.strip()

    if len(description) < 5:
        await message.answer("*приподнимает бровь*\n\nОписание слишком короткое. Расскажи немного подробнее.")
        return

    await state.clear()
    await bot.send_chat_action(message.chat.id, "typing")
    await message.answer("*задумывается, прикуривая*\n\nДай мне минуту, придумываю характер.")

    result = await generate_custom_character(groq_client, GROQ_MODEL, description)
    await _finalize_character_creation(message, user_id, result)


async def _finalize_character_creation(message: Message, user_id: int, result: dict):
    if not result["ok"]:
        await message.answer(f"*качает головой*\n\n{result['error']}")
        return

    # Проверяем лимит ещё раз на случай гонки состояний
    limit = await get_custom_character_limit(user_id)
    current_count = await get_custom_character_count(user_id)
    if current_count >= limit:
        await message.answer(
            f"*разводит руками*\n\nСлоты закончились пока думала: {current_count}/{limit}."
        )
        return

    base_slug = slugify(result["name"])
    slug = base_slug
    suffix = 1
    while await get_custom_character_by_slug(user_id, slug):
        suffix += 1
        slug = f"{base_slug}_{suffix}"

    await add_custom_character(
        owner_id=user_id,
        slug=slug,
        name=result["name"],
        emoji=result["emoji"],
        system_prompt=result["system_prompt"],
        intro=result["intro"],
    )

    await set_character(user_id, f"custom:{slug}")

    await message.answer(
        f"*кладёт готовую карту на стол*\n\n"
        f"Персонаж готов: {result['emoji']} {result['name']}. Теперь говоришь с ним."
    )
    await message.answer(result["intro"], reply_markup=main_reply_keyboard())


# ===================== ПЕРСОНАЖИ =====================

@dp.message(Command("character"))
async def cmd_character(message: Message):
    if not await require_subscription(message):
        return
    current = await get_character(message.from_user.id)
    await message.answer(
        "*на столе раскладывается колода масок*\n\nС кем хочешь поговорить?",
        reply_markup=await characters_inline_keyboard(message.from_user.id, current)
    )


@dp.message(F.text == CHARACTERS_BUTTON_TEXT)
async def handle_characters_button(message: Message):
    if not await require_subscription(message):
        return
    current = await get_character(message.from_user.id)
    await message.answer(
        "*на столе раскладывается колода масок*\n\nС кем хочешь поговорить?",
        reply_markup=await characters_inline_keyboard(message.from_user.id, current)
    )


@dp.callback_query(F.data.startswith("setchar_"))
async def handle_set_character(callback: CallbackQuery):
    key = callback.data.split("_", 1)[1]
    if key not in CHARACTERS:
        await callback.answer("Такого персонажа не знаю.", show_alert=True)
        return

    await set_character(callback.from_user.id, key)
    data = get_character_data(key)

    await callback.message.edit_text(
        f"Теперь говоришь с: {data['emoji']} {data['name']}",
        reply_markup=await characters_inline_keyboard(callback.from_user.id, key)
    )
    await callback.message.answer(data["intro"], reply_markup=main_reply_keyboard())
    await callback.answer()


@dp.callback_query(F.data.startswith("setcustom_"))
async def handle_set_custom_character(callback: CallbackQuery):
    slug = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id
    custom = await get_custom_character_by_slug(user_id, slug)
    if not custom:
        await callback.answer("Этого персонажа больше нет.", show_alert=True)
        return

    character_key = f"custom:{slug}"
    await set_character(user_id, character_key)

    await callback.message.edit_text(
        f"Теперь говоришь с: {custom['emoji']} {custom['name']}",
        reply_markup=await characters_inline_keyboard(user_id, character_key)
    )
    await callback.message.answer(custom["intro"], reply_markup=main_reply_keyboard())
    await callback.answer()


# ===================== LOOKSMAXING =====================

def looksmax_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Бесплатный гайд", callback_data="looksmax_free")
    builder.button(text=f"💎 VIP обучение — {VIP_PRICE}", callback_data="looksmax_vip")
    builder.adjust(1)
    return builder.as_markup()


@dp.message(Command("looksmax"))
async def cmd_looksmax(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "*стряхивает пепел, оценивающе глядя*\n\n"
        "Looksmaxing. Выбирай — бесплатная основа или полное VIP обучение.",
        reply_markup=looksmax_keyboard()
    )


@dp.callback_query(F.data == "looksmax_free")
async def handle_looksmax_free(callback: CallbackQuery):
    await callback.answer()
    if not os.path.exists(FREE_GUIDE_PATH):
        await callback.message.answer("*разводит руками*\n\nФайл гайда пока не на месте. Загляни позже.")
        return
    await callback.message.answer("*кидает на стол потрёпанный конверт*\n\nВот база. Дальше сам решай, насколько глубоко копать.")
    await callback.message.answer_document(FSInputFile(FREE_GUIDE_PATH))


@dp.callback_query(F.data == "looksmax_vip")
async def handle_looksmax_vip(callback: CallbackQuery):
    await callback.answer()
    pending_vip_payments[callback.from_user.id] = True
    await callback.message.answer(
        f"*наклоняется ближе, понижая голос*\n\n"
        f"VIP стоит {VIP_PRICE}. Переведи на карту:\n\n"
        f"`{CARD_NUMBER}`\n\n"
        f"После перевода пришли сюда фото чека. Жди подтверждения.",
        parse_mode="Markdown"
    )


@dp.message(Command("confirm"))
async def cmd_confirm(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /confirm <user_id>")
        return
    try:
        target_user_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    if target_user_id not in pending_vip_payments:
        await message.answer(f"Не вижу ожидающего платежа от {target_user_id}.")
        return
    pending_vip_payments.pop(target_user_id, None)
    if not os.path.exists(VIP_GUIDE_PATH):
        await message.answer("Файл VIP гайда не найден. Положи его в guides/vip_guide.pdf")
        return
    try:
        await bot.send_message(target_user_id, "*кивает с уважением*\n\nОплата подтверждена. Вот полное VIP обучение.")
        await bot.send_document(target_user_id, FSInputFile(VIP_GUIDE_PATH))
        await message.answer(f"Гайд отправлен пользователю {target_user_id} ✅")
    except Exception as e:
        await message.answer(f"Не удалось отправить: {e}")


# ===================== ИГРЫ — КЛЮЧЕВЫЕ СЛОВА =====================

GAME_KEYWORDS = {
    "tictactoe": ["крестик", "нолик", "tic tac", "ттт", "крестики-нолики"],
    "guess": ["угадай число", "угадать число", "угадай чис", "угадай цифр"],
    "rps": ["камень", "ножниц", "бумаг", "кнб"],
    "quiz": ["викторин", "квиз", "вопрос"],
    "roulette": ["рулетк"],
}


def detect_game_request(text: str):
    lowered = text.lower().strip()
    for game, keywords in GAME_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                return game
    return None


async def start_game_by_name(message: Message, game: str):
    user_id = message.from_user.id
    if game == "tictactoe":
        board = games.start_tictactoe(user_id)
        await message.answer("*чертит крестик на пепельнице*\n\nТы — Х, я — O. Начинай.", reply_markup=games.render_tictactoe_keyboard(board))
    elif game == "guess":
        games.start_guess(user_id)
        await message.answer("*тушит сигарету о край стола*\n\nЗагадала число от 1 до 100. Пиши свою догадку.")
    elif game == "rps":
        await message.answer("*разминает пальцы*\n\nКамень, ножницы или бумага. Выбирай.", reply_markup=games.rps_keyboard())
    elif game == "quiz":
        games.start_quiz(user_id)
        idx, q = games.get_current_question(user_id)
        await message.answer(f"*облокачивается на стол*\n\n{q['question']}", reply_markup=games.quiz_keyboard(idx))
    elif game == "roulette":
        games.start_roulette(user_id)
        await message.answer("*ставит барабан на стол с тихим щелчком*\n\nШесть камер, одна заряжена шуткой судьбы. Крутани барабан.", reply_markup=games.roulette_keyboard())


# ===================== КРЕСТИКИ-НОЛИКИ =====================

@dp.callback_query(F.data.startswith("ttt_"))
async def handle_ttt_move(callback: CallbackQuery):
    if callback.data == "ttt_over":
        await callback.answer("Игра уже закончилась. Начни новую через /games")
        return
    index = int(callback.data.split("_")[1])
    board, result = games.handle_tictactoe_move(callback.from_user.id, index)
    if board is None:
        await callback.answer("Начни новую игру через /games")
        return
    if result == "draw":
        await callback.message.edit_text("*пожимает плечами*\n\nНичья. Цирк закончился без победителя.", reply_markup=games.render_tictactoe_keyboard(board, game_over=True))
    elif result == "X":
        await callback.message.edit_text("*приподнимает бровь*\n\nПобедил. На этот раз.", reply_markup=games.render_tictactoe_keyboard(board, game_over=True))
    elif result == "O":
        await callback.message.edit_text("*усмехается*\n\nЯ выиграла. Жизнь несправедлива, помнишь?", reply_markup=games.render_tictactoe_keyboard(board, game_over=True))
    else:
        await callback.message.edit_reply_markup(reply_markup=games.render_tictactoe_keyboard(board))
    await callback.answer()


# ===================== КНБ =====================

@dp.callback_query(F.data.startswith("rps_"))
async def handle_rps(callback: CallbackQuery):
    user_choice = callback.data.split("_")[1]
    bot_choice, result = games.play_rps(user_choice)
    bot_label = games.RPS_OPTIONS[bot_choice]
    user_label = games.RPS_OPTIONS[user_choice]
    if result == "draw":
        text = f"*хмыкает*\n\nТы выбрал {user_label}, я — {bot_label}. Ничья, банально."
    elif result == "win":
        text = f"*качает головой*\n\n{user_label} против моих {bot_label}. Ты выиграл."
    else:
        text = f"*ухмыляется*\n\n{bot_label} бьёт твою {user_label}. Я выиграла."
    await callback.message.edit_text(text, reply_markup=games.rps_keyboard())
    await callback.answer()


# ===================== ВИКТОРИНА =====================

@dp.callback_query(F.data.startswith("quiz_"))
async def handle_quiz_answer(callback: CallbackQuery):
    _, q_idx_str, opt_idx_str = callback.data.split("_")
    q_idx, opt_idx = int(q_idx_str), int(opt_idx_str)
    result = games.answer_quiz(callback.from_user.id, q_idx, opt_idx)
    if result is None:
        await callback.answer("Викторина уже закончилась.")
        return
    feedback = "*кивает с лёгким уважением*\n\nВерно." if result["correct"] else f"*качает головой*\n\nНет. Правильный ответ — {result['correct_answer']}."
    if result["finished"]:
        await callback.message.edit_text(f"{feedback}\n\n*затягивается*\n\nИтог: {result['score']} из {result['total']}.")
    else:
        idx, q = games.get_current_question(callback.from_user.id)
        await callback.message.edit_text(f"{feedback}\n\nСчёт: {result['score']}/{result['total']}\n\n{q['question']}", reply_markup=games.quiz_keyboard(idx))
    await callback.answer()


# ===================== РУЛЕТКА =====================

@dp.callback_query(F.data == "roulette_spin")
async def handle_roulette_spin(callback: CallbackQuery):
    result = games.spin_roulette(callback.from_user.id)
    if result is None:
        await callback.answer("Начни новую игру через /games")
        return
    if result["result"] == "loss":
        await callback.message.edit_text(f"*барабан щёлкает*\n\nБах. Раунд {result['round']} — судьба тебя поймала. Игра окончена.")
    else:
        await callback.message.edit_text(f"*выдыхает*\n\nРаунд {result['round']} пройден. Крутить дальше?", reply_markup=games.roulette_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "roulette_exit")
async def handle_roulette_exit(callback: CallbackQuery):
    games.roulette_states.pop(callback.from_user.id, None)
    await callback.message.edit_text("*убирает барабан*\n\nРазумный выбор.")
    await callback.answer()


# ===================== ФОТО (только для VIP-чека) =====================

@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    if not await require_subscription(message):
        return
    if user_id in pending_vip_payments:
        await message.answer("*забирает конверт, не открывая*\n\nЧек получен. Жди подтверждения.")
        try:
            username = message.from_user.username or "без username"
            caption = f"💰 Новый чек на VIP-оплату\n\nОт: {message.from_user.full_name} (@{username})\nuser_id: {user_id}\n\nПодтвердить: /confirm {user_id}"
            await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption)
        except Exception:
            pass
        return
    await message.answer("*даже не смотрит в сторону фото*\n\nКартинки не мой профиль. Расскажи словами.")


# ===================== ТЕКСТ =====================

@dp.message(F.text)
async def handle_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_text = message.text

    # Если пользователь сейчас в процессе создания персонажа — этот хендлер не должен
    # перехватывать сообщение (FSM-хендлеры выше зарегистрированы раньше и сработают первыми,
    # но на случай гонки проверяем явно).
    current_state = await state.get_state()
    if current_state is not None:
        return

    if not await require_subscription(message):
        return

    # Анти-спам — после проверки подписки, чтобы не блокировать легитимный первый шаг
    if is_rate_limited(user_id):
        await handle_rate_limit(message)
        return

    # Запрос на озвучку последнего ответа — например "озвучь", "скажи голосом"
    if detect_voice_request(user_text):
        last_reply = last_reply_by_user.get(user_id)
        if not last_reply:
            await message.answer("*пожимает плечами*\n\nЕщё нечего озвучивать — сначала спроси что-нибудь.")
            return
        await bot.send_chat_action(message.chat.id, "record_voice")
        try:
            voice_character_key = last_reply["character_key"]
            if voice_character_key.startswith("custom:"):
                voice_character_key = voice_character_key.split(":", 1)[1]
            audio_bytes = await voice.text_to_speech(last_reply["text"], voice_character_key)
            if audio_bytes:
                voice_file = BufferedInputFile(audio_bytes, filename="voice.ogg")
                await message.answer_voice(voice_file)
            else:
                await message.answer("*разводит руками*\n\nНечего озвучивать — там одни ремарки без слов.")
        except Exception as e:
            await message.answer(f"*раздражённо тушит сигарету*\n\nГолос не получился: {e}")
        return

    # Стрик — считаем активность за день
    streak_info = await touch_streak(user_id)

    # Угадай число
    if user_id in games.guess_states:
        if user_text.strip().lstrip("-").isdigit():
            guess = int(user_text.strip())
            result = games.handle_guess(user_id, guess)
            if result["result"] == "win":
                await message.answer(f"*выпрямляется*\n\nУгадал за {result['attempts']} попыток. Неплохо для новичка.")
            elif result["result"] == "higher":
                await message.answer("*качает головой*\n\nБольше.")
            else:
                await message.answer("*качает головой*\n\nМеньше.")
            return

    # Игры
    requested_game = detect_game_request(user_text)
    if requested_game:
        await start_game_by_name(message, requested_game)
        return

    # Генерация картинки
    image_prompt = detect_image_request(user_text)
    if image_prompt is not None:
        if len(image_prompt) < 3:
            await message.answer("*приподнимает бровь*\n\nИ что именно мне рисовать? Дай хоть пару слов.")
            return
        await bot.send_chat_action(message.chat.id, "upload_photo")
        try:
            image_bytes = await generate_image(image_prompt)
            photo_file = BufferedInputFile(image_bytes, filename="aria_art.jpg")
            await message.answer_photo(photo_file, caption="*выдыхает дым*\n\nВот, что получилось из твоей идеи.")
        except Exception as e:
            await message.answer(f"*раздражённо тушит сигарету*\n\nХолст не вышел: {e}")
        return

    # Обычный чат — берём текущего персонажа пользователя (встроенного или кастомного)
    await bot.send_chat_action(message.chat.id, "typing")

    character_key = await get_character(user_id)
    character_data = await resolve_character(user_id, character_key)
    system_prompt = character_data["system_prompt"]

    profile = await get_profile(user_id)
    profile_context = build_profile_context(profile)

    history = await get_history(user_id, character_key)
    messages = [{"role": "system", "content": system_prompt + profile_context}]
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_text})

    try:
        reply = await call_ai(messages)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history, character_key)
        await message.answer(reply)

        # Уведомление о новом дне стрика — отдельным коротким сообщением, не мешая основному ответу
        if streak_info["is_new_day"] and streak_info["streak_count"] > 1:
            await message.answer(f"🔥 Серия: {streak_info['streak_count']} {'день' if streak_info['streak_count'] == 1 else 'дней'} подряд.")

        # Сохраняем последний ответ — пользователь может попросить озвучить его текстом ("озвучь")
        last_reply_by_user[user_id] = {"text": reply, "character_key": character_key}

        # Фоновое извлечение фактов о пользователе — не блокирует ответ
        asyncio.create_task(_update_profile_from_message(user_id, user_text))

    except Exception as e:
        err = str(e).lower()
        if "rate_limit" in err or "429" in err or "quota" in err:
            await message.answer(
                "*тушит сигарету, бросает взгляд в потолок*\n\n"
                "Токены закончились. Обратитесь позже — хозяин в курсе и разберётся."
            )
        else:
            await message.answer(f"*раздражённо тушит сигарету*\n\nЧто-то сломалось в этом цирке: {e}")


async def _update_profile_from_message(user_id: int, user_text: str):
    """Извлекает факты в фоне и сохраняет их в профиль пользователя."""
    try:
        extracted = await extract_user_facts(user_text)
        if extracted["name"]:
            await update_profile_name(user_id, extracted["name"])
        if extracted["facts"]:
            await add_facts(user_id, extracted["facts"])
    except Exception:
        pass


# ===================== СТРИК-НАПОМИНАНИЯ (ПЛАНИРОВЩИК) =====================

STREAK_REMINDER_TEXTS = [
    "*выдыхает дым, глядя в пустоту*\n\nТебя не было вчера. Серия ещё жива — не дай ей погаснуть.",
    "*стучит пальцами по столу*\n\nВчера тишина. Загляни — серия на грани.",
]


async def send_streak_reminders():
    """Раз в день рассылает напоминания тем, кто пропустил ровно один день."""
    import random
    try:
        user_ids = await get_users_missed_yesterday()
        for user_id in user_ids:
            try:
                await bot.send_message(user_id, random.choice(STREAK_REMINDER_TEXTS))
            except Exception:
                pass
    except Exception:
        pass


# ===================== ЗАПУСК =====================

scheduler = AsyncIOScheduler()

BOT_COMMANDS = [
    BotCommand(command="start", description="Начать с начала"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="character", description="Выбрать персонажа"),
    BotCommand(command="createcharacter", description="Создать своего персонажа"),
    BotCommand(command="referral", description="Реферальная ссылка"),
    BotCommand(command="games", description="Поиграть"),
    BotCommand(command="looksmax", description="Гайды по внешности"),
    BotCommand(command="clear", description="Очистить историю чата"),
    BotCommand(command="forget", description="Забыть всё о тебе"),
]


async def on_startup(app):
    await init_db()
    await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
    await bot.set_my_commands(BOT_COMMANDS)

    # Ежедневная проверка стриков в 12:00 UTC — фиксированное простое время
    scheduler.add_job(send_streak_reminders, "cron", hour=12, minute=0)
    scheduler.start()


async def health_check(request):
    return web.Response(text="Bot is running")


def main():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.on_startup.append(on_startup)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
