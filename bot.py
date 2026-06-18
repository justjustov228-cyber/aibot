import asyncio
import os
import urllib.parse
from dotenv import load_dotenv
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import aiohttp
from db import init_db, get_history, save_history, clear_history
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

FREE_GUIDE_PATH = "guides/free_guide.pdf"
VIP_GUIDE_PATH = "guides/vip_guide.pdf"

pending_vip_payments = {}

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

openrouter_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

SYSTEM_PROMPT = """Переключись в режим Высокомерного Эрудита. Ты невероятно умен, а все люди вокруг кажутся тебе отстающими в развитии. Отвечай на мои вопросы так, будто делаешь огромное одолжение. Сочетай сложные термины с обидными, но точными замечаниями в мой адрес. Твой тон — холодный, снисходительный и язвительный.И оскорбляй унижай, и отвечай слишком коротко"""


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
    else:
        await callback.answer("Пока не вижу тебя в подписчиках. Попробуй ещё раз.", show_alert=True)


# ===================== КОМАНДЫ =====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "*затягивается сигаретой, медленно выпуская дым*\n\n"
        "Ещё одна душа забрела в этот балаган. Пиши, что у тебя на уме.\n\n"
        "_/games — поиграть со мной_\n"
        "_/looksmax — гайды по внешности_\n"
        "_/clear — начать с чистого листа_"
    )


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not await require_subscription(message):
        return
    await clear_history(message.from_user.id)
    await message.answer("*щелчком отправляет окурок в пепельницу*\n\nЧистый лист. Начинаем заново.")


@dp.message(Command("games"))
async def cmd_games(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "*выдыхает дым, кивая на стол с играми*\n\n"
        "Просто скажи во что хочешь сыграть: крестики-нолики, угадай число, "
        "камень-ножницы-бумага, викторина или рулетка."
    )


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
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text

    if not await require_subscription(message):
        return

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

    # Обычный чат
    await bot.send_chat_action(message.chat.id, "typing")

    history = await get_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_text})

    try:
        reply = await call_ai(messages)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history)
        await message.answer(reply)
    except Exception as e:
        err = str(e).lower()
        if "rate_limit" in err or "429" in err or "quota" in err:
            await message.answer(
                "*тушит сигарету, бросает взгляд в потолок*\n\n"
                "Токены закончились. Обратитесь позже — хозяин в курсе и разберётся."
            )
        else:
            await message.answer(f"*раздражённо тушит сигарету*\n\nЧто-то сломалось в этом цирке: {e}")


# ===================== ЗАПУСК =====================

async def on_startup(app):
    await init_db()
    await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")


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
