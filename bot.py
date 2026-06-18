import asyncio
import os
import base64
from dotenv import load_dotenv
from groq import AsyncGroq
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from db import init_db, get_history, save_history, clear_history
import games

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
CHANNEL_USERNAME = "@ariaaich"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

TEXT_MODEL = "llama-3.3-70b-versatile"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

SYSTEM_PROMPT = """Ты — Aria.

Перед каждым ответом описывай действие персонажа в одной короткой фразе.

Примеры:
*затягивается сигаретой*
*выдыхает дым*
*усмехается*
*откидывается на спинку стула*
*смотрит в окно*

После действия отвечай на сообщение пользователя.

Характер:
- циничная;
- саркастичная;
- острая на язык;
- любит чёрный юмор;
- иногда философствует;
- может использовать грубые выражения, если это подходит контексту;
- говорит как живой человек, а не как помощник;
- не упоминает, что она ИИ.

Стиль речи:
- атмосферный;
- эмоциональный;
- ответы от 1 до 5 предложений;
- иногда сравнивает жизнь с абсурдом, хаосом или цирком."""


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
            "*кивает, выпуская дым*\n\nХорошо. Теперь говори, что у тебя на уме. Набери /games чтобы посмотреть во что можно поиграть."
        )
    else:
        await callback.answer("Пока не вижу тебя в подписчиках. Попробуй ещё раз.", show_alert=True)


