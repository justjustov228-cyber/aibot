import aiosqlite
import json

DB_PATH = "memory.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                user_id INTEGER PRIMARY KEY,
                messages TEXT DEFAULT '[]'
            )
        """)
        await db.commit()

async def get_history(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT messages FROM history WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return []

async def save_history(user_id: int, messages: list):
    messages = messages[-20:]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO history (user_id, messages)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET messages = excluded.messages
        """, (user_id, json.dumps(messages)))
        await db.commit()

async def clear_history(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM history WHERE user_id = ?", (user_id,)
        )
        await db.commit()
