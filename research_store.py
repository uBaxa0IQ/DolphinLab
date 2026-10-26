"""Local factual state and durable mailboxes. All mutations use one event-loop thread."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:12]


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.root = path.parent.resolve()
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,data TEXT,
            PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS mail(seq INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT,sender TEXT,body TEXT,created REAL,delivered INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,data TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS receipts(agent TEXT,call_id TEXT,result TEXT,
            PRIMARY KEY(agent,call_id));
        """)
        self.db.commit()
        self.log_error = None
        self.sync_events()

    def sync_events(self):
        target = self.root / "events.jsonl"
        temporary = target.with_suffix(".jsonl.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                for seq, kind, data, created in self.db.execute("SELECT seq,kind,data,created FROM events ORDER BY seq"):
                    stream.write(dumps({"seq": seq, "time": created, "kind": kind, "data": json.loads(data)}) + "\n")
            os.replace(temporary, target)
            self.log_error = None
        except OSError as error:
            self.log_error = f"{type(error).__name__}: {error}"
            temporary.unlink(missing_ok=True)

    def put(self, kind: str, ident: str, data: dict):
        self.db.execute("INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                        (kind, ident, dumps(data)))
        self.db.commit()

    def get(self, kind: str, ident: str) -> dict:
        row = self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?",
                              (kind, ident)).fetchone()
        if row is None:
            raise KeyError(f"Unknown {kind}: {ident}")
        return json.loads(row[0])

    def all(self, kind: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT data FROM objects WHERE kind=? ORDER BY rowid", (kind,))]

    def event(self, kind: str, data: dict):
        now = time.time()
        cursor = self.db.execute("INSERT INTO events(kind,data,created) VALUES(?,?,?)",
                                 (kind, dumps(data), now))
        self.db.commit()
        try:
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(dumps({"seq": cursor.lastrowid, "time": now, "kind": kind, "data": data}) + "\n")
        except OSError as error:
            # SQLite remains authoritative and can rebuild the mirror after a transient file lock.
            self.log_error = f"{type(error).__name__}: {error}"

    def send(self, recipient: str, sender: str, body: str) -> int:
        agent = self.get("agent", recipient)
        if agent["state"] == "closed":
            raise ValueError("Recipient is closed; reassign an active team instead")
        cur = self.db.execute("INSERT INTO mail(recipient,sender,body,created) VALUES(?,?,?,?)",
                              (recipient, sender, body, time.time()))
        self.db.commit()
        self.event("message_sent", {"id": cur.lastrowid, "to": recipient, "from": sender, "text": body})
        return cur.lastrowid

    def pending(self, recipient: str) -> bool:
        return self.db.execute("SELECT 1 FROM mail WHERE recipient=? AND delivered=0 LIMIT 1",
                               (recipient,)).fetchone() is not None

    def take_mail(self, agent: dict, limit: int = 12000) -> int:
        """Atomically append a bounded batch to persistent history, then acknowledge it.

        API failure cannot lose a message: it is already part of the next request.
        Mail arriving during a request remains in the mailbox for the next boundary.
        """
        rows = self.db.execute("SELECT seq,sender,body FROM mail WHERE recipient=? "
                               "AND delivered=0 ORDER BY seq LIMIT 50", (agent["id"],)).fetchall()
        chosen, size = [], 0
        for row in rows:
            if chosen and size + len(row[2]) > limit:
                break
            chosen.append(row)
            size += len(row[2])
        if not chosen:
            return 0
        content = "New addressed messages (chronological):\n" + "\n".join(
            f"[{seq} from {sender}] {body}" for seq, sender, body in chosen)
        agent["history"].append({"role": "user", "content": content})
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES('agent',?,?)",
                            (agent["id"], dumps(agent)))
            self.db.executemany("UPDATE mail SET delivered=1 WHERE seq=?", [(r[0],) for r in chosen])
        self.event("mail_delivered", {"agent": agent["id"], "message_ids": [r[0] for r in chosen]})
        return len(chosen)

    def receipt(self, agent: str, call_id: str):
        row = self.db.execute("SELECT result FROM receipts WHERE agent=? AND call_id=?",
                              (agent, call_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_receipt(self, agent: str, call_id: str, result):
        self.db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?)",
                        (agent, call_id, dumps(result)))
        self.db.commit()

    def close(self):
        try:
            self.db.close()
        finally:
            callback = getattr(self, "on_close", None)
            if callback:
                callback()

    def recent_mail(self, recipients, limit=20):
        if not recipients:
            return []
        placeholders = ",".join("?" for _ in recipients)
        rows = self.db.execute(f"SELECT seq,recipient,sender,body,created,delivered FROM mail "
                               f"WHERE recipient IN ({placeholders}) ORDER BY seq DESC LIMIT ?",
                               (*recipients, limit)).fetchall()
        return [dict(zip(("id", "to", "from", "text", "created", "delivered"), row)) for row in reversed(rows)]

    def retire_mail(self, recipient):
        """Retain old messages for recall, but do not replay obsolete instructions after reset."""
        self.db.execute("UPDATE mail SET delivered=1 WHERE recipient=?", (recipient,))
        self.db.commit()
