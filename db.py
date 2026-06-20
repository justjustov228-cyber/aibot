import aiosqlite
import json
from datetime import date

DB_PATH = "memory.db"

DEFAULT_CHARACTER = "aria"
MAX_CUSTOM_CHARACTERS_BASE = 0  # без рефералов кастомных персонажей нет


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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
                character TEXT DEFAULT 'aria',
                streak_count INTEGER DEFAULT 0,
                last_active_date TEXT DEFAULT '',
                referred_by INTEGER DEFAULT NULL,
                referral_count INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS custom_characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT DEFAULT '🎭',
                system_prompt TEXT NOT NULL,
                intro TEXT NOT NULL,
                created_at TEXT DEFAULT ''
            )
        """)
        await db.commit()

        # Миграции для уже существующих БД (на случай обновления без пересоздания базы)
        await _ensure_column(db, "profiles", "character", "TEXT DEFAULT 'aria'")
        await _ensure_column(db, "history", "character", "TEXT NOT NULL DEFAULT 'aria'")
        await _ensure_column(db, "profiles", "streak_count", "INTEGER DEFAULT 0")
        await _ensure_column(db, "profiles", "last_active_date", "TEXT DEFAULT ''")
        await _ensure_column(db, "profiles", "referred_by", "INTEGER DEFAULT NULL")
        await _ensure_column(db, "profiles", "referral_count", "INTEGER DEFAULT 0")


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
    """Если character не указан — чистит историю со ВСЕМИ персонажами."""
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

async def _ensure_profile_row(db, user_id: int):
    await db.execute("""
        INSERT INTO profiles (user_id, name, facts, character)
        VALUES (?, '', '[]', 'aria')
        ON CONFLICT(user_id) DO NOTHING
    """, (user_id,))


async def get_profile(user_id: int) -> dict:
    """Возвращает полный профиль пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT name, facts, character, streak_count, last_active_date,
                      referred_by, referral_count
               FROM profiles WHERE user_id = ?""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "name": row[0] or "",
                    "facts": json.loads(row[1] or "[]"),
                    "character": row[2] or DEFAULT_CHARACTER,
                    "streak_count": row[3] or 0,
                    "last_active_date": row[4] or "",
                    "referred_by": row[5],
                    "referral_count": row[6] or 0,
                }
            return {
                "name": "", "facts": [], "character": DEFAULT_CHARACTER,
                "streak_count": 0, "last_active_date": "",
                "referred_by": None, "referral_count": 0,
            }


