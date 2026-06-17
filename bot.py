import asyncio
import os
import base64
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
CHANNEL_USERNAME = "@ariaaich"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

TEXT_MODEL = "llama-3.3-70b-versatile"
VISION_MODEL = "llama-3.2-90b-vision-preview"

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


async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def subscribe_keyboard():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")
    builder.button(text="✅ Я подписался", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "*прищуривается, оценивающе глядя*\n\n"
            "Прежде чем мы поговорим — подпишись на канал. Таковы правила этого балагана.",
            reply_markup=subscribe_keyboard()
        )
        return

    await message.answer(
        "*затягивается сигаретой, медленно выпуская дым*\n\n"
        "Ещё одна душа забрела в этот балаган. Пиши, что у тебя на уме. Можешь и фото скинуть — гляну, что там у тебя.\n\n"
        "_/clear — сжечь всю историю и начать с чистого листа_"
    )


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback):
    if await is_subscribed(callback.from_user.id):
        await callback.message.edit_text(
            "*кивает, выпуская дым*\n\nХорошо. Теперь говори, что у тебя на уме."
        )
    else:
        await callback.answer("Пока не вижу тебя в подписчиках. Попробуй ещё раз.", show_alert=True)


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "*прищуривается*\n\nСначала подпишись на канал.",
            reply_markup=subscribe_keyboard()
        )
        return
    await clear_history(message.from_user.id)
    await message.answer("*щелчком отправляет окурок в пепельницу*\n\nЧистый лист. Начинаем заново.")


@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id

    if not await is_subscribed(user_id):
        await message.answer(
            "*прищуривается*\n\nСначала подпишись на канал, потом покажешь свои картинки.",
            reply_markup=subscribe_keyboard()
        )
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


@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text

    if not await is_subscribed(user_id):
        await message.answer(
            "*прищуривается, оценивающе глядя*\n\n"
            "Прежде чем мы поговорим — подпишись на канал. Таковы правила этого балагана.",
            reply_markup=subscribe_keyboard()
        )
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
