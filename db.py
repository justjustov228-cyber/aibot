import asyncio
import os
from dotenv import load_dotenv
import google.generativeai as genai
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from db import init_db, get_history, save_history, clear_history

load_dotenv()

bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
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

    # Загружаем историю и конвертируем в формат Gemini
    history = await get_history(user_id)
    
    gemini_history = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})

    try:
        chat = model.start_chat(history=gemini_history)
        response = await asyncio.to_thread(chat.send_message, user_text)
        reply = response.text

        # Сохраняем в историю
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        await save_history(user_id, history)

        await message.answer(reply)

    except Exception as e:
        await message.answer(f"Ошибка: {e}")

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
