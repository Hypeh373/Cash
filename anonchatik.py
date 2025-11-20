#!/usr/bin/env python3
import os
import telebot
from telebot import types
import sqlite3
import logging
import time

logging.basicConfig(filename='anonchat_bot.log', level=logging.INFO)

BOT_TOKEN = os.getenv('ANONCHAT_BOT_TOKEN')
DB_PATH = os.getenv('ANONCHAT_DB', 'anonchat.db')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()]

if not BOT_TOKEN:
    logging.error("NO BOT TOKEN")
    # Exit or wait? Exit is better to restart cleanly
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# DB Init
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, state TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS active_chats (user1_id INTEGER PRIMARY KEY, user2_id INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS queue (user_id INTEGER PRIMARY KEY, timestamp REAL)''')

init_db()

# Helpers
def get_state(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT state FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return res[0] if res else None

def set_state(user_id, state):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO users (user_id, state) VALUES (?, ?)", (user_id, state))

def get_partner(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        # Check both directions
        res = conn.execute("SELECT user2_id FROM active_chats WHERE user1_id = ?", (user_id,)).fetchone()
        if res: return res[0]
        res = conn.execute("SELECT user1_id FROM active_chats WHERE user2_id = ?", (user_id,)).fetchone()
        if res: return res[0]
    return None

def end_chat(user_id):
    partner_id = get_partner(user_id)
    if partner_id:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM active_chats WHERE user1_id = ? OR user2_id = ?", (user_id, user_id))
            conn.execute("DELETE FROM active_chats WHERE user1_id = ? OR user2_id = ?", (partner_id, partner_id)) # Safety
        
        try: bot.send_message(user_id, "Диалог завершен.\n/next — искать нового собеседника")
        except: pass
        try: bot.send_message(partner_id, "Собеседник завершил диалог.\n/next — искать нового собеседника")
        except: pass
        
        set_state(user_id, 'idle')
        set_state(partner_id, 'idle')
    else:
        # Remove from queue if there
        with sqlite3.connect(DB_PATH) as conn:
             conn.execute("DELETE FROM queue WHERE user_id = ?", (user_id,))
        set_state(user_id, 'idle')
        try: bot.send_message(user_id, "Поиск остановлен.")
        except: pass

def find_partner(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        # Get oldest from queue
        res = conn.execute("SELECT user_id FROM queue WHERE user_id != ? ORDER BY timestamp ASC LIMIT 1", (user_id,)).fetchone()
        if res:
            partner_id = res[0]
            # Match found
            conn.execute("DELETE FROM queue WHERE user_id = ?", (partner_id,))
            conn.execute("DELETE FROM queue WHERE user_id = ?", (user_id,)) # Just in case
            conn.execute("INSERT INTO active_chats (user1_id, user2_id) VALUES (?, ?)", (user_id, partner_id))
            
            set_state(user_id, 'chatting')
            set_state(partner_id, 'chatting')
            
            msg = "Собеседник найден\n\n/next — искать нового собеседника\n/stop — закончить диалог"
            try: bot.send_message(user_id, msg)
            except: pass
            try: bot.send_message(partner_id, msg)
            except: pass
            return True
        else:
            # Add to queue
            conn.execute("INSERT OR REPLACE INTO queue (user_id, timestamp) VALUES (?, ?)", (user_id, time.time()))
            set_state(user_id, 'search')
            try: bot.send_message(user_id, "Ищем собеседника...")
            except: pass
            return False

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    set_state(user_id, 'idle')
    bot.send_message(user_id, "Привет! Это анонимный чат.\nНажми /next чтобы найти собеседника.")

@bot.message_handler(commands=['next', 'search'])
def next_handler(message):
    user_id = message.from_user.id
    end_chat(user_id) # if any
    find_partner(user_id)

@bot.message_handler(commands=['stop'])
def stop_handler(message):
    user_id = message.from_user.id
    end_chat(user_id)

# Broadcast (Admin)
@bot.message_handler(commands=['broadcast'])
def broadcast_handler(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(message.chat.id, "Отправьте сообщение для рассылки.")
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    if message.content_type == 'text' and message.text == '/cancel':
        bot.send_message(message.chat.id, "Отменено.")
        return
        
    bot.send_message(message.chat.id, "Начинаю рассылку...")
    with sqlite3.connect(DB_PATH) as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()
    
    count = 0
    for u in users:
        try:
            bot.copy_message(u[0], message.chat.id, message.message_id)
            count += 1
            time.sleep(0.05)
        except:
            pass
    bot.send_message(message.chat.id, f"Рассылка завершена. Доставлено: {count}")

@bot.message_handler(content_types=['text', 'photo', 'video', 'voice', 'sticker', 'animation'])
def chat_handler(message):
    user_id = message.from_user.id
    state = get_state(user_id)
    
    if state == 'chatting':
        partner_id = get_partner(user_id)
        if partner_id:
            try:
                bot.copy_message(partner_id, message.chat.id, message.message_id)
            except:
                # Partner blocked or failed
                end_chat(user_id)
                bot.send_message(user_id, "Собеседник отключился.")
        else:
            # State inconsistency
            set_state(user_id, 'idle')
    elif state == 'search':
        bot.send_message(user_id, "Все еще ищем... Ждите.")
    else:
        if message.text and message.text.startswith('/'): return # Ignore other commands
        bot.send_message(user_id, "Вы не в диалоге. Нажмите /next для поиска.")

if __name__ == '__main__':
    while True:
        try:
            bot.polling(non_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(5)