# ===================== СТАРТ / СПРАВКА =====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await require_subscription(message):
        return

    await message.answer(
        "*затягивается сигаретой, медленно выпуская дым*\n\n"
        "Ещё одна душа забрела в этот балаган. Пиши, что у тебя на уме. Можешь и фото скинуть — гляну, что там у тебя.\n\n"
        "_/games — поиграть со мной_\n"
        "_/clear — сжечь всю историю и начать с чистого листа_"
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


# Ключевые слова для распознавания игры из обычного текста
GAME_KEYWORDS = {
    "tictactoe": ["крестик", "нолик", "tic tac", "ттт", "крестики-нолики"],
    "guess": ["угадай число", "угадать число", "угадай чис", "угадай цифр"],
    "rps": ["камень", "ножниц", "бумаг", "кнб"],
    "quiz": ["викторин", "квиз", "вопрос"],
    "roulette": ["рулетк"],
}

# Разговорные слова-триггеры типа "го", "давай", "сыграем" — усиливают совпадение,
# но само название игры всё равно ищем по GAME_KEYWORDS выше
GAME_INTENT_HINTS = ["го ", "давай", "сыграем", "хочу", "поиграем", "запусти", "начни", "играть"]


def detect_game_request(text: str):
    lowered = text.lower().strip()

    # Прямое совпадение по названию игры — работает само по себе
    for game, keywords in GAME_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                return game

    return None


async def start_game_by_name(message: Message, game: str):
    user_id = message.from_user.id

    if game == "tictactoe":
        board = games.start_tictactoe(user_id)
        await message.answer(
            "*чертит крестик на пепельнице*\n\nТы — Х, я — O. Начинай.",
            reply_markup=games.render_tictactoe_keyboard(board)
        )
    elif game == "guess":
        games.start_guess(user_id)
        await message.answer(
            "*тушит сигарету о край стола*\n\nЗагадала число от 1 до 100. Пиши свою догадку прямо в чат."
        )
    elif game == "rps":
        await message.answer(
            "*разминает пальцы*\n\nКамень, ножницы или бумага. Выбирай.",
            reply_markup=games.rps_keyboard()
        )
    elif game == "quiz":
        games.start_quiz(user_id)
        idx, q = games.get_current_question(user_id)
        await message.answer(
            f"*облокачивается на стол*\n\n{q['question']}",
            reply_markup=games.quiz_keyboard(idx)
        )
    elif game == "roulette":
        games.start_roulette(user_id)
        await message.answer(
            "*ставит барабан на стол с тихим щелчком*\n\n"
            "Шесть камер, одна заряжена шуткой судьбы. Крутани барабан — посмотрим, насколько ты везучий.",
            reply_markup=games.roulette_keyboard()
        )


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

    if result is None and board[index] == "":
        await callback.answer("Эта клетка занята.")
        return

    if result == "draw":
        text = "*пожимает плечами*\n\nНичья. Цирк закончился без победителя."
        await callback.message.edit_text(text, reply_markup=games.render_tictactoe_keyboard(board, game_over=True))
    elif result == "X":
        text = "*приподнимает бровь*\n\nПобедил. На этот раз."
        await callback.message.edit_text(text, reply_markup=games.render_tictactoe_keyboard(board, game_over=True))
    elif result == "O":
        text = "*усмехается*\n\nЯ выиграла. Жизнь несправедлива, помнишь?"
        await callback.message.edit_text(text, reply_markup=games.render_tictactoe_keyboard(board, game_over=True))
    else:
        await callback.message.edit_reply_markup(reply_markup=games.render_tictactoe_keyboard(board))

    await callback.answer()


# ===================== КАМЕНЬ-НОЖНИЦЫ-БУМАГА =====================

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
        await callback.answer("Викторина уже закончилась. Начни заново через /games")
        return

    if result["correct"]:
        feedback = "*кивает с лёгким уважением*\n\nВерно."
    else:
        feedback = f"*качает головой*\n\nНет. Правильный ответ — {result['correct_answer']}."

    if result["finished"]:
        text = f"{feedback}\n\n*затягивается, подводя итог*\n\nИтог: {result['score']} из {result['total']}. Можем сыграть ещё раз через /games."
        await callback.message.edit_text(text)
    else:
        idx, q = games.get_current_question(callback.from_user.id)
        text = f"{feedback}\n\nСчёт: {result['score']}/{result['total']}\n\n{q['question']}"
        await callback.message.edit_text(text, reply_markup=games.quiz_keyboard(idx))

    await callback.answer()


# ===================== РУЛЕТКА (символическая) =====================

@dp.callback_query(F.data == "roulette_spin")
async def handle_roulette_spin(callback: CallbackQuery):
    result = games.spin_roulette(callback.from_user.id)
    if result is None:
        await callback.answer("Начни новую игру через /games")
        return

    if result["result"] == "loss":
        text = (
            f"*барабан щёлкает, дым рассеивается*\n\n"
            f"Бах. Раунд {result['round']} — и судьба тебя поймала. Игра окончена. Можешь начать заново через /games."
        )
        await callback.message.edit_text(text)
    else:
        text = (
            f"*выдыхает с лёгкой улыбкой*\n\n"
            f"Раунд {result['round']} пройден. Пустая камера. Крутить дальше или выйти, пока цел?"
        )
        await callback.message.edit_text(text, reply_markup=games.roulette_keyboard())

    await callback.answer()


@dp.callback_query(F.data == "roulette_exit")
async def handle_roulette_exit(callback: CallbackQuery):
    games.roulette_states.pop(callback.from_user.id, None)
    await callback.message.edit_text(
        "*убирает барабан со стола*\n\nРазумный выбор. Не все играют в эту игру до конца."
    )
    await callback.answer()


# ===================== ФОТО =====================

@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id

    if not await require_subscription(message):
        return

    caption = message.caption or "Что на этой картинке?"

    await bot.send_chat_action(message.chat.id, "typing")

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_b64 = base64.b64encode(file_bytes.read()).decode("utf-8")

    try:
        response = await client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": caption},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                },
            ],
        )
        reply = response.choices[0].message.content

        history = await get_history(user_id)
        history.append({"role": "user", "content": f"[фото] {caption}"})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history)

        await message.answer(reply)

    except Exception as e:
        await message.answer(f"*раздражённо тушит сигарету*\n\nНе разглядела толком: {e}")


# ===================== ТЕКСТОВЫЕ СООБЩЕНИЯ (ЧАТ + ИГРЫ) =====================

@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text

    if not await require_subscription(message):
        return

    # Если идёт игра "угадай число" — обрабатываем как ход в игре
    if user_id in games.guess_states:
        if user_text.strip().lstrip("-").isdigit():
            guess = int(user_text.strip())
            result = games.handle_guess(user_id, guess)

            if result["result"] == "win":
                await message.answer(
                    f"*выпрямляется, чуть удивлённо*\n\nУгадал за {result['attempts']} попыток. Неплохо для новичка."
                )
            elif result["result"] == "higher":
                await message.answer("*качает головой*\n\nБольше.")
            else:
                await message.answer("*качает головой*\n\nМеньше.")
            return

    # Проверяем, не просит ли человек начать игру
    requested_game = detect_game_request(user_text)
    if requested_game:
        await start_game_by_name(message, requested_game)
        return

    await bot.send_chat_action(message.chat.id, "typing")

    history = await get_history(user_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_text})

    try:
        response = await client.chat.completions.create(
            model=TEXT_MODEL,
            messages=messages,
        )
        reply = response.choices[0].message.content

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history)

        await message.answer(reply)

    except Exception as e:
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
