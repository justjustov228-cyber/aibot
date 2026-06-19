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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT '',
                facts TEXT DEFAULT '[]'
            )
        """)
        await db.commit()


# ===================== ИСТОРИЯ ЧАТА =====================

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


# ===================== ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ (ПАМЯТЬ) =====================

async def get_profile(user_id: int) -> dict:
    """Возвращает {'name': str, 'facts': [str, ...]}"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name, facts FROM profiles WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"name": row[0] or "", "facts": json.loads(row[1] or "[]")}
            return {"name": "", "facts": []}


async def update_profile_name(user_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO profiles (user_id, name, facts)
            VALUES (?, ?, '[]')
            ON CONFLICT(user_id) DO UPDATE SET name = excluded.name
        """, (user_id, name))
        await db.commit()


async def add_facts(user_id: int, new_facts: list):
    """Добавляет новые факты, избегая дублей, хранит максимум 30 последних."""
    if not new_facts:
        return

    profile = await get_profile(user_id)
    existing = profile["facts"]

    for fact in new_facts:
        fact = fact.strip()
        if fact and fact not in existing:
            existing.append(fact)

    existing = existing[-30:]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO profiles (user_id, name, facts)
            VALUES (?, '', ?)
            ON CONFLICT(user_id) DO UPDATE SET facts = excluded.facts
        """, (user_id, json.dumps(existing)))
        await db.commit()


async def clear_profile(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM profiles WHERE user_id = ?", (user_id,)
        )
        await db.commit()
