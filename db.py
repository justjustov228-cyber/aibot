import aiosqlite
import json

DB_PATH = "memory.db"

DEFAULT_CHARACTER = "aria"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # История чата теперь хранится отдельно по (user_id, character),
        # чтобы переключение персонажа не путало контекст диалога.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                user_id INTEGER NOT NULL,
                character TEXT NOT NULL DEFAULT 'aria',
                messages TEXT DEFAULT '[]',
                PRIMARY KEY (user_id, character)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT '',
                facts TEXT DEFAULT '[]',
                character TEXT DEFAULT 'aria'
            )
        """)
        await db.commit()

        # Миграция для уже существующих БД: добавляем колонку character,
        # если её ещё нет (на случай обновления бота без пересоздания базы).
        await _ensure_column(db, "profiles", "character", "TEXT DEFAULT 'aria'")
        await _ensure_column(db, "history", "character", "TEXT NOT NULL DEFAULT 'aria'")


async def _ensure_column(db, table: str, column: str, definition: str):
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = [row[1] async for row in cursor]
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        await db.commit()


# ===================== ИСТОРИЯ ЧАТА =====================

async def get_history(user_id: int, character: str = DEFAULT_CHARACTER) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT messages FROM history WHERE user_id = ? AND character = ?",
            (user_id, character),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return []


async def save_history(user_id: int, messages: list, character: str = DEFAULT_CHARACTER):
    messages = messages[-20:]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO history (user_id, character, messages)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, character) DO UPDATE SET messages = excluded.messages
        """, (user_id, character, json.dumps(messages)))
        await db.commit()


async def clear_history(user_id: int, character: str = None):
    """Если character не указан — чистит историю со ВСЕМИ персонажами (как раньше)."""
    async with aiosqlite.connect(DB_PATH) as db:
        if character is None:
            await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        else:
            await db.execute(
                "DELETE FROM history WHERE user_id = ? AND character = ?",
                (user_id, character),
            )
        await db.commit()


# ===================== ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ (ПАМЯТЬ) =====================

async def get_profile(user_id: int) -> dict:
    """Возвращает {'name': str, 'facts': [str, ...], 'character': str}"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name, facts, character FROM profiles WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "name": row[0] or "",
                    "facts": json.loads(row[1] or "[]"),
                    "character": row[2] or DEFAULT_CHARACTER,
                }
            return {"name": "", "facts": [], "character": DEFAULT_CHARACTER}


async def update_profile_name(user_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO profiles (user_id, name, facts, character)
            VALUES (?, ?, '[]', 'aria')
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
            INSERT INTO profiles (user_id, name, facts, character)
            VALUES (?, '', ?, 'aria')
            ON CONFLICT(user_id) DO UPDATE SET facts = excluded.facts
        """, (user_id, json.dumps(existing)))
        await db.commit()


async def clear_profile(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM profiles WHERE user_id = ?", (user_id,)
        )
        await db.commit()


# ===================== ВЫБОР ПЕРСОНАЖА =====================

async def get_character(user_id: int) -> str:
    profile = await get_profile(user_id)
    return profile["character"]


async def set_character(user_id: int, character: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO profiles (user_id, name, facts, character)
            VALUES (?, '', '[]', ?)
            ON CONFLICT(user_id) DO UPDATE SET character = excluded.character
        """, (user_id, character))
        await db.commit()
