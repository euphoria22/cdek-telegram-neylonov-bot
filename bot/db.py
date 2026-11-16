import json
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(owner_id, name)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    uuid TEXT NOT NULL,
    cdek_number TEXT,
    title TEXT,
    draft TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    owner_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(owner_id, key)
);
CREATE INDEX IF NOT EXISTS orders_user ON orders(user_id, id DESC);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---------- шаблоны ----------
    async def list_templates(self, owner: int) -> list[dict]:
        cur = await self.conn.execute("SELECT id, name, data FROM templates WHERE owner_id=? ORDER BY name", (owner,))
        return [{"id": r["id"], "name": r["name"], "data": json.loads(r["data"])} for r in await cur.fetchall()]

    async def get_template(self, owner: int, tpl_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT id, name, data FROM templates WHERE owner_id=? AND id=?", (owner, tpl_id))
        r = await cur.fetchone()
        return {"id": r["id"], "name": r["name"], "data": json.loads(r["data"])} if r else None

    async def save_template(self, owner: int, name: str, data: dict) -> int:
        await self.conn.execute(
            "INSERT INTO templates(owner_id, name, data, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(owner_id, name) DO UPDATE SET data=excluded.data",
            (owner, name, json.dumps(data, ensure_ascii=False), int(time.time())))
        await self.conn.commit()
        cur = await self.conn.execute("SELECT id FROM templates WHERE owner_id=? AND name=?", (owner, name))
        return (await cur.fetchone())["id"]

    async def rename_template(self, owner: int, tpl_id: int, name: str) -> None:
        await self.conn.execute("UPDATE templates SET name=? WHERE owner_id=? AND id=?", (name, owner, tpl_id))
        await self.conn.commit()

    async def delete_template(self, owner: int, tpl_id: int) -> None:
        await self.conn.execute("DELETE FROM templates WHERE owner_id=? AND id=?", (owner, tpl_id))
        await self.conn.commit()

    # ---------- настройки ----------
    async def get_setting(self, owner: int, key: str) -> str | None:
        cur = await self.conn.execute("SELECT value FROM settings WHERE owner_id=? AND key=?", (owner, key))
        r = await cur.fetchone()
        return r["value"] if r else None

    async def set_setting(self, owner: int, key: str, value: str | None) -> None:
        if value is None:
            await self.conn.execute("DELETE FROM settings WHERE owner_id=? AND key=?", (owner, key))
        else:
            await self.conn.execute("INSERT INTO settings(owner_id, key, value) VALUES(?,?,?) "
                                    "ON CONFLICT(owner_id, key) DO UPDATE SET value=excluded.value", (owner, key, value))
        await self.conn.commit()

    # ---------- заказы ----------
    async def add_order(self, user_id: int, uuid: str, cdek_number: str | None, title: str, draft: dict) -> int:
        cur = await self.conn.execute(
            "INSERT INTO orders(user_id, uuid, cdek_number, title, draft, created_at) VALUES(?,?,?,?,?,?)",
            (user_id, uuid, cdek_number, title, json.dumps(draft, ensure_ascii=False), int(time.time())))
        await self.conn.commit()
        return cur.lastrowid

    async def set_cdek_number(self, order_id: int, number: str) -> None:
        await self.conn.execute("UPDATE orders SET cdek_number=? WHERE id=?", (number, order_id))
        await self.conn.commit()

    async def list_orders(self, user_id: int, limit: int = 20) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT id, uuid, cdek_number, title, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit))
        return [dict(r) for r in await cur.fetchall()]

    async def get_order(self, user_id: int, order_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM orders WHERE user_id=? AND id=?", (user_id, order_id))
        r = await cur.fetchone()
        if not r:
            return None
        d = dict(r)
        d["draft"] = json.loads(d["draft"])
        return d
