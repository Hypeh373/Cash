#!/usr/bin/env python3
"""
Anonymous Chat Bot - Fully featured version with admin panel, ban/unban, broadcast.
"""

import logging
import os
import sqlite3
import threading
import time
import json
from datetime import datetime
from typing import Any, Dict, Optional, List

import telebot
from telebot import types

# =================================================================================
# CONFIGURATION
# =================================================================================
BOT_TOKEN = os.getenv("ANONCHAT_BOT_TOKEN")
DB_PATH = os.getenv("ANONCHAT_DB", "anonchat.db")
# Parse ADMIN_IDS from env
raw_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set()
if raw_admins:
    for x in raw_admins.replace(";", ",").split(","):
        if x.strip().isdigit():
            ADMIN_IDS.add(int(x.strip()))

# =================================================================================
# DATABASE STORAGE CLASS
# =================================================================================
class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    state TEXT,
                    banned INTEGER DEFAULT 0
                )''')
                conn.execute('''CREATE TABLE IF NOT EXISTS active_chats (
                    user1_id INTEGER PRIMARY KEY,
                    user2_id INTEGER
                )''')
                conn.execute('''CREATE TABLE IF NOT EXISTS queue (
                    user_id INTEGER PRIMARY KEY,
                    timestamp REAL
                )''')
                conn.execute('''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )''')
                conn.execute('''CREATE TABLE IF NOT EXISTS bans (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    created_at TEXT
                )''')

    def add_user(self, user):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user.id,))

    def get_state(self, user_id: int) -> Optional[str]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                res = conn.execute("SELECT state FROM users WHERE user_id = ?", (user_id,)).fetchone()
                return res[0] if res else None

    def set_state(self, user_id: int, state: Optional[str]):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                if state is None:
                    conn.execute("UPDATE users SET state = NULL WHERE user_id = ?", (user_id,))
                else:
                    conn.execute("INSERT OR REPLACE INTO users (user_id, state) VALUES (?, ?)", (user_id, state))

    def get_partner(self, user_id: int) -> Optional[int]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                res = conn.execute("SELECT user2_id FROM active_chats WHERE user1_id = ?", (user_id,)).fetchone()
                if res: return res[0]
                res = conn.execute("SELECT user1_id FROM active_chats WHERE user2_id = ?", (user_id,)).fetchone()
                if res: return res[0]
        return None

    def start_chat(self, user1: int, user2: int):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT INTO active_chats (user1_id, user2_id) VALUES (?, ?)", (user1, user2))
                conn.execute("DELETE FROM queue WHERE user_id IN (?, ?)", (user1, user2))

    def end_chat(self, user_id: int):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                partner = self.get_partner(user_id)
                if partner:
                    conn.execute("DELETE FROM active_chats WHERE user1_id IN (?, ?) OR user2_id IN (?, ?)", (user_id, partner, user_id, partner))

    def add_to_queue(self, user_id: int):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT OR REPLACE INTO queue (user_id, timestamp) VALUES (?, ?)", (user_id, time.time()))

    def get_queue(self) -> List[int]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT user_id FROM queue ORDER BY timestamp").fetchall()
                return [r[0] for r in rows]

    def remove_from_queue(self, user_id: int):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM queue WHERE user_id = ?", (user_id,))

    def get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                res = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                return res[0] if res else None

    def set_setting(self, key: str, value: str):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def is_banned(self, user_id: int) -> bool:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                res = conn.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
                return res and res[0] == 1

    def ban_user(self, user_id: int, reason: str):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
                conn.execute("INSERT OR REPLACE INTO bans (user_id, reason, created_at) VALUES (?, ?, ?)",
                            (user_id, reason, datetime.utcnow().isoformat()))

    def unban_user(self, user_id: int):
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))

    def get_ban_info(self, user_id: int) -> Optional[Dict[str, str]]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                res = conn.execute("SELECT reason, created_at FROM bans WHERE user_id = ?", (user_id,)).fetchone()
                if res:
                    return {"reason": res[0], "created_at": res[1]}
        return None

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                active_chats = conn.execute("SELECT COUNT(*) FROM active_chats").fetchone()[0]
                queue_size = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
                return {"total_users": total_users, "active_chats": active_chats, "queue_size": queue_size}

# =================================================================================
# INIT
# =================================================================================
db = Storage(DB_PATH)
bot = telebot.TeleBot(BOT_TOKEN)

# =================================================================================
# HELPERS
# =================================================================================
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("/next"), types.KeyboardButton("/stop"))
    return markup

def admin_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("📣 Рассылка", callback_data="broadcast"))
    markup.add(types.InlineKeyboardButton("🚫 Бан/Разбан", callback_data="ban_menu"))
    markup.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stats"))
    return markup

def ban_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("➕ Забанить", callback_data="ban_add"))
    markup.add(types.InlineKeyboardButton("♻️ Разбанить", callback_data="ban_remove"))
    markup.add(types.InlineKeyboardButton("📋 Список банов", callback_data="ban_list"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back"))
    return markup

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# =================================================================================
# HANDLERS
# =================================================================================

@bot.message_handler(commands=['start'])
def start_handler(message):
    db.add_user(message.from_user)
    if db.is_banned(message.from_user.id):
        ban_info = db.get_ban_info(message.from_user.id)
        bot.send_message(message.chat.id, f"🚫 Вы заблокированы.\nПричина: {ban_info['reason'] if ban_info else 'Не указана'}")
        return
    welcome_text = db.get_setting('welcome_text') or "Добро пожаловать в анонимный чат! Используйте /next для поиска собеседника или /stop для выхода."
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu())

@bot.message_handler(commands=['next'])
def next_handler(message):
    if db.is_banned(message.from_user.id):
        return
    user_id = message.from_user.id
    db.end_chat(user_id)
    db.add_to_queue(user_id)
    queue = db.get_queue()
    if len(queue) >= 2:
        user1 = queue[0]
        user2 = queue[1]
        db.start_chat(user1, user2)
        db.remove_from_queue(user1)
        db.remove_from_queue(user2)
        bot.send_message(user1, "🎉 Собеседник найден! /next — искать нового, /stop — закончить диалог")
        bot.send_message(user2, "🎉 Собеседник найден! /next — искать нового, /stop — закончить диалог")
    else:
        bot.send_message(user_id, "🔄 Ищем собеседника... Ожидайте.")

@bot.message_handler(commands=['stop'])
def stop_handler(message):
    user_id = message.from_user.id
    partner = db.get_partner(user_id)
    if partner:
        db.end_chat(user_id)
        bot.send_message(user_id, "💔 Диалог окончен.")
        bot.send_message(partner, "💔 Собеседник вышел из диалога.")
    else:
        db.remove_from_queue(user_id)
        bot.send_message(user_id, "Вы не в диалоге.")

@bot.message_handler(commands=['admin'])
def admin_handler(message):
    if not is_admin(message.from_user.id):
        return
    text = "⚙️ Админ панель:"
    bot.send_message(message.chat.id, text, reply_markup=admin_menu())

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    bot.answer_callback_query(call.id)

    if call.data == "broadcast":
        msg = bot.edit_message_text("Отправьте текст рассылки:", user_id, call.message.message_id,
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")))
        db.set_state(user_id, 'waiting_broadcast')

    elif call.data == "ban_menu":
        bot.edit_message_text("🚫 Управление банами:", user_id, call.message.message_id, reply_markup=ban_menu())

    elif call.data == "ban_add":
        msg = bot.edit_message_text("Введите ID пользователя для бана (формат: ID Причина):", user_id, call.message.message_id,
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="ban_menu")))
        db.set_state(user_id, 'waiting_ban')

    elif call.data == "ban_remove":
        msg = bot.edit_message_text("Введите ID пользователя для разбана:", user_id, call.message.message_id,
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="ban_menu")))
        db.set_state(user_id, 'waiting_unban')

    elif call.data == "ban_list":
        bans = []
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("SELECT user_id, reason, created_at FROM bans ORDER BY created_at DESC LIMIT 10").fetchall()
            for row in rows:
                bans.append(f"ID: {row[0]}, Причина: {row[1]}, Дата: {row[2]}")
        text = "📋 Список последних банов:\n\n" + "\n".join(bans) if bans else "Банов нет."
        bot.edit_message_text(text, user_id, call.message.message_id, reply_markup=ban_menu())

    elif call.data == "stats":
        stats = db.get_stats()
        text = f"📊 Статистика:\n👥 Всего пользователей: {stats['total_users']}\n💬 Активных диалогов: {stats['active_chats']}\n🔄 В очереди: {stats['queue_size']}"
        bot.edit_message_text(text, user_id, call.message.message_id, reply_markup=admin_menu())

    elif call.data == "admin_back":
        bot.edit_message_text("⚙️ Админ панель:", user_id, call.message.message_id, reply_markup=admin_menu())

@bot.message_handler(func=lambda m: True)
def message_processor(message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        return

    state = db.get_state(user_id)
    if state == 'waiting_broadcast' and is_admin(user_id):
        # Broadcast to all users
        with sqlite3.connect(DB_PATH) as conn:
            users = conn.execute("SELECT user_id FROM users WHERE banned = 0").fetchall()
        for user in users:
            try:
                bot.send_message(user[0], message.text)
            except:
                pass
        bot.send_message(user_id, "✅ Рассылка завершена.")
        db.set_state(user_id, None)

    elif state == 'waiting_ban' and is_admin(user_id):
        parts = message.text.split(maxsplit=1)
        if len(parts) >= 2:
            target_id = int(parts[0]) if parts[0].isdigit() else None
            reason = parts[1]
            if target_id:
                db.ban_user(target_id, reason)
                bot.send_message(user_id, f"✅ Пользователь {target_id} забанен. Причина: {reason}")
            else:
                bot.send_message(user_id, "❌ Неверный ID.")
        else:
            bot.send_message(user_id, "Формат: ID Причина")
        db.set_state(user_id, None)

    elif state == 'waiting_unban' and is_admin(user_id):
        if message.text.isdigit():
            target_id = int(message.text)
            db.unban_user(target_id)
            bot.send_message(user_id, f"✅ Пользователь {target_id} разбанен.")
        else:
            bot.send_message(user_id, "❌ Неверный ID.")
        db.set_state(user_id, None)

    else:
        # Forward to partner
        partner = db.get_partner(user_id)
        if partner:
            try:
                bot.send_message(partner, message.text)
            except:
                pass

if __name__ == '__main__':
    logging.info("AnonChat Bot started")
    bot.polling()