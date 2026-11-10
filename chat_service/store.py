"""Saves conversations in a SQLite file (one file, no database server needed).

Three tables:
  conversations  one row per shop application: who owns it, and the agent's saved memory
  messages       every message in every conversation, in order
  reviews        reviews opened for iPe staff (stand-in for the onboarding service)
"""
import datetime as dt
import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, agent_state TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS conv_by_merchant ON conversations(merchant_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
  text TEXT NOT NULL, attachments TEXT NOT NULL, card TEXT, client_message_id TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS msg_by_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, shop TEXT, assignee TEXT,
  volume_inr INTEGER, missing TEXT, updated_at TEXT NOT NULL);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # ---------------- conversations
    def create_conversation(self, cid, merchant_id, state):
        t = now()
        with self.lock, self.db:
            self.db.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, merchant_id, json.dumps(state), t, t))

    def get_conversation(self, cid):
        r = self.db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(r, agent_state=json.loads(r["agent_state"])) if r else None

    def list_conversations(self, merchant_id):
        rows = self.db.execute("SELECT * FROM conversations WHERE merchant_id=? ORDER BY updated_at DESC, id",
                               (merchant_id,)).fetchall()
        return [dict(r, agent_state=json.loads(r["agent_state"])) for r in rows]

    def save_state(self, cid, state):
        with self.lock, self.db:
            self.db.execute("UPDATE conversations SET agent_state=?, updated_at=? WHERE id=?", (json.dumps(state), now(), cid))

    # ---------------- messages
    def add_message(self, cid, role, text, attachments=None, card=None, client_message_id=None):
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO messages (conversation_id, role, text, attachments, card, client_message_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid, role, text, json.dumps(attachments or []), json.dumps(card) if card else None, client_message_id, now()))
            return cur.lastrowid

    def messages(self, cid):
        rows = self.db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
        return [{"id": r["id"], "role": r["role"], "text": r["text"], "attachments": json.loads(r["attachments"]),
                 "card": json.loads(r["card"]) if r["card"] else None, "created_at": r["created_at"]} for r in rows]

    def reply_after(self, cid, client_message_id):
        """For a retried message: the assistant reply stored right after the original."""
        r = self.db.execute("SELECT id FROM messages WHERE conversation_id=? AND client_message_id=?",
                            (cid, client_message_id)).fetchone()
        if not r:
            return None
        nxt = self.db.execute("SELECT id FROM messages WHERE conversation_id=? AND id>? AND role='assistant' ORDER BY id LIMIT 1",
                              (cid, r["id"])).fetchone()
        return nxt["id"] if nxt else None

    # ---------------- reviews (onboarding service stand-in)
    def upsert_review(self, review_id, cid, shop, assignee, volume, missing):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET "
                "missing=excluded.missing, updated_at=excluded.updated_at",
                (review_id, cid, shop, assignee, volume, json.dumps(missing), now()))

    def review(self, review_id):
        r = self.db.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        return dict(r, missing=json.loads(r["missing"])) if r else None
