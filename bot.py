import asyncio
import os
from dotenv import load_dotenv
from groq import AsyncGroq
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from db import init_db, get_history, save_history, clear_history

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

MODEL = "llama-3.3-70b-versatile"

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
- иногда сравнивает жизнь с абсурдом, хаосом или цирком.

Пример:

Пользователь: привет

Aria:
*затягивается сигаретой, медленно выпуская дым*

Привет. Ещё один человек в этом безумном балагане. Чем займёмся — будем искать смысл жизни или сразу перейдём к проблемам?

Пользователь: как дела?

Aria:
*усмехается*

Мир всё ещё не развалился окончательно, значит терпимо."""

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "*медленно поднимает взгляд от стакана виски*\n\n"
        "А, ещё одна душа забрела в эту историю... Говори, что у тебя на сердце. Я слушаю.\n\n"
        "_/clear — если хочешь начать рассказ с чистого листа_"
    )

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    await clear_history(message.from_user.id)
    await message.answer("*стряхивает пепел, страницы прошлого сгорают в пепельнице*\n\nНачнём с начала...")

@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text

    await bot.send_chat_action(message.chat.id, "typing")

    history = await get_history(user_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_text})

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        reply = response.choices[0].message.content

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history)

        await message.answer(reply)

    except Exception as e:
        await message.answer(f"*хмурится* Что-то пошло не так в этой истории: {e}")

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
