import asyncio
import os
from dotenv import load_dotenv
import google.generativeai as genai
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from db import init_db, get_history, save_history, clear_history

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # например https://твой-сервис.onrender.com
PORT = int(os.getenv("PORT", 10000))

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    system_instruction="Ты личный ассистент пользователя в Telegram. Помнишь историю разговора. Отвечай кратко и по делу. Общайся на языке пользователя."
)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я твой личный ассистент с памятью 🧠\n"
        "Просто пиши мне что угодно.\n\n"
        "/clear — очистить историю разговора"
    )

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    await clear_history(message.from_user.id)
    await message.answer("История очищена ✅")

@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text

    await bot.send_chat_action(message.chat.id, "typing")

    history = await get_history(user_id)
    gemini_history = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})

    try:
        chat = model.start_chat(history=gemini_history)
        response = await asyncio.to_thread(chat.send_message, user_text)
        reply = response.text

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history)

        await message.answer(reply)

    except Exception as e:
        await message.answer(f"Ошибка: {e}")

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