async def update_profile_name(user_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_profile_row(db, user_id)
        await db.execute("UPDATE profiles SET name = ? WHERE user_id = ?", (name, user_id))
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
        await _ensure_profile_row(db, user_id)
        await db.execute(
            "UPDATE profiles SET facts = ? WHERE user_id = ?",
            (json.dumps(existing), user_id)
        )
        await db.commit()


async def clear_profile(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
        await db.commit()


# ===================== ВЫБОР ПЕРСОНАЖА =====================

async def get_character(user_id: int) -> str:
    profile = await get_profile(user_id)
    return profile["character"]


async def set_character(user_id: int, character: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_profile_row(db, user_id)
        await db.execute(
            "UPDATE profiles SET character = ? WHERE user_id = ?",
            (character, user_id)
        )
        await db.commit()


# ===================== СТРИКИ =====================

async def touch_streak(user_id: int) -> dict:
    """
    Вызывается при каждом текстовом сообщении пользователя.
    Обновляет стрик: если писал вчера — +1, если сегодня уже считан — без изменений,
    если пропустил день(и) — стрик сбрасывается на 1.
    Возвращает {'streak_count': int, 'is_new_day': bool, 'streak_broken': bool}
    """
    today = date.today().isoformat()
    profile = await get_profile(user_id)
    last_active = profile["last_active_date"]
    streak = profile["streak_count"]

    if last_active == today:
        return {"streak_count": streak, "is_new_day": False, "streak_broken": False}

    streak_broken = False
    if not last_active:
        streak = 1
    else:
        try:
            last_date = date.fromisoformat(last_active)
            delta_days = (date.today() - last_date).days
        except ValueError:
            delta_days = 999

        if delta_days == 1:
            streak += 1
        else:
            streak_broken = delta_days > 1
            streak = 1

    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_profile_row(db, user_id)
        await db.execute(
            "UPDATE profiles SET streak_count = ?, last_active_date = ? WHERE user_id = ?",
            (streak, today, user_id)
        )
        await db.commit()

    return {"streak_count": streak, "is_new_day": True, "streak_broken": streak_broken}


async def get_users_missed_yesterday() -> list:
    """
    Возвращает user_id всех, кто писал когда-то, но не сегодня и не вчера
    (то есть пропустил ровно один день — самый момент напомнить, пока стрик жив технически
    только сегодня, но человек уже не заходил со вчера).
    """
    from datetime import timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM profiles WHERE last_active_date = ? AND streak_count > 0",
            (yesterday,)
        ) as cursor:
            rows = [row[0] async for row in cursor]
            return rows


# ===================== РЕФЕРАЛЬНАЯ ПРОГРАММА =====================

async def set_referrer_if_new(user_id: int, referrer_id: int) -> bool:
    """
    Устанавливает referred_by, ТОЛЬКО если у пользователя его ещё нет
    (профиль только создаётся) и referrer_id != user_id.
    Возвращает True, если связь была успешно установлена (новый реферал засчитан).
    """
    if referrer_id == user_id:
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT referred_by FROM profiles WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        # Если профиль уже существует и у него уже есть referred_by — реферал не засчитывается повторно
        if row is not None and row[0] is not None:
            return False
        # Если профиль уже существует, но без referred_by (старый юзер, который теперь "перешёл по ссылке") —
        # не засчитываем, иначе это легко накрутить, открыв чат заново по чужой ссылке.
        if row is not None:
            return False

        await db.execute("""
            INSERT INTO profiles (user_id, name, facts, character, referred_by)
            VALUES (?, '', '[]', 'aria', ?)
        """, (user_id, referrer_id))
        await db.commit()

    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_profile_row(db, referrer_id)
        await db.execute(
            "UPDATE profiles SET referral_count = referral_count + 1 WHERE user_id = ?",
            (referrer_id,)
        )
        await db.commit()

    return True


async def get_referral_count(user_id: int) -> int:
    profile = await get_profile(user_id)
    return profile["referral_count"]


# ===================== КАСТОМНЫЕ ПЕРСОНАЖИ =====================

async def get_custom_character_limit(user_id: int) -> int:
    """Лимит кастомных персонажей = количество рефералов. Без рефералов — 0."""
    referral_count = await get_referral_count(user_id)
    return MAX_CUSTOM_CHARACTERS_BASE + referral_count


async def get_custom_characters(owner_id: int) -> list:
    """Возвращает список кастомных персонажей пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, slug, name, emoji, system_prompt, intro
               FROM custom_characters WHERE owner_id = ? ORDER BY id""",
            (owner_id,)
        ) as cursor:
            rows = [row async for row in cursor]
            return [
                {
                    "id": r[0], "slug": r[1], "name": r[2],
                    "emoji": r[3], "system_prompt": r[4], "intro": r[5],
                }
                for r in rows
            ]


async def get_custom_character_count(owner_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM custom_characters WHERE owner_id = ?", (owner_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def add_custom_character(owner_id: int, slug: str, name: str, emoji: str,
                                system_prompt: str, intro: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO custom_characters (owner_id, slug, name, emoji, system_prompt, intro, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (owner_id, slug, name, emoji, system_prompt, intro, date.today().isoformat()))
        await db.commit()


async def get_custom_character_by_slug(owner_id: int, slug: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, slug, name, emoji, system_prompt, intro
               FROM custom_characters WHERE owner_id = ? AND slug = ?""",
            (owner_id, slug)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row[0], "slug": row[1], "name": row[2],
                    "emoji": row[3], "system_prompt": row[4], "intro": row[5],
                }
            return None


async def delete_custom_character(owner_id: int, slug: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM custom_characters WHERE owner_id = ? AND slug = ?",
            (owner_id, slug)
        )
        await db.commit()
