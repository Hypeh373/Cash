# -*- coding: utf-8 -*-
import telebot
from telebot import types
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
import threading
import time
import re
import random
import sys
import os
import json
import requests
from html import escape
import traceback

# --- ИНТЕГРАЦИЯ FLYER API ---
try:
    import asyncio
    from flyerapi import Flyer, APIError as FlyerAPIError
    from functools import wraps
    FLYER_AVAILABLE = True
except ImportError:
    FLYER_AVAILABLE = False
    def wraps(f): return f
    class Flyer: pass
    class FlyerAPIError(Exception): pass
# -----------------------------

# --- НАСТРОЙКА ЛОГИРОВАНИЯ (САМОЕ НАЧЛО!) ---
log_file = f'bot_{sys.argv[1] if len(sys.argv) > 1 else "unknown"}_admin.log'
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter(f"%(asctime)s [BotID:{sys.argv[1] if len(sys.argv) > 1 else '???'}] - %(levelname)s - %(message)s")
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(f"%(asctime)s [BotID:{sys.argv[1] if len(sys.argv) > 1 else '???'}] - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
# ------------------------------------------------


# =================================================================================
# --------------------------- ЗАГРУЗКА КОНФИГУРАЦИИ -------------------------------
# =================================================================================

CONSTRUCTOR_BOT_USERNAME = "CreatorShop1_Bot"
SHOW_BRANDING = os.environ.get('CREATOR_BRANDING') == 'true'

if len(sys.argv) < 2 or not sys.argv[1].isdigit():
    error_msg = f"ОШИБКА: Запустите скрипт с ID бота в качестве аргумента. Пример: python {sys.argv[0]} 123"
    print(error_msg)
    logging.critical(error_msg)
    sys.exit(1)

BOT_ID = int(sys.argv[1])
CREATOR_DB_NAME = 'creator_data4.db'
creator_db_lock = threading.Lock()

def load_config():
    try:
        with creator_db_lock:
            conn = sqlite3.connect(f'file:{CREATOR_DB_NAME}?mode=ro', uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bots WHERE id = ?", (BOT_ID,))
            config = cursor.fetchone()
            conn.close()
        
        if config: return dict(config)
        else:
            logging.critical(f"ОШИБКА: Конфигурация для бота с ID {BOT_ID} не найдена.")
            sys.exit(1)
    except sqlite3.Error as e:
        logging.critical(f"ОШИБКА: Не удалось прочитать БД конструктора: {e}")
        sys.exit(1)

config = load_config()

TOKEN = config.get('bot_token')
if not TOKEN:
    logging.critical(f"ОШИБКА: Для бота ID {BOT_ID} не установлен токен.")
    sys.exit(1)

ADMIN_ID = config.get('owner_id')
try:
    admins_json = config.get('admins')
    ADMINS_LIST = json.loads(admins_json) if admins_json else []
    if ADMIN_ID and ADMIN_ID not in ADMINS_LIST: ADMINS_LIST.append(ADMIN_ID)
except (json.JSONDecodeError, TypeError):
    ADMINS_LIST = [ADMIN_ID] if ADMIN_ID else []
    
DB_NAME = f'dbs/bot_{BOT_ID}_clicker_data.db'
CLICK_REWARD_MIN = float(config.get('click_reward_min', 0.001))
CLICK_REWARD_MAX = float(config.get('click_reward_max', 0.005))
ENERGY_MAX = int(config.get('energy_max', 1000))
ENERGY_PER_CLICK = 10
ENERGY_REGEN_RATE_PER_SEC = int(config.get('energy_regen_rate', 2))
WELCOME_BONUS = float(config.get('welcome_bonus_clicker', 1.0))
DAILY_BONUS_REWARD = float(config.get('daily_bonus_clicker', 0.5))
DAILY_BONUS_COOLDOWN_HOURS = int(config.get('daily_bonus_cooldown_clicker', 12))
REFERRAL_BONUS_REFERRER = float(config.get('ref_bonus_referrer_clicker', 0.2))
REFERRAL_BONUS_NEW_USER = float(config.get('ref_bonus_new_user_clicker', 0.1))
WITHDRAWAL_MIN = float(config.get('withdrawal_min_clicker', 10.0))
WITHDRAWAL_METHOD_TEXT = config.get('withdrawal_method_text_clicker', 'Payeer-кошелек')
PAYMENTS_CHANNEL = config.get('payments_channel_clicker')
SUPPORT_CHAT = config.get('support_chat_clicker')

FLYER_API_KEY = config.get('clicker_flyer_api_key') or os.environ.get('FLYER_API_KEY')
FLYER_ENABLED = config.get('clicker_op_enabled', False)
FLYER_CHECK_INTERVAL_MIN = 20
FLYER_CHECK_INTERVAL_MAX = 30


FLYER_INCOMPLETE_STATUSES = ('incomplete', 'abort')
flyer = None
async_loop = None

logging.info(f"Flyer 'flyerapi' available: {FLYER_AVAILABLE}")
logging.info(f"Flyer enabled in settings: {FLYER_ENABLED}")
logging.info(f"Flyer API key found: {'Yes' if FLYER_API_KEY else 'No'}")

if FLYER_AVAILABLE:
    async_loop = asyncio.new_event_loop()
    if FLYER_ENABLED and FLYER_API_KEY:
        try:
            flyer = Flyer(key=FLYER_API_KEY)
            logging.info("Flyer client initialized successfully.")
        except Exception as _e:
            flyer = None
            logging.error(f"Failed to initialize Flyer: {_e}")
            traceback.print_exc()
else:
    logging.warning("Flyer library not found. Flyer functionality will be disabled.")

def run_async_from_sync(coro):
    if not async_loop or not async_loop.is_running():
        logging.error("Asyncio event loop is not running. Cannot execute coroutine.")
        # Закрываем корутину, чтобы избежать предупреждения 'coroutine never awaited'
        coro.close()
        return None 
    future = asyncio.run_coroutine_threadsafe(coro, async_loop)
    try: 
        return future.result(timeout=15)
    except asyncio.TimeoutError: 
        logging.error(f"Таймаут выполнения async задачи Flyer.")
        return None
    except Exception as e: 
        logging.error(f"Ошибка выполнения async задачи Flyer: {e}")
        return None
# =================================================================================
# ------------------- ИНТЕГРАЦИЯ "МОИ ОП" ИЗ КОНСТРУКТОРА --------------------------
# =================================================================================

user_recharge_state = {}

def get_creator_setting(key):
    with creator_db_lock:
        try:
            conn = sqlite3.connect(f'file:{CREATOR_DB_NAME}?mode=ro', uri=True, timeout=15)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
        except sqlite3.Error as e:
            logging.error(f"Ошибка чтения настройки '{key}' из главной БД: {e}")
            return None
            
def credit_owner_for_task(owner_id: int, amount: float, user_id: int, task: dict):
    task_signature = task.get('signature')
    task_type = task.get('task')
    
    if not task_signature:
        logging.error(f"[BotID:{BOT_ID}] Не удалось получить signature для задачи. Начисление невозможно. Task: {task}")
        return

    with creator_db_lock:
        try:
            conn_creator = sqlite3.connect(CREATOR_DB_NAME, timeout=15)
            cursor_creator = conn_creator.cursor()
            
            if task_type == 'subscribe channel':
                check_after = datetime.utcnow() + timedelta(hours=24)
                try:
                    cursor_creator.execute(
                        "INSERT INTO pending_flyer_rewards (owner_id, bot_id, task_signature, amount, check_after_timestamp) VALUES (?, ?, ?, ?, ?)",
                        (owner_id, BOT_ID, task_signature, amount, check_after)
                    )
                    cursor_creator.execute("UPDATE users SET frozen_balance = frozen_balance + ? WHERE user_id = ?", (amount, owner_id))
                    conn_creator.commit()
                    logging.info(f"[FLYER_CREDIT_HOLD] [BotID:{BOT_ID}] Начислено {amount:.4f} руб. НА УДЕРЖАНИЕ владельцу {owner_id} за задачу {task_signature}")
                except sqlite3.IntegrityError:
                    logging.warning(f"[BotID:{BOT_ID}] Попытка повторно добавить задачу {task_signature} в очередь. Пропускаем.")
            else:
                cursor_creator.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, owner_id))
                conn_creator.commit()
                logging.info(f"[FLYER_CREDIT_DIRECT] [BotID:{BOT_ID}] Начислено {amount:.4f} руб. НАПРЯМУЮ владельцу {owner_id} за задачу {task_signature} (тип: {task_type}).")

            conn_creator.close()
        except Exception as e:
            logging.error(f"[BotID:{BOT_ID}] Критическая ошибка в credit_owner_for_task для владельца {owner_id}: {e}")
            traceback.print_exc()

def get_admin_op_tasks(user_id):
    admin_tasks = []
    try:
        with creator_db_lock:
            conn_creator = sqlite3.connect(f'file:{CREATOR_DB_NAME}?mode=ro', uri=True, timeout=15)
            conn_creator.row_factory = sqlite3.Row
            cursor = conn_creator.cursor()
            query = """
                SELECT a.id, a.title, a.resource_link, a.reward
                FROM admin_tasks AS a
                LEFT JOIN user_completed_admin_tasks AS u ON a.id = u.task_id AND u.user_id = ?
                WHERE u.user_id IS NULL AND a.is_active = 1
            """
            cursor.execute(query, (user_id,))
            tasks_from_db = cursor.fetchall()
            conn_creator.close()

        for task_row in tasks_from_db:
            admin_tasks.append({
                'task': task_row['title'],
                'links': [task_row['resource_link']],
                'signature': f"admin_op_{task_row['id']}",
                'reward': task_row['reward']
            })
        if admin_tasks:
            logging.info(f"[ADMIN_OP] Найдено {len(admin_tasks)} новых заданий 'Мои ОП' для пользователя {user_id}.")
        return admin_tasks
    except sqlite3.Error as e:
        logging.error(f"Ошибка получения заданий 'Мои ОП' из БД конструктора: {e}")
        return []

def credit_owner_for_admin_op(owner_id, user_id, task_id, reward):
    try:
        with creator_db_lock:
            conn_creator = sqlite3.connect(CREATOR_DB_NAME, timeout=15)
            cursor = conn_creator.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, owner_id))
            cursor.execute("INSERT OR IGNORE INTO user_completed_admin_tasks (user_id, task_id) VALUES (?, ?)", (user_id, task_id))
            conn_creator.commit()
            conn_creator.close()
        logging.info(f"[ADMIN_OP_CREDIT] Владельцу {owner_id} начислено {reward} ₽ за задание #{task_id} от юзера {user_id}.")
    except Exception as e:
        logging.error(f"Критическая ошибка в credit_owner_for_admin_op: {e}", exc_info=True)


async def is_flyer_check_passed_async(user_id: int):
    logging.info(f"[OP_CHECK] Запуск проверки ОП для user_id: {user_id}")
    
    # Проверяем подписку на ОП каналы из таблицы op_channels
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT channel_username FROM op_channels")
        op_channels = [row[0] for row in cursor.fetchall()]
        conn.close()
    
    not_subscribed_channels = []
    for channel_username in op_channels:
        try:
            member = bot.get_chat_member(channel_username, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                not_subscribed_channels.append(channel_username)
        except Exception as e:
            logging.warning(f"[OP_CHECK] Не удалось проверить подписку на {channel_username} для user_id {user_id}: {e}")
            not_subscribed_channels.append(channel_username)
    
    if not_subscribed_channels:
        logging.info(f"[OP_CHECK] Пользователь {user_id} не подписан на каналы: {not_subscribed_channels}")
        op_channel_tasks = [{
            'task': f'Подписаться на канал',
            'links': [f'https://t.me/{ch.replace("@", "")}' for ch in not_subscribed_channels],
            'signature': 'op_channels_subscription'
        }]
        show_task_message(user_id, op_channel_tasks)
        return False
    
    if not flyer:
        logging.info(f"[OP_CHECK] Flyer не инициализирован или отключен. Проверка Flyer заданий пропущена для user_id: {user_id}")
        return True
    
    admin_op_tasks = get_admin_op_tasks(user_id)

    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT flyer_tasks_json, flyer_tasks_timestamp, rewarded_flyer_tasks FROM users WHERE user_id = ?", (user_id,))
        user_flyer_data = cursor.fetchone()
        conn.close()

    now = datetime.now()
    
    rewarded_tasks_json = user_flyer_data[2] if user_flyer_data else '[]'
    rewarded_signatures = set(json.loads(rewarded_tasks_json or '[]'))

    flyer_tasks = []
    should_fetch_new = True
    if user_flyer_data and user_flyer_data[0] and user_flyer_data[1]:
        try:
            tasks_json, timestamp_str = user_flyer_data[0], user_flyer_data[1]
            timestamp = datetime.fromisoformat(timestamp_str)
            if now - timestamp < timedelta(minutes=10):
                should_fetch_new = False
                flyer_tasks = json.loads(tasks_json or '[]')
                logging.info(f"[OP_CHECK] Используется кэш Flyer для user_id: {user_id}. Заданий: {len(flyer_tasks)}")
        except (json.JSONDecodeError, ValueError) as e:
             logging.warning(f"[Flyer] [ID: {user_id}] Не удалось прочитать flyer_tasks_json из кэша: {e}")
    
    if should_fetch_new:
        try:
            logging.info(f"[OP_CHECK] Запрос новых заданий от Flyer API для user_id: {user_id}")
            fetched_tasks = await flyer.get_tasks(user_id=user_id, limit=5) or []
            logging.info(f"[OP_CHECK] Flyer API вернул {len(fetched_tasks)} заданий для user_id: {user_id}")
            with db_lock:
                conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET flyer_tasks_json = ?, flyer_tasks_timestamp = ? WHERE user_id = ?",
                               (json.dumps(fetched_tasks), now.isoformat(), user_id))
                conn.commit()
                conn.close()
            flyer_tasks = fetched_tasks
        except Exception as e:
            logging.error(f"[Flyer][ID: {user_id}] Ошибка при получении новых заданий: {e}")
            if user_flyer_data and user_flyer_data[0]: 
                try: flyer_tasks = json.loads(user_flyer_data[0] or '[]')
                except: flyer_tasks = []

    all_tasks_to_check = admin_op_tasks + flyer_tasks

    if not all_tasks_to_check:
        logging.info(f"[OP_CHECK] Нет заданий для проверки для user_id: {user_id}")
        return True

    failed_tasks = []
    for task in all_tasks_to_check:
        if task['signature'].startswith('admin_op_'):
            failed_tasks.append(task)
            continue
        
        try:
            status = await flyer.check_task(user_id=user_id, signature=task['signature'])
            if status in FLYER_INCOMPLETE_STATUSES:
                failed_tasks.append(task)
            else: 
                if task['signature'] not in rewarded_signatures:
                    sub_reward_str = get_creator_setting('stars_sub_reward') or "1.0"
                    reward = float(sub_reward_str)
                    credit_owner_for_task(ADMIN_ID, reward, user_id, task)
                    rewarded_signatures.add(task['signature'])
        except Exception as e:
            logging.error(f"[Flyer][ID: {user_id}] Ошибка при проверке/начислении за задание {task.get('signature')}: {e}")

    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET rewarded_flyer_tasks = ? WHERE user_id = ?", (json.dumps(list(rewarded_signatures)), user_id))
        conn.commit()
        conn.close()

    if failed_tasks:
        logging.info(f"[OP_CHECK] Найдено {len(failed_tasks)} невыполненных заданий для user_id: {user_id}")
        show_task_message(user_id, failed_tasks)
        return False
    
    logging.info(f"[OP_CHECK] Все задания выполнены для user_id: {user_id}")
    return True


def show_task_message(user_id: int, tasks):
    if not tasks: return
    try:
        markup = types.InlineKeyboardMarkup(row_width=2)
        task_buttons = [types.InlineKeyboardButton(f"➕ {t.get('task','Задание').capitalize()}", url=link) for t in tasks for link in t.get('links',[])]
        markup.add(*task_buttons); markup.add(types.InlineKeyboardButton('☑️ Проверить', callback_data='check_all_tasks'))
        bot.send_message(user_id, "<b>Для продолжения, пожалуйста, выполните спонсорские задания:</b>", reply_markup=markup, parse_mode='HTML')
    except Exception as e: logging.error(f"[show_task_message] Ошибка при показе заданий: {e}")

def require_flyer_check(func):
    @wraps(func)
    def wrapper(message_or_call, *args, **kwargs):
        is_callback = isinstance(message_or_call, types.CallbackQuery)
        user = message_or_call.from_user
        
        res = run_async_from_sync(is_flyer_check_passed_async(user.id))
        
        if res is False:
            if is_callback:
                bot.answer_callback_query(message_or_call.id, "Сначала выполните спонсорские задания!", show_alert=True)
            return
        if res is None:
            if is_callback:
                bot.answer_callback_query(message_or_call.id, "Техническая ошибка проверки. Попробуйте ещё раз.", show_alert=True)
            else:
                bot.send_message(user.id, "Техническая ошибка проверки. Попробуйте ещё раз.")
            return
        return func(message_or_call, *args, **kwargs)
    return wrapper

# =================================================================================

# =================================================================================
# --------------------------- CRYPTO PAY ИНТЕГРАЦИЯ -------------------------------
# =================================================================================

CRYPTO_PAY_API_BASE_URL = "https://pay.crypt.bot/api/"
CRYPTO_PAY_TOKEN_SETTING = "crypto_pay_api_token"
CRYPTO_PAY_AUTO_WITHDRAW_SETTING = "crypto_pay_auto_withdraw_enabled"
CRYPTO_PAY_ASSET_SETTING = "crypto_pay_asset_code"
CRYPTO_PAY_DEFAULT_ASSET = "TON"

crypto_client_lock = threading.Lock()
_cached_crypto_pay_client = None
_cached_crypto_pay_token = None


class CryptoPayError(Exception):
    """Базовое исключение для ошибок Crypto Pay API."""


class CryptoPayClient:
    """Простой клиент для работы с Crypto Pay API."""

    def __init__(self, token: str, timeout: int = 15):
        self.token = (token or "").strip()
        self.timeout = timeout
        self.session = requests.Session()

    def _request(self, method: str, payload: dict | None = None):
        if not self.token:
            raise CryptoPayError("API токен Crypto Pay не настроен.")

        url = f"{CRYPTO_PAY_API_BASE_URL}{method}"
        try:
            response = self.session.post(
                url,
                json=payload or {},
                headers={"Crypto-Pay-API-Token": self.token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CryptoPayError(f"Ошибка сети при обращении к Crypto Pay: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            text_preview = response.text[:200]
            raise CryptoPayError(f"Crypto Pay вернул некорректный ответ: {text_preview}") from exc

        if not data.get("ok"):
            raise CryptoPayError(data.get("error", "Неизвестная ошибка Crypto Pay"))

        return data.get("result")

    def create_check(self, payload: dict):
        return self._request("createCheck", payload)

    def get_check(self, check_id: int):
        return self._request("getCheck", {"check_id": check_id})

    def get_balance(self):
        return self._request("getBalance")

    def get_exchange_rates(self):
        """Получить обменные курсы валют."""
        return self._request("getExchangeRates")

    def create_invoice(self, payload: dict):
        return self._request("createInvoice", payload)


def reset_cached_crypto_client():
    global _cached_crypto_pay_client, _cached_crypto_pay_token
    with crypto_client_lock:
        _cached_crypto_pay_client = None
        _cached_crypto_pay_token = None


def normalize_crypto_amount(amount: float) -> str:
    value = f"{float(amount):.8f}"
    return value.rstrip("0").rstrip(".") if "." in value else value

# =================================================================================


# =================================================================================
# --------------------------- ОСНОВНОЙ КОД БОТА -----------------------------------
# =================================================================================

BOT_START_TIME = datetime.now()
bot = telebot.TeleBot(TOKEN)
db_lock = threading.RLock()
broadcast_lock = threading.Lock()


def get_setting_value(key: str, default=None):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
    return row[0] if row else default


def set_setting_value(key: str, value: str):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
        conn.close()


def delete_setting_value(key: str):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()
        conn.close()


def get_crypto_pay_token() -> str | None:
    token = get_setting_value(CRYPTO_PAY_TOKEN_SETTING)
    return token.strip() if token else None


def set_crypto_pay_token(token: str | None):
    if token:
        set_setting_value(CRYPTO_PAY_TOKEN_SETTING, token.strip())
    else:
        delete_setting_value(CRYPTO_PAY_TOKEN_SETTING)
    reset_cached_crypto_client()


def is_crypto_auto_withdraw_enabled() -> bool:
    return (get_setting_value(CRYPTO_PAY_AUTO_WITHDRAW_SETTING, "0") or "0") == "1"


def set_crypto_auto_withdraw_enabled(enabled: bool):
    set_setting_value(CRYPTO_PAY_AUTO_WITHDRAW_SETTING, "1" if enabled else "0")


def get_crypto_asset_code() -> str:
    asset = get_setting_value(CRYPTO_PAY_ASSET_SETTING, CRYPTO_PAY_DEFAULT_ASSET)
    return (asset or CRYPTO_PAY_DEFAULT_ASSET).upper()


def set_crypto_asset_code(asset_code: str):
    clean_code = (asset_code or "").upper().strip()
    if not clean_code:
        clean_code = CRYPTO_PAY_DEFAULT_ASSET
    set_setting_value(CRYPTO_PAY_ASSET_SETTING, clean_code)


def get_crypto_exchange_rate() -> float:
    """Получить курс обмена RUB -> Crypto (сколько RUB за 1 единицу криптовалюты)."""
    rate = get_setting_value("crypto_exchange_rate", "100.0")
    try:
        return float(rate)
    except (ValueError, TypeError):
        return 100.0  # По умолчанию 100 RUB = 1 USDT/TON


def set_crypto_exchange_rate(rate: float):
    """Установить курс обмена RUB -> Crypto."""
    if rate <= 0:
        rate = 100.0
    set_setting_value("crypto_exchange_rate", str(rate))


def convert_rub_to_crypto(rub_amount: float) -> float:
    """Конвертировать рубли в криптовалюту по текущему курсу."""
    rate = get_crypto_exchange_rate()
    return rub_amount / rate


def fetch_crypto_rate_from_api() -> dict | None:
    """Получить актуальный курс из Crypto Pay API.
    Возвращает словарь с курсами {asset: rate_in_rub} или None при ошибке.
    """
    client = get_crypto_pay_client()
    if not client:
        logging.warning("Crypto Pay клиент не настроен, невозможно получить курсы.")
        return None
    
    try:
        rates = client.get_exchange_rates()
        if not rates:
            logging.warning("API вернул пустой список курсов.")
            return None
        
        # Ищем курсы RUB
        result = {}
        for rate_obj in rates:
            source = rate_obj.get("source", "")
            target = rate_obj.get("target", "")
            rate_value = rate_obj.get("rate", "")
            is_valid = rate_obj.get("is_valid", False)
            
            # Нам нужны курсы crypto -> RUB
            if target == "RUB" and is_valid:
                try:
                    result[source] = float(rate_value)
                except (ValueError, TypeError):
                    continue
        
        return result if result else None
    except CryptoPayError as exc:
        logging.error(f"Ошибка получения курсов из Crypto Pay API: {exc}")
        return None
    except Exception as exc:
        logging.error(f"Неожиданная ошибка при получении курсов: {exc}")
        return None


def update_crypto_rate_from_api() -> bool:
    """Обновить курс обмена из Crypto Pay API.
    Возвращает True если курс успешно обновлен.
    """
    asset_code = get_crypto_asset_code()
    rates = fetch_crypto_rate_from_api()
    
    if not rates:
        return False
    
    if asset_code not in rates:
        logging.warning(f"Курс для {asset_code} не найден в API.")
        return False
    
    new_rate = rates[asset_code]
    set_crypto_exchange_rate(new_rate)
    logging.info(f"Курс обновлен из API: 1 {asset_code} = {new_rate:.2f} RUB")
    return True


def get_crypto_pay_client() -> CryptoPayClient | None:
    global _cached_crypto_pay_client, _cached_crypto_pay_token
    token = get_crypto_pay_token()
    if not token:
        return None

    with crypto_client_lock:
        if _cached_crypto_pay_client and _cached_crypto_pay_token == token:
            return _cached_crypto_pay_client
        _cached_crypto_pay_client = CryptoPayClient(token)
        _cached_crypto_pay_token = token
        return _cached_crypto_pay_client


def crypto_pay_is_configured() -> bool:
    return get_crypto_pay_client() is not None


try:
    bot_info = bot.get_me()
except Exception as e:
    logging.critical(f"Неверный токен. Ошибка: {e}"); sys.exit(1)

def init_db():
    conn = None
    try:
        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL;')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                    balance REAL DEFAULT 0, energy INTEGER DEFAULT 0, max_energy INTEGER DEFAULT 0,
                    last_energy_update TEXT, registered_at TEXT, referred_by INTEGER,
                    referral_count INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
                    last_daily_bonus_claim TEXT, click_count_since_check INTEGER DEFAULT 0,
                    flyer_tasks_json TEXT,         
                    flyer_tasks_timestamp TEXT,    
                    rewarded_flyer_tasks TEXT DEFAULT '[]'
                )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, details TEXT, status TEXT DEFAULT 'pending', created_at TEXT)''')
            cursor.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
            cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            cursor.execute('''CREATE TABLE IF NOT EXISTS op_channels (channel_username TEXT PRIMARY KEY)''')

            column_names = [info[1] for info in cursor.execute("PRAGMA table_info(users)").fetchall()]
            new_columns = {
                'click_count_since_check': 'INTEGER DEFAULT 0',
                'flyer_tasks_json': 'TEXT',
                'flyer_tasks_timestamp': 'TEXT',
                'rewarded_flyer_tasks': "TEXT DEFAULT '[]'",
                'is_banned': 'INTEGER DEFAULT 0',
                'captcha_passed': 'INTEGER DEFAULT 0',
                'captcha_attempts': 'INTEGER DEFAULT 0',
                'user_language_code': 'TEXT',
                'daily_bonus_count': 'INTEGER DEFAULT 0'
            }
            for col, col_type in new_columns.items():
                if col not in column_names:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                    logging.info(f"Колонка '{col}' добавлена в таблицу 'users'.")
            
            for admin_id in ADMINS_LIST:
                cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            
            conn.commit()
            logging.info(f"Инициализация БД прошла успешно.")

    except sqlite3.Error as e:
        logging.critical(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации БД: {e}")
        traceback.print_exc()
        if conn:
            conn.close()
        sys.exit(1)
    
    finally:
        if conn:
            conn.close()
            logging.info(f"Соединение с БД успешно закрыто.")

init_db()


# =================================================================================
# --------------------------- ФУНКЦИИ ПРОВЕРКИ ЯЗЫКА ---------------------
# =================================================================================

def is_language_check_enabled():
    return get_setting_value('language_check_enabled', '1') == '1'

def set_language_check_enabled(enabled: bool):
    set_setting_value('language_check_enabled', '1' if enabled else '0')

def is_referral_bonus_require_gift_enabled():
    return get_setting_value('referral_bonus_require_gift_enabled', '1') == '1'

def set_referral_bonus_require_gift_enabled(enabled: bool):
    set_setting_value('referral_bonus_require_gift_enabled', '1' if enabled else '0')

def get_referral_bonus_require_gift_count():
    count = get_setting_value('referral_bonus_require_gift_count', '1')
    try:
        return int(count)
    except:
        return 1

def set_referral_bonus_require_gift_count(count: int):
    set_setting_value('referral_bonus_require_gift_count', str(max(1, count)))

def is_semi_auto_withdraw_enabled():
    return get_setting_value('semi_auto_withdraw_enabled', '0') == '1'

def set_semi_auto_withdraw_enabled(enabled: bool):
    set_setting_value('semi_auto_withdraw_enabled', '1' if enabled else '0')

def check_user_language(user):
    """Проверяет язык пользователя. Возвращает True если русский или проверка отключена"""
    if not is_language_check_enabled():
        return True
    
    lang_code = user.language_code
    if not lang_code:
        # Если язык не указан, проверяем имя пользователя на кириллицу
        if user.first_name:
            has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', user.first_name))
            if has_cyrillic:
                return True
    
    # Проверяем код языка
    if lang_code and lang_code.lower().startswith('ru'):
        return True
    
    return False

def ban_user(user_id, reason=""):
    """Блокирует пользователя"""
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    logging.info(f"Пользователь {user_id} заблокирован. Причина: {reason}")

def is_user_banned(user_id):
    """Проверяет, заблокирован ли пользователь"""
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
    if result:
        return result[0] == 1
    return False

def check_banned(func):
    """Декоратор для проверки блокировки пользователя"""
    @wraps(func)
    def wrapper(message_or_call, *args, **kwargs):
        is_callback = isinstance(message_or_call, types.CallbackQuery)
        user = message_or_call.from_user
        
        if is_user_banned(user.id):
            if is_callback:
                try:
                    bot.answer_callback_query(message_or_call.id, "❌ Вы заблокированы. Доступ запрещен.", show_alert=True)
                except:
                    pass
            else:
                try:
                    bot.send_message(user.id, "❌ <b>Вы заблокированы.</b>\n\nДоступ к боту запрещен.", parse_mode='HTML')
                except:
                    pass
            return
        return func(message_or_call, *args, **kwargs)
    return wrapper

# =================================================================================

def is_admin(user_id): return user_id in ADMINS_LIST
def get_main_menu_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("💎 Кликер"), types.KeyboardButton("👤 Личный кабинет"))
    markup.add(types.KeyboardButton("🎁 Подарок"), types.KeyboardButton("👥 Рефералы"))
    markup.add(types.KeyboardButton("ℹ️ О боте"))
    if is_admin(user_id): markup.add(types.KeyboardButton("👑 Админ-панель"))
    return markup
def get_admin_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("📣 Рассылка"), types.KeyboardButton("📊 Статистика"))
    markup.add(types.KeyboardButton("📬 Заявки на вывод"), types.KeyboardButton("🚫 Бан/Разбан"))
    markup.add(types.KeyboardButton("📜 Рефералы юзера"), types.KeyboardButton("💬 Написать юзеру"))
    markup.add(types.KeyboardButton("💳 Crypto Pay"), types.KeyboardButton("⚙️ ОП Каналы"))
    markup.add(types.KeyboardButton("🔒 Список заблокированных"), types.KeyboardButton("⚙️ Настройки"))
    markup.add(types.KeyboardButton("◀️ Главное меню"))
    return markup
def get_cancel_keyboard(): return types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add("❌ Отмена")

def find_user_by_id_or_username(identifier):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        try:
            if identifier.isdigit():
                cursor.execute('SELECT user_id, first_name, username FROM users WHERE user_id = ?', (int(identifier),)); user_data = cursor.fetchone()
            else:
                cursor.execute('SELECT user_id, first_name, username FROM users WHERE username = ? COLLATE NOCASE', (identifier.replace('@', ''),)); user_data = cursor.fetchone()
        except Exception as e: logging.error(f"Ошибка поиска: {e}"); user_data = None
        finally: conn.close()
    return user_data

def update_and_get_energy(user_id):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT energy, max_energy, last_energy_update FROM users WHERE user_id = ?", (user_id,)); res = cursor.fetchone()
        if not res: conn.close(); return 0
        current_energy, max_energy, last_update_str = res
        
        if current_energy >= max_energy:
             if user_id in user_recharge_state: user_recharge_state.pop(user_id, None)
             conn.close(); return max_energy

        if last_update_str:
            last_update = datetime.fromisoformat(last_update_str)
            seconds_passed = (datetime.now() - last_update).total_seconds()
            energy_to_add = int(seconds_passed * ENERGY_REGEN_RATE_PER_SEC)
            
            if energy_to_add > 0:
                new_energy = min(max_energy, current_energy + energy_to_add)
                new_last_update = datetime.now().isoformat()
                cursor.execute("UPDATE users SET energy = ?, last_energy_update = ? WHERE user_id = ?", (new_energy, new_last_update, user_id))
                conn.commit()
                current_energy = new_energy
                if current_energy >= max_energy:
                     if user_id in user_recharge_state: user_recharge_state.pop(user_id, None)

        conn.close(); return current_energy

@bot.message_handler(commands=['start'])
def start_handler(message):
    user = message.from_user
    
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT is_banned, user_language_code FROM users WHERE user_id = ?", (user.id,))
        user_data = cursor.fetchone()
        
        if user_data and user_data[0] == 1:  # is_banned
            conn.close(); bot.send_message(user.id, "<b>Вам запрещено пользоваться ботом.</b>", parse_mode='HTML'); return
        
        # Сохраняем язык пользователя
        if user.language_code:
            cursor.execute("UPDATE users SET user_language_code = ? WHERE user_id = ?", (user.language_code, user.id))
        
        is_new_user = False
        if not user_data:
            is_new_user = True
            referrer_id = None
            if match := re.search(r'start ref(\d+)', message.text):
                potential_referrer_id = int(match.group(1))
                if potential_referrer_id != user.id:
                    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (potential_referrer_id,)); 
                    if cursor.fetchone(): referrer_id = potential_referrer_id
            initial_balance = WELCOME_BONUS + (REFERRAL_BONUS_NEW_USER if referrer_id else 0); now_iso = datetime.now().isoformat()
            cursor.execute("INSERT INTO users (user_id, username, first_name, balance, energy, max_energy, last_energy_update, registered_at, referred_by, user_language_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user.id, user.username, user.first_name, initial_balance, ENERGY_MAX, ENERGY_MAX, now_iso, now_iso, referrer_id, user.language_code))
            # Реферальный бонус НЕ начисляется сразу, только после получения подарка
            conn.commit()
        conn.close()
    
    if not run_async_from_sync(is_flyer_check_passed_async(user.id)):
        return 

    welcome_message = config.get('welcome_message', '👋 Добро пожаловать!')
    if SHOW_BRANDING: welcome_message += f"\n\nБот создан с помощью @{CONSTRUCTOR_BOT_USERNAME}"
    bot.send_message(user.id, welcome_message, reply_markup=get_main_menu_keyboard(user.id), parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data == 'check_all_tasks')
@check_banned
def handle_check_tasks_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id, text="Проверяю...")

    admin_op_tasks_to_credit = get_admin_op_tasks(user_id)
    if admin_op_tasks_to_credit:
        logging.info(f"[CALLBACK_CHECK] {user_id} нажал проверку. Начисляю награды за {len(admin_op_tasks_to_credit)} заданий 'Мои ОП'.")
        for task in admin_op_tasks_to_credit:
            task_id_str = task['signature'].replace('admin_op_', '')
            if task_id_str.isdigit():
                credit_owner_for_admin_op(ADMIN_ID, user_id, int(task_id_str), task['reward'])
    
    if run_async_from_sync(is_flyer_check_passed_async(user_id)):
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        bot.send_message(user_id, "🎉 <b>Спасибо! Доступ открыт.</b>", reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML')


@bot.message_handler(func=lambda message: message.text == "💎 Кликер")
@check_banned
@require_flyer_check
def clicker_menu_handler(message):
    user_id = message.from_user.id; current_energy = update_and_get_energy(user_id)
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)); result = cursor.fetchone(); conn.close()
    if not result: return
    balance = result[0];
    text = f"💰 Баланс: <b>{balance:.4f} ₽</b>\n⚡️ Энергия: <b>{current_energy}/{ENERGY_MAX}</b>"
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("👆 Клик!", callback_data="do_click"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
@bot.callback_query_handler(func=lambda call: call.data == 'do_click')
@check_banned
def do_click_callback(call):
    user_id = call.from_user.id
    current_energy = update_and_get_energy(user_id)

    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT click_count_since_check, balance FROM users WHERE user_id = ?", (user_id,)); res = cursor.fetchone()
        if not res: conn.close(); return
        click_count, current_balance = res

        if current_energy < ENERGY_PER_CLICK:
            bot.answer_callback_query(call.id, "Нет энергии! ⚡️", show_alert=False)
            conn.close(); return
            
        if click_count >= random.randint(FLYER_CHECK_INTERVAL_MIN, FLYER_CHECK_INTERVAL_MAX):
            cursor.execute("UPDATE users SET click_count_since_check = 0 WHERE user_id = ?", (user_id,)); conn.commit(); conn.close()
            if not run_async_from_sync(is_flyer_check_passed_async(user_id)):
                bot.answer_callback_query(call.id, "Пожалуйста, выполните задания.", show_alert=True); return
            with db_lock: conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        else:
            cursor.execute("UPDATE users SET click_count_since_check = click_count_since_check + 1 WHERE user_id = ?", (user_id,))
            
        reward = random.uniform(CLICK_REWARD_MIN, CLICK_REWARD_MAX)
        new_energy = current_energy - ENERGY_PER_CLICK
        new_balance = current_balance + reward
        
        cursor.execute("UPDATE users SET energy = ?, balance = ? WHERE user_id = ?", (new_energy, new_balance, user_id))
        conn.commit(); conn.close()
        
    bot.answer_callback_query(call.id, f"+{reward:.4f} ₽", show_alert=False)
    try:
        updated_text = f"💰 Баланс: <b>{new_balance:.4f} ₽</b>\n⚡️ Энергия: <b>{new_energy}/{ENERGY_MAX}</b>"
        bot.edit_message_text(updated_text, call.message.chat.id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode="HTML")
    except telebot.apihelper.ApiTelegramException as e:
        if 'message is not modified' not in str(e): logging.warning(f"Ошибка автообновления: {e}")
    except Exception as e:
        logging.error(f"Необработанная ошибка автообновления: {e}")
@bot.message_handler(func=lambda message: message.text == "👤 Личный кабинет")
@check_banned
@require_flyer_check
def profile_handler(message):
    user_id = message.from_user.id; current_energy = update_and_get_energy(user_id)
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT balance, referral_count FROM users WHERE user_id = ?", (user_id,)); res = cursor.fetchone(); conn.close()
    if not res: return
    balance, ref_count = res
    text = (f"👤 <b>Ваш Профиль</b>\n\n"
            f"▫️ <b>ID:</b> <code>{user_id}</code>\n"
            f"▫️ <b>Баланс:</b> {balance:.4f} ₽\n"
            f"▫️ <b>Энергия:</b> {current_energy}/{ENERGY_MAX} ⚡️\n\n"
            f"🤝 Приглашено друзей: <b>{ref_count}</b>")
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📤 Вывести", callback_data="withdraw_start"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
@bot.message_handler(func=lambda message: message.text == "🎁 Подарок")
@check_banned
@require_flyer_check
def daily_bonus_handler(message):
    user_id = message.from_user.id
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT last_daily_bonus_claim, daily_bonus_count, referred_by FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if not result:
            conn.close()
            return
        last_claim_str, bonus_count, referrer_id = result
        
        if last_claim_str:
            try:
                if datetime.now() < datetime.fromisoformat(last_claim_str) + timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS):
                    time_left = (datetime.fromisoformat(last_claim_str) + timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS)) - datetime.now()
                    hours, rem = divmod(int(time_left.total_seconds()), 3600); minutes, _ = divmod(rem, 60)
                    bot.send_message(user_id, f"⏳ <b>Подождите.</b> Следующий подарок через <b>{hours} ч. {minutes} мин.</b>", parse_mode='HTML')
                    conn.close(); return
            except: pass
        
        # Увеличиваем счетчик подарков
        new_bonus_count = (bonus_count or 0) + 1
        cursor.execute("UPDATE users SET balance = balance + ?, last_daily_bonus_claim = ?, daily_bonus_count = ? WHERE user_id = ?", 
                      (DAILY_BONUS_REWARD, datetime.now().isoformat(), new_bonus_count, user_id))
        
        # Проверяем, нужно ли начислить реферальный бонус
        if referrer_id and is_referral_bonus_require_gift_enabled():
            required_count = get_referral_bonus_require_gift_count()
            # Проверяем, был ли уже начислен реферальный бонус
            cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referrer_id,))
            ref_data = cursor.fetchone()
            if ref_data and ref_data[0] == 0 and new_bonus_count >= required_count:
                # Начисляем реферальный бонус рефереру
                if REFERRAL_BONUS_REFERRER > 0:
                    cursor.execute("UPDATE users SET balance = balance + ?, referral_count = referral_count + 1 WHERE user_id = ?", 
                                  (REFERRAL_BONUS_REFERRER, referrer_id))
                    try: 
                        bot.send_message(referrer_id, f"🎉 Ваш реферал получил подарок! Вам начислено <b>{REFERRAL_BONUS_REFERRER} ₽</b>.", parse_mode='HTML')
                    except Exception as e: 
                        logging.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")
        elif referrer_id and not is_referral_bonus_require_gift_enabled():
            # Старая система - начисляем сразу при регистрации (если еще не начислено)
            cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referrer_id,))
            ref_data = cursor.fetchone()
            if ref_data and ref_data[0] == 0 and REFERRAL_BONUS_REFERRER > 0:
                cursor.execute("UPDATE users SET balance = balance + ?, referral_count = referral_count + 1 WHERE user_id = ?", 
                              (REFERRAL_BONUS_REFERRER, referrer_id))
                try: 
                    bot.send_message(referrer_id, f"🎉 Ваш реферал присоединился! Вам начислено <b>{REFERRAL_BONUS_REFERRER} ₽</b>.", parse_mode='HTML')
                except Exception as e: 
                    logging.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")
        
        conn.commit(); conn.close()
    bot.send_message(user_id, f"🎉 <b>Поздравляем!</b> Вы получили: <b>+{DAILY_BONUS_REWARD} ₽</b>.\nСледующий подарок через {DAILY_BONUS_COOLDOWN_HOURS} часов.", parse_mode='HTML')
@bot.message_handler(func=lambda message: message.text == "👥 Рефералы")
@check_banned
@require_flyer_check
def show_referrals(message):
    user_id = message.from_user.id
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (user_id,)); ref_count = (cursor.fetchone() or [0])[0]; conn.close()
    total_earned = ref_count * REFERRAL_BONUS_REFERRER
    text = (f"👥 <b>Реферальная программа</b>\n\n"
            f"Приглашайте друзей и получайте рубли!\n\n"
            f"▫️ Вы получаете: <b>{REFERRAL_BONUS_REFERRER} ₽</b> за каждого друга.\n"
            f"▫️ Ваш друг получает: <b>{REFERRAL_BONUS_NEW_USER} ₽</b> при старте.\n\n"
            f"📈 <b>Ваша статистика:</b>\n"
            f"  - Приглашено друзей: <b>{ref_count} чел.</b>\n"
            f"  - Заработано: <b>{total_earned:.4f} ₽</b>\n\n"
            f"🔗 <b>Ваша ссылка для приглашений:</b>\n"
            f"<code>https://t.me/{bot_info.username}?start=ref{user_id}</code>")
    bot.send_message(message.chat.id, text, parse_mode='HTML')
@bot.inline_handler(lambda query: query.query.startswith('ref'))
def show_ref_link_inline(query):
    user_id = query.from_user.id; ref_link = f"https://t.me/{bot_info.username}?start=ref{user_id}"
    result = types.InlineQueryResultArticle('1', 'Ваша реферальная ссылка', types.InputTextMessageContent(f"🔥 Присоединяйся и зарабатывай вместе со мной!\n\nТвоя ссылка: {ref_link}"), description=ref_link)
    bot.answer_inline_query(query.id, [result], cache_time=1)
def format_timedelta(td):
    days = td.days; hours, rem = divmod(td.seconds, 3600); minutes, _ = divmod(rem, 60)
    return f"{days}д {hours:02}:{minutes:02}"
@bot.message_handler(func=lambda message: message.text == "ℹ️ О боте")
@check_banned
@require_flyer_check
def about_bot_handler(message):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        total_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        last_24h_iso = (datetime.now() - timedelta(hours=24)).isoformat()
        new_users_24h = cursor.execute("SELECT COUNT(*) FROM users WHERE registered_at >= ?", (last_24h_iso,)).fetchone()[0]
        total_paid_out = cursor.execute("SELECT SUM(amount) FROM withdrawals WHERE status = 'approved'").fetchone()[0] or 0
        conn.close()
    uptime = datetime.now() - BOT_START_TIME
    text = (f"📊 <b>Статистика бота</b>\n\n"
            f"⏱️ <b>Аптайм:</b> <code>{format_timedelta(uptime)}</code>\n"
            f"👥 <b>Всего пользователей:</b> {total_users}\n"
            f"🆕 <b>Новых за 24ч:</b> {new_users_24h}\n"
            f"💰 <b>Всего выплачено:</b> {total_paid_out:.2f} ₽\n\n"
            f"<i>Нажимай на кнопку 'Кликер' и начни зарабатывать!</i>")
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton("🔥 Администратор", url=f"tg://user?id={ADMIN_ID}")]
    if SUPPORT_CHAT: buttons.append(types.InlineKeyboardButton("💬 Чат", url=SUPPORT_CHAT))
    if PAYMENTS_CHANNEL: buttons.append(types.InlineKeyboardButton("💰 Выплаты", url=f"https://t.me/{PAYMENTS_CHANNEL.replace('@','')}"))
    for i in range(0, len(buttons), 2): markup.row(*buttons[i:i+2])
    if SHOW_BRANDING: markup.add(types.InlineKeyboardButton("Хочу такого же бота (free)", url=f"https://t.me/{CONSTRUCTOR_BOT_USERNAME}"))
    bot.send_message(message.chat.id, text, reply_markup=markup, disable_web_page_preview=True, parse_mode='HTML')
@bot.callback_query_handler(func=lambda call: call.data == 'withdraw_start')
@check_banned
@require_flyer_check
def withdraw_start_callback(call):
    user_id = call.from_user.id
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)); balance = cursor.fetchone()[0]; conn.close()
    if balance < WITHDRAWAL_MIN: bot.answer_callback_query(call.id, f"Минимальная сумма для вывода: {WITHDRAWAL_MIN} ₽", show_alert=True); return
    bot.answer_callback_query(call.id)
    auto_enabled = is_crypto_auto_withdraw_enabled() and crypto_pay_is_configured()
    if auto_enabled:
        asset_code = get_crypto_asset_code()
        exchange_rate = get_crypto_exchange_rate()
        text = (f"📤 <b>Автоматический вывод</b>\n\n"
                f"Ваш баланс: <b>{balance:.4f} ₽</b>\n"
                f"Минимальная сумма: <b>{WITHDRAWAL_MIN} ₽</b>\n"
                f"Актив выплат: <code>{asset_code}</code>\n"
                f"Курс: <b>1 {asset_code} = {exchange_rate:.2f} ₽</b>\n\n"
                f"Введите сумму вывода В РУБЛЯХ.\n"
                f"Чек Crypto Pay будет создан и отправлен автоматически.")
    else:
        text = (f"📤 <b>Вывод средств</b>\n\n"
                f"Ваш баланс: <b>{balance:.4f} ₽</b>\n"
                f"Минимальная сумма: <b>{WITHDRAWAL_MIN} ₽</b>\n\n"
                f"Введите сумму и реквизиты ({WITHDRAWAL_METHOD_TEXT}) в формате:\n<code>СУММА | РЕКВИЗИТЫ</code>")
    msg = bot.send_message(call.message.chat.id, text, reply_markup=get_cancel_keyboard(), parse_mode='HTML')
    bot.register_next_step_handler(msg, process_withdrawal_details, auto_enabled)
def process_withdrawal_details(message, auto_withdraw_enabled=False):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        try:
            bot.send_message(user_id, "❌ <b>Вы заблокированы.</b>\n\nДоступ к боту запрещен.", parse_mode='HTML')
        except:
            pass
        return
    if not run_async_from_sync(is_flyer_check_passed_async(message.from_user.id)):
        return
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_main_menu_keyboard(user_id))
        return

    use_auto_withdraw = auto_withdraw_enabled and crypto_pay_is_configured()

    if auto_withdraw_enabled and not use_auto_withdraw:
        text = (f"⚠️ <b>Автовывод временно недоступен.</b>\n\n"
                f"Введите сумму и реквизиты ({WITHDRAWAL_METHOD_TEXT}) в формате:\n<code>СУММА | РЕКВИЗИТЫ</code>")
        msg = bot.send_message(message.chat.id, text, reply_markup=get_cancel_keyboard(), parse_mode='HTML')
        bot.register_next_step_handler(msg, process_withdrawal_details, False)
        return

    if use_auto_withdraw:
        amount_text = (message.text or "").replace(",", ".").strip()
        try:
            amount = float(amount_text)
        except ValueError:
            msg = bot.send_message(message.chat.id, "❌ <b>Неверный формат суммы.</b> Попробуйте снова.", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
            bot.register_next_step_handler(msg, process_withdrawal_details, auto_withdraw_enabled)
            return
        details = None
    else:
        try:
            amount_str, details = map(str.strip, message.text.split('|', 1))
            amount = float(amount_str.replace(",", "."))
        except (ValueError, IndexError):
            msg = bot.send_message(message.chat.id, "❌ <b>Неверный формат.</b> Попробуйте снова.", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
            bot.register_next_step_handler(msg, process_withdrawal_details, auto_withdraw_enabled)
            return

    if amount <= 0:
        msg = bot.send_message(message.chat.id, "❌ Сумма должна быть больше нуля. Попробуйте снова.", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
        bot.register_next_step_handler(msg, process_withdrawal_details, auto_withdraw_enabled)
        return
    if amount < WITHDRAWAL_MIN:
        msg = bot.send_message(message.chat.id, f"❌ Минимальная сумма для вывода: <b>{WITHDRAWAL_MIN}</b>.", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
        bot.register_next_step_handler(msg, process_withdrawal_details, auto_withdraw_enabled)
        return

    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            bot.send_message(user_id, "❌ Пользователь не найден.", reply_markup=get_main_menu_keyboard(user_id))
            return
        balance = row[0]
        if balance < amount:
            conn.close()
            bot.send_message(user_id, "❌ На балансе недостаточно средств.", reply_markup=get_main_menu_keyboard(user_id))
            return
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
    new_balance = balance - amount

    # Проверяем полуавтоматический режим
    if is_semi_auto_withdraw_enabled() and crypto_pay_is_configured():
        # Полуавтоматический режим - создаем заявку для админа
        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO withdrawals (user_id, amount, details, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, amount, f"SEMI_AUTO_{user_id}", "pending_semi_auto", datetime.now().isoformat())
            )
            withdrawal_id = cursor.lastrowid
            conn.commit()
            conn.close()
        
        bot.send_message(user_id, "✅ Ваша заявка на вывод принята! Ожидайте одобрения администратора.", reply_markup=get_main_menu_keyboard(user_id))
        
        sender_info = message.from_user
        asset_code = get_crypto_asset_code()
        admin_text = (
            f"📬 <b>Новая заявка на вывод №{withdrawal_id}</b>\n\n"
            f"👤 <b>Пользователь:</b> {escape(sender_info.first_name or 'N/A')} (@{escape(sender_info.username or 'N/A')}, <code>{sender_info.id}</code>)\n"
            f"💰 <b>Сумма:</b> {amount:.4f} ₽\n"
            f"💎 <b>Актив:</b> <code>{asset_code}</code>"
        )
        admin_markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"semi_wd_approve_{withdrawal_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"semi_wd_decline_{withdrawal_id}")
        )
        for admin_user_id in ADMINS_LIST:
            try:
                bot.send_message(admin_user_id, admin_text, reply_markup=admin_markup, parse_mode='HTML')
            except Exception as e:
                logging.error(f"Не удалось отправить заявку админу {admin_user_id}: {e}")
        return

    if use_auto_withdraw:
        client = get_crypto_pay_client()
        if client:
            asset_code = get_crypto_asset_code()
            # Конвертируем рубли в криптовалюту
            crypto_amount = convert_rub_to_crypto(amount)
            amount_formatted = normalize_crypto_amount(crypto_amount)
            try:
                check_payload = {
                    "asset": asset_code,
                    "amount": amount_formatted,
                    "pin_to_user_id": user_id,
                }
                check = client.create_check(check_payload)
                check_id = check.get("check_id")
                check_url = check.get("bot_check_url") or check.get("check_url")
                if check_id and not check_url:
                    try:
                        check_info = client.get_check(check_id)
                        if isinstance(check_info, dict):
                            check_url = check_info.get("bot_check_url") or check_info.get("check_url")
                    except CryptoPayError as info_exc:
                        logging.warning(f"[BotID:{BOT_ID}] Не удалось получить данные чека {check_id}: {info_exc}")

                details_value = f"CryptoPay чек #{check_id} ({asset_code})"
                if check_url:
                    details_value += f" | {check_url}"

                with db_lock:
                    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO withdrawals (user_id, amount, details, status, created_at) VALUES (?, ?, ?, ?, ?)",
                        (user_id, amount, details_value, "auto_completed", datetime.now().isoformat())
                    )
                    conn.commit()
                    conn.close()

                if check_url:
                    link_line = f'🔗 <a href="{check_url}">Получить чек в @CryptoBot</a>'
                else:
                    link_line = "🔗 Чек доступен в разделе <code>My Checks</code> в @CryptoBot."

                success_text = (
                    "✅ <b>Автоматический вывод выполнен!</b>\n\n"
                    f"💰 Списано с баланса: <b>{amount:.4f} ₽</b>\n"
                    f"💎 Сумма чека: <b>{amount_formatted} {asset_code}</b>\n"
                    f"{link_line}\n\n"
                    f"📉 Новый баланс: <b>{new_balance:.4f} ₽</b>"
                )
                bot.send_message(user_id, success_text, reply_markup=get_main_menu_keyboard(user_id), parse_mode='HTML', disable_web_page_preview=True)

                sender_info = message.from_user
                admin_text = (
                    "<b>🤖 Автовывод выполнен</b>\n\n"
                    f"👤 Пользователь: {escape(sender_info.first_name or 'N/A')} (@{escape(sender_info.username or 'N/A')} | <code>{sender_info.id}</code>)\n"
                    f"💰 Списано: <b>{amount:.4f} ₽</b>\n"
                    f"💎 Чек: <b>{amount_formatted} {asset_code}</b>\n"
                    f"🔗 Ссылка: {check_url or 'недоступен'}"
                )
                for admin_user_id in ADMINS_LIST:
                    try:
                        bot.send_message(admin_user_id, admin_text, parse_mode='HTML', disable_web_page_preview=True)
                    except Exception as e:
                        logging.error(f"[BotID:{BOT_ID}] Не удалось отправить уведомление админу {admin_user_id}: {e}")
                
                # Публикация в канал выплат для автовывода
                try:
                    if PAYMENTS_CHANNEL:
                        username_display = f"@{escape(sender_info.username)}" if sender_info.username else "нет"
                        channel_text = (
                            "✅ <b>Новая автовыплата!</b>\n\n"
                            f"💰 <b>Сумма:</b> {amount:.2f} ₽\n"
                            f"👤 <b>Пользователь:</b> {username_display} (ID: <code>{sender_info.id}</code>)"
                        )
                        bot.send_message(PAYMENTS_CHANNEL, channel_text, parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[BotID:{BOT_ID}] Не удалось отправить в канал выплат: {e}")
                return
            except CryptoPayError as exc:
                logging.error(f"[BotID:{BOT_ID}] Ошибка создания чека Crypto Pay: {exc}")
                with db_lock:
                    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                    cursor.execute(
                        "INSERT INTO withdrawals (user_id, amount, details, status, created_at) VALUES (?, ?, ?, ?, ?)",
                        (user_id, amount, f"Auto withdraw failed: {str(exc)[:150]}", "auto_failed", datetime.now().isoformat())
                    )
                    conn.commit()
                    conn.close()
                bot.send_message(
                    user_id,
                    "❌ <b>Автоматический вывод временно недоступен.</b>\n"
                    "Средства возвращены на баланс. Попробуйте позже или обратитесь в поддержку.",
                    reply_markup=get_main_menu_keyboard(user_id),
                    parse_mode='HTML'
                )
                sender_info = message.from_user
                admin_alert = (
                    "<b>⚠️ Автовывод не выполнен</b>\n\n"
                    f"👤 Пользователь: {escape(sender_info.first_name or 'N/A')} (@{escape(sender_info.username or 'N/A')} | <code>{sender_info.id}</code>)\n"
                    f"💰 Сумма: <b>{amount:.4f} ₽</b> (чек на <b>{amount_formatted} {asset_code}</b>)\n"
                    f"Причина: {escape(str(exc))}\n"
                    "Проверьте баланс резерва Crypto Pay."
                )
                for admin_user_id in ADMINS_LIST:
                    try:
                        bot.send_message(admin_user_id, admin_alert, parse_mode='HTML')
                    except Exception as e:
                        logging.error(f"[BotID:{BOT_ID}] Не удалось уведомить админа {admin_user_id} об ошибке автовывода: {e}")
                return

    # Обычная заявка на вывод (ручная обработка)
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO withdrawals (user_id, amount, details, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, details or "", "pending", datetime.now().isoformat())
        )
        withdrawal_id = cursor.lastrowid
        conn.commit()
        conn.close()

    bot.send_message(user_id, "✅ Ваша заявка на вывод принята!", reply_markup=get_main_menu_keyboard(user_id))

    sender_info = message.from_user
    admin_text = (
        f"📬 <b>Новая заявка на вывод №{withdrawal_id}</b>\n\n"
        f"👤 <b>Пользователь:</b> {escape(sender_info.first_name or 'N/A')} (@{escape(sender_info.username or 'N/A')}, <code>{sender_info.id}</code>)\n"
        f"💰 <b>Сумма:</b> {amount:.4f} ₽\n"
        f"💳 <b>Реквизиты:</b> <code>{escape((details or 'не указано'))}</code>"
    )
    admin_markup = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"wd_approve_{withdrawal_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"wd_decline_{withdrawal_id}")
    )
    for admin_user_id in ADMINS_LIST:
        try:
            bot.send_message(admin_user_id, admin_text, reply_markup=admin_markup, parse_mode='HTML')
        except Exception as e:
            logging.error(f"Не удалось отправить заявку админу {admin_user_id}: {e}")
@bot.message_handler(func=lambda message: message.text in ["👑 Админ-панель", "◀️ Главное меню"] and is_admin(message.from_user.id))
def admin_menu_nav(message):
    if message.text == "👑 Админ-панель": bot.send_message(message.chat.id, "<b>Админ-панель</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML')
    else: bot.send_message(message.chat.id, "<b>Главное меню.</b>", reply_markup=get_main_menu_keyboard(message.from_user.id), parse_mode='HTML')
def build_crypto_pay_menu_text():
    token_status = "установлен" if get_crypto_pay_token() else "не указан"
    auto_status = "включен" if is_crypto_auto_withdraw_enabled() else "выключен"
    asset_code = get_crypto_asset_code()
    exchange_rate = get_crypto_exchange_rate()
    lines = [
        "<b>💳 Управление Crypto Pay</b>",
        "",
        f"🔑 Токен: <b>{token_status}</b>",
        f"⚙️ Автовывод: <b>{auto_status}</b>",
        f"💱 Актив выплат: <code>{asset_code}</code>",
        f"📊 Курс: <b>1 {asset_code} = {exchange_rate:.2f} ₽</b>",
        "",
        "Настройте автоматический вывод через чеки Crypto Pay и контролируйте резерв."
    ]
    return "\n".join(lines)
def get_crypto_pay_inline_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🔑 Установить токен", callback_data="crypto_set_token"))
    markup.add(types.InlineKeyboardButton("⚡ Переключить автовывод", callback_data="crypto_toggle_auto"))
    markup.add(types.InlineKeyboardButton("💱 Изменить актив", callback_data="crypto_change_asset"))
    markup.add(types.InlineKeyboardButton("🔄 Обновить курс из API", callback_data="crypto_update_rate"))
    markup.add(types.InlineKeyboardButton("📊 Изменить курс вручную", callback_data="crypto_change_rate"))
    markup.add(types.InlineKeyboardButton("💰 Пополнить резерв", callback_data="crypto_deposit"))
    markup.add(types.InlineKeyboardButton("📈 Показать баланс", callback_data="crypto_balance"))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_back"))
    return markup
def send_crypto_pay_admin_menu(chat_id, message_id=None, edit=False):
    text = build_crypto_pay_menu_text()
    markup = get_crypto_pay_inline_markup()
    if edit and message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='HTML')
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
@bot.message_handler(func=lambda message: message.text == "💳 Crypto Pay" and is_admin(message.from_user.id))
def open_crypto_pay_menu(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Открыл меню Crypto Pay")
    send_crypto_pay_admin_menu(message.chat.id)
@bot.callback_query_handler(func=lambda call: call.data.startswith("crypto_") and is_admin(call.from_user.id))
def handle_crypto_pay_callbacks(call):
    action = call.data
    logging.info(f"[ADMIN] [{call.from_user.id}] Crypto Pay действие: {action}")
    if action == "crypto_set_token":
        logging.info(f"[ADMIN] [{call.from_user.id}] Начал настройку Crypto Pay токена")
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.from_user.id,
            "Отправьте новый <b>Crypto Pay API токен</b>.\n\n"
            "Он начинается с букв <code>KEY:</code>. Если хотите удалить токен, отправьте слово <code>удалить</code>.",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        bot.register_next_step_handler(msg, process_crypto_pay_token_input, call.message.chat.id, call.message.message_id)
        return
    if action == "crypto_toggle_auto":
        if not get_crypto_pay_token():
            logging.warning(f"[ADMIN] [{call.from_user.id}] Попытка включить автовывод без токена")
            bot.answer_callback_query(call.id, "Сначала укажите токен Crypto Pay.", show_alert=True)
            return
        new_state = not is_crypto_auto_withdraw_enabled()
        set_crypto_auto_withdraw_enabled(new_state)
        logging.info(f"[ADMIN] [{call.from_user.id}] Автовывод Crypto Pay {'включен' if new_state else 'выключен'}")
        bot.answer_callback_query(call.id, f"Автовывод {'включен' if new_state else 'выключен'}.")
        send_crypto_pay_admin_menu(call.message.chat.id, call.message.message_id, edit=True)
        return
    if action == "crypto_change_asset":
        bot.answer_callback_query(call.id)
        current_asset = get_crypto_asset_code()
        msg = bot.send_message(
            call.from_user.id,
            f"Введите код актива Crypto Pay (например, TON, USDT, BTC).\n\nТекущий: <code>{current_asset}</code>",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        bot.register_next_step_handler(msg, process_crypto_pay_asset_input, call.message.chat.id, call.message.message_id)
        return
    if action == "crypto_update_rate":
        if not crypto_pay_is_configured():
            bot.answer_callback_query(call.id, "Сначала настройте токен Crypto Pay.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Получаю курс из API...")
        if update_crypto_rate_from_api():
            send_crypto_pay_admin_menu(call.message.chat.id, call.message.message_id, edit=True)
            asset_code = get_crypto_asset_code()
            rate = get_crypto_exchange_rate()
            bot.send_message(call.from_user.id, f"✅ Курс успешно обновлен из API!\n\n<b>1 {asset_code} = {rate:.2f} ₽</b>", parse_mode='HTML')
        else:
            bot.send_message(call.from_user.id, "❌ Не удалось получить курс из API. Проверьте настройки токена или установите курс вручную.")
        return
    if action == "crypto_change_rate":
        bot.answer_callback_query(call.id)
        current_rate = get_crypto_exchange_rate()
        current_asset = get_crypto_asset_code()
        msg = bot.send_message(
            call.from_user.id,
            f"Введите курс обмена (сколько рублей за 1 {current_asset}).\n\n"
            f"Текущий курс: <b>1 {current_asset} = {current_rate:.2f} ₽</b>\n\n"
            f"Пример: <code>100</code> (означает 1 {current_asset} = 100 ₽)",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        bot.register_next_step_handler(msg, process_crypto_pay_rate_input, call.message.chat.id, call.message.message_id)
        return
    if action == "crypto_balance":
        client = get_crypto_pay_client()
        if not client:
            bot.answer_callback_query(call.id, "Crypto Pay не настроен. Укажите токен.", show_alert=True)
            return
        try:
            balances = client.get_balance() or []
        except CryptoPayError as exc:
            bot.answer_callback_query(call.id, f"Ошибка получения баланса: {exc}", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        asset_code = get_crypto_asset_code()
        lines = ["<b>📊 Балансы Crypto Pay</b>", ""]
        asset_found = False
        for item in balances:
            code = item.get("currency_code") or item.get("asset")
            available = item.get("available")
            if not code:
                continue
            lines.append(f"{code}: <b>{available}</b>")
            if code.upper() == asset_code.upper():
                asset_found = True
        if not asset_found:
            lines.append("")
            lines.append(f"⚠️ Баланс для выбранного актива <code>{asset_code}</code> не найден.")
        bot.send_message(call.from_user.id, "\n".join(lines), parse_mode='HTML')
        return
    if action == "crypto_deposit":
        if not crypto_pay_is_configured():
            bot.answer_callback_query(call.id, "Сначала настройте токен Crypto Pay.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        asset = get_crypto_asset_code()
        msg = bot.send_message(
            call.from_user.id,
            f"Введите сумму пополнения резерва в <b>{asset}</b>.\nПример: <code>10.5</code>",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        bot.register_next_step_handler(msg, process_crypto_pay_deposit_amount, asset, call.message.chat.id, call.message.message_id)
        return
    bot.answer_callback_query(call.id, "Команда не поддерживается.", show_alert=True)
def process_crypto_pay_token_input(message, menu_chat_id, menu_message_id):
    if message.text == "❌ Отмена":
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил настройку Crypto Pay токена")
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_admin_keyboard())
        send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
        return
    value = (message.text or "").strip()
    if value.lower() in ("удалить", "delete", "remove"):
        logging.info(f"[ADMIN] [{message.from_user.id}] Удалил Crypto Pay токен")
        set_crypto_pay_token(None)
        bot.send_message(message.chat.id, "🗑️ Токен Crypto Pay удалён.", reply_markup=get_admin_keyboard())
    elif not value:
        logging.warning(f"[ADMIN] [{message.from_user.id}] Попытка установить пустой Crypto Pay токен")
        msg = bot.send_message(message.chat.id, "❌ Токен не может быть пустым. Попробуйте снова.", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_crypto_pay_token_input, menu_chat_id, menu_message_id)
        return
    else:
        logging.info(f"[ADMIN] [{message.from_user.id}] Обновил Crypto Pay токен")
        set_crypto_pay_token(value)
        bot.send_message(message.chat.id, "✅ Токен Crypto Pay обновлён.", reply_markup=get_admin_keyboard())
    send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
def process_crypto_pay_asset_input(message, menu_chat_id, menu_message_id):
    if message.text == "❌ Отмена":
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил изменение актива Crypto Pay")
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_admin_keyboard())
        send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
        return
    asset = (message.text or "").strip().upper()
    if not asset or not re.fullmatch(r"[A-Z0-9_]{2,10}", asset):
        logging.warning(f"[ADMIN] [{message.from_user.id}] Неверный формат актива Crypto Pay: {asset}")
        msg = bot.send_message(message.chat.id, "❌ Неверный формат актива. Используйте латинские буквы/цифры, например TON или USDT.", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_crypto_pay_asset_input, menu_chat_id, menu_message_id)
        return
    logging.info(f"[ADMIN] [{message.from_user.id}] Обновил актив Crypto Pay: {asset}")
    set_crypto_asset_code(asset)
    bot.send_message(message.chat.id, f"✅ Актив выплат обновлён: <code>{asset}</code>.", reply_markup=get_admin_keyboard(), parse_mode='HTML')
    send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)

def process_crypto_pay_rate_input(message, menu_chat_id, menu_message_id):
    if message.text == "❌ Отмена":
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил изменение курса Crypto Pay")
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_admin_keyboard())
        send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
        return
    rate_text = (message.text or "").replace(",", ".").strip()
    try:
        rate = float(rate_text)
    except ValueError:
        logging.warning(f"[ADMIN] [{message.from_user.id}] Неверный формат курса Crypto Pay: {rate_text}")
        msg = bot.send_message(message.chat.id, "❌ Неверный формат. Введите число, например 100 или 95.5", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_crypto_pay_rate_input, menu_chat_id, menu_message_id)
        return
    if rate <= 0:
        logging.warning(f"[ADMIN] [{message.from_user.id}] Курс Crypto Pay должен быть больше нуля: {rate}")
        msg = bot.send_message(message.chat.id, "❌ Курс должен быть больше нуля. Попробуйте снова.", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_crypto_pay_rate_input, menu_chat_id, menu_message_id)
        return
    set_crypto_exchange_rate(rate)
    asset_code = get_crypto_asset_code()
    bot.send_message(message.chat.id, f"✅ Курс обмена обновлён: <b>1 {asset_code} = {rate:.2f} ₽</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML')
    send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
def process_crypto_pay_deposit_amount(message, asset, menu_chat_id, menu_message_id):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_admin_keyboard())
        send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
        return
    amount_text = (message.text or "").replace(",", ".").strip()
    try:
        amount = float(amount_text)
    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ Не удалось распознать сумму. Введите число, например 15.5", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_crypto_pay_deposit_amount, asset, menu_chat_id, menu_message_id)
        return
    if amount <= 0:
        msg = bot.send_message(message.chat.id, "❌ Сумма должна быть больше нуля. Попробуйте снова.", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_crypto_pay_deposit_amount, asset, menu_chat_id, menu_message_id)
        return
    client = get_crypto_pay_client()
    if not client:
        bot.send_message(message.chat.id, "⚠️ Crypto Pay не настроен. Сначала добавьте токен.", reply_markup=get_admin_keyboard())
        send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
        return
    try:
        invoice = client.create_invoice({
            "asset": asset,
            "amount": normalize_crypto_amount(amount),
            "description": f"Reserve top-up by admin {message.from_user.id}",
        })
    except CryptoPayError as exc:
        bot.send_message(message.chat.id, f"❌ Не удалось создать инвойс: {exc}", reply_markup=get_admin_keyboard())
        send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
        return
    invoice_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
    amount_formatted = invoice.get("amount") or normalize_crypto_amount(amount)
    text = (
        f"✅ Инвойс на пополнение создан.\n\n"
        f"💱 Актив: <code>{asset}</code>\n"
        f"💰 Сумма: <b>{amount_formatted}</b>\n"
        f"🆔 Invoice ID: <code>{invoice.get('invoice_id')}</code>"
    )
    inline_markup = None
    if invoice_url:
        inline_markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("💳 Оплатить через Crypto Bot", url=invoice_url)
        )
    bot.send_message(message.chat.id, text, reply_markup=inline_markup, parse_mode='HTML')
    bot.send_message(message.chat.id, "Меню администратора обновлено.", reply_markup=get_admin_keyboard())
    send_crypto_pay_admin_menu(menu_chat_id, menu_message_id, edit=True)
@bot.message_handler(func=lambda m: m.text == "📣 Рассылка" and is_admin(m.from_user.id))
def broadcast_start(m): bot.register_next_step_handler(bot.send_message(m.chat.id, "Отправьте пост для рассылки.", reply_markup=get_cancel_keyboard()), get_broadcast_content)
def get_broadcast_content(m):
    if m.text == "❌ Отмена": bot.send_message(m.chat.id, "Отмена.", reply_markup=get_admin_keyboard()); return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add("✅ Начать", "❌ Отмена")
    bot.register_next_step_handler(
        bot.send_message(m.chat.id, "Подтверждаете?", reply_markup=markup),
        confirm_and_run_broadcast,
        m.chat.id,
        m.message_id,
    )
def confirm_and_run_broadcast(m, source_chat_id, source_message_id):
    if m.text != "✅ Начать":
        bot.send_message(m.chat.id, "Отмена.", reply_markup=get_admin_keyboard())
        return
    if not broadcast_lock.acquire(blocking=False):
        bot.send_message(m.chat.id, "⚠️ Рассылка уже выполняется. Дождитесь завершения предыдущей.", reply_markup=get_admin_keyboard())
        return
    bot.send_message(m.chat.id, "Рассылка поставлена в очередь. Запускаем...", reply_markup=get_admin_keyboard())
    threading.Thread(
        target=run_broadcast_job,
        args=(m.chat.id, source_chat_id, source_message_id),
        daemon=True,
    ).start()
def run_broadcast_job(admin_chat_id, source_chat_id, source_message_id):
    try:
        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE is_banned != 1")
            users = cursor.fetchall()
            conn.close()
        if not users:
            bot.send_message(admin_chat_id, "Нет пользователей.", reply_markup=get_admin_keyboard())
            return
        total = len(users)
        success = failed = 0
        bot.send_message(admin_chat_id, f"Рассылка запущена для {total} пользователей...", reply_markup=get_admin_keyboard())
        for user_row in users:
            user_id = user_row[0]
            try:
                bot.copy_message(user_id, source_chat_id, source_message_id)
                success += 1
            except Exception as exc:
                failed += 1
                logging.debug(f"Broadcast delivery failed for {user_id}: {exc}")
            time.sleep(0.05)
        bot.send_message(
            admin_chat_id,
            f"Рассылка завершена.\nУспешно: {success}\nОшибка: {failed}",
            parse_mode='HTML'
        )
    except Exception as exc:
        logging.error(f"Ошибка при выполнении рассылки: {exc}", exc_info=True)
        bot.send_message(admin_chat_id, f"❌ Ошибка рассылки: {exc}", reply_markup=get_admin_keyboard())
    finally:
        broadcast_lock.release()
@bot.message_handler(func=lambda message: message.text == "📊 Статистика" and is_admin(message.from_user.id))
def user_stats_handler(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Запросил статистику")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        total_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        last_24h_iso = (datetime.now() - timedelta(hours=24)).isoformat()
        new_users_24h = cursor.execute("SELECT COUNT(*) FROM users WHERE registered_at >= ?", (last_24h_iso,)).fetchone()[0]
        total_referrals = cursor.execute("SELECT SUM(referral_count) FROM users").fetchone()[0] or 0
        conn.close()
    logging.info(f"[ADMIN] [{message.from_user.id}] Статистика: всего={total_users}, новых_24ч={new_users_24h}, рефералов={total_referrals}")
    stats_text = (f"📊 <b>Статистика пользователей</b>\n\n"
                  f"👥 Всего: <b>{total_users}</b>\n"
                  f"🆕 Новых за 24ч: <b>{new_users_24h}</b>\n"
                  f"💌 Приглашено: <b>{total_referrals}</b>")
    bot.send_message(message.chat.id, stats_text, parse_mode='HTML')
@bot.message_handler(func=lambda message: message.text == "📬 Заявки на вывод" and is_admin(message.from_user.id))
def list_pending_withdrawals(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Запросил список заявок на вывод")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, amount FROM withdrawals WHERE status = 'pending'"); pending_wds = cursor.fetchall(); conn.close()
    if not pending_wds: 
        logging.info(f"[ADMIN] [{message.from_user.id}] Нет заявок на вывод")
        bot.send_message(message.chat.id, "Новых заявок на вывод нет."); return
    logging.info(f"[ADMIN] [{message.from_user.id}] Найдено {len(pending_wds)} заявок на вывод")
    text = "📬 <b>Активные заявки на вывод:</b>\n"
    for wd_id, user_id, amount in pending_wds: text += f"\n/wd_{wd_id} - <b>{amount:.2f} ₽</b> от <code>{user_id}</code>"
    bot.send_message(message.chat.id, text, parse_mode='HTML')
@bot.message_handler(func=lambda message: message.text.startswith('/wd_') and is_admin(message.from_user.id))
def show_withdrawal_details(message):
    try: wd_id = int(message.text.split('_')[1])
    except: return
    logging.info(f"[ADMIN] [{message.from_user.id}] Запросил детали заявки на вывод #{wd_id}")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT user_id, amount, details, status FROM withdrawals WHERE id = ?", (wd_id,)); wd_info = cursor.fetchone()
        if not wd_info: 
            logging.warning(f"[ADMIN] [{message.from_user.id}] Заявка на вывод #{wd_id} не найдена")
            bot.reply_to(message, "Заявка не найдена."); conn.close(); return
        user_id, amount, details, status = wd_info
        cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,)); user_info = cursor.fetchone(); conn.close()
    logging.info(f"[ADMIN] [{message.from_user.id}] Просмотр заявки #{wd_id}: user={user_id}, amount={amount}, status={status}")
    name = (user_info[0] or "N/A")
    text = (f"📬 <b>Заявка №{wd_id}</b>\n\n👤 <b>Пользователь:</b> {escape(name)} (<code>{user_id}</code>)\n"
            f"💰 <b>Сумма:</b> {amount:.4f} ₽\n💳 <b>Реквизиты:</b> <code>{escape(details)}</code>\nСтатус: <b>{status}</b>")
    markup = types.InlineKeyboardMarkup()
    if status == 'pending': markup.add(types.InlineKeyboardButton("✅ Одобрить", callback_data=f"wd_approve_{wd_id}"), types.InlineKeyboardButton("❌ Отклонить", callback_data=f"wd_decline_{wd_id}"))
    bot.reply_to(message, text, reply_markup=markup, parse_mode='HTML')
@bot.callback_query_handler(func=lambda call: call.data.startswith('wd_') and is_admin(call.from_user.id))
def handle_withdrawal_admin(call):
    action, withdrawal_id = call.data.split('_')[1:]
    logging.info(f"[ADMIN] [{call.from_user.id}] Обработка заявки на вывод #{withdrawal_id}, действие: {action}")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute('SELECT user_id, amount, status FROM withdrawals WHERE id = ?', (withdrawal_id,)); res = cursor.fetchone()
        if not res or res[2] != 'pending': 
            logging.warning(f"[ADMIN] [{call.from_user.id}] Попытка обработать уже обработанную заявку #{withdrawal_id}")
            conn.close(); bot.edit_message_text(call.message.html_text + "\n\n⚠️ <b>Уже обработано.</b>", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode='HTML'); bot.answer_callback_query(call.id); return
        user_id, amount, _ = res
        if action == 'approve':
            cursor.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (withdrawal_id,)); conn.commit()
            logging.info(f"[ADMIN] [{call.from_user.id}] ОДОБРИЛ заявку на вывод #{withdrawal_id}: user={user_id}, amount={amount}")
            bot.edit_message_text(call.message.html_text + "\n\n✅ <b>ОДОБРЕНО</b>", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode='HTML')
            try: bot.send_message(user_id, f"✅ Ваша заявка на вывод {amount:.4f} ₽ одобрена!")
            except: pass
            try:
                if PAYMENTS_CHANNEL:
                    # Получаем информацию о пользователе для публикации в канал
                    cursor.execute("SELECT first_name, username FROM users WHERE user_id = ?", (user_id,))
                    user_info = cursor.fetchone()
                    username = user_info[1] if user_info and user_info[1] else None
                    username_display = f"@{escape(username)}" if username else "нет"
                    channel_text = (
                        "✅ <b>Новая выплата!</b>\n\n"
                        f"💰 <b>Сумма:</b> {amount:.2f} ₽\n"
                        f"👤 <b>Пользователь:</b> {username_display} (ID: <code>{user_id}</code>)"
                    )
                    bot.send_message(PAYMENTS_CHANNEL, channel_text, parse_mode='HTML')
            except Exception as e: logging.error(f"Не удалось отправить в канал выплат: {e}")
        elif action == 'decline':
            cursor.execute("UPDATE withdrawals SET status = 'declined' WHERE id = ?", (withdrawal_id,)); cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id)); conn.commit()
            logging.info(f"[ADMIN] [{call.from_user.id}] ОТКЛОНИЛ заявку на вывод #{withdrawal_id}: user={user_id}, amount={amount}, средства возвращены")
            bot.edit_message_text(call.message.html_text + "\n\n❌ <b>ОТКЛОНЕНО</b>", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode='HTML')
            try: bot.send_message(user_id, f"❌ Ваша заявка на вывод {amount:.4f} ₽ отклонена, средства возвращены.")
            except: pass
        conn.close(); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('semi_wd_') and is_admin(call.from_user.id))
def handle_semi_withdrawal_admin(call):
    action, withdrawal_id = call.data.split('_')[2:]
    withdrawal_id = int(withdrawal_id)
    logging.info(f"[ADMIN] [{call.from_user.id}] Обработка полуавтоматической заявки на вывод #{withdrawal_id}, действие: {action}")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, amount, status FROM withdrawals WHERE id = ?', (withdrawal_id,))
        res = cursor.fetchone()
        user_info = None
        if res:
            cursor.execute("SELECT first_name, username FROM users WHERE user_id = ?", (res[0],))
            user_info = cursor.fetchone()
        conn.close()

    if not res or res[2] != 'pending_semi_auto':
        logging.warning(f"[ADMIN] [{call.from_user.id}] Попытка обработать уже обработанную полуавтоматическую заявку #{withdrawal_id}")
        try:
            bot.edit_message_text(
                call.message.html_text + "\n\n⚠️ <b>Уже обработано.</b>",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
                parse_mode='HTML'
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    user_id, amount, _ = res
    username = user_info[1] if user_info and user_info[1] else None
    username_display = f"@{escape(username)}" if username else "нет"

    if action == 'approve':
        client = get_crypto_pay_client()
        if not client:
            bot.answer_callback_query(call.id, "Crypto Pay не настроен!", show_alert=True)
            return

        asset_code = get_crypto_asset_code()
        crypto_amount = convert_rub_to_crypto(amount)
        amount_formatted = normalize_crypto_amount(crypto_amount)

        try:
            check_payload = {
                "asset": asset_code,
                "amount": amount_formatted,
                "pin_to_user_id": user_id,
            }
            check = client.create_check(check_payload)
            check_id = check.get("check_id")
            check_url = check.get("bot_check_url") or check.get("check_url")

            if check_id and not check_url:
                try:
                    check_info = client.get_check(check_id)
                    if isinstance(check_info, dict):
                        check_url = check_info.get("bot_check_url") or check_info.get("check_url")
                except CryptoPayError:
                    pass
        except CryptoPayError as exc:
            bot.answer_callback_query(call.id, f"Ошибка создания чека: {exc}", show_alert=True)
            with db_lock:
                conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                cursor.execute("UPDATE withdrawals SET status = 'failed' WHERE id = ?", (withdrawal_id,))
                conn.commit()
                conn.close()
            return

        details_value = f"CryptoPay чек #{check_id} ({asset_code})"
        if check_url:
            details_value += f" | {check_url}"

        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
            cursor = conn.cursor()
            cursor.execute("UPDATE withdrawals SET status = 'approved', details = ? WHERE id = ?", (details_value, withdrawal_id))
            conn.commit()
            conn.close()

        logging.info(f"[ADMIN] [{call.from_user.id}] ОДОБРИЛ полуавтоматическую заявку на вывод #{withdrawal_id}: user={user_id}, amount={amount}, check_id={check_id}")
        bot.edit_message_text(
            call.message.html_text + "\n\n✅ <b>ОДОБРЕНО</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
            parse_mode='HTML'
        )

        if check_url:
            link_line = f'🔗 <a href="{check_url}">Получить чек в @CryptoBot</a>'
        else:
            link_line = "🔗 Чек доступен в разделе <code>My Checks</code> в @CryptoBot."

        success_text = (
            "✅ <b>Ваша заявка на вывод одобрена!</b>\n\n"
            f"💰 Сумма: <b>{amount:.4f} ₽</b>\n"
            f"💎 Чек: <b>{amount_formatted} {asset_code}</b>\n"
            f"{link_line}"
        )
        bot.send_message(user_id, success_text, parse_mode='HTML', disable_web_page_preview=True)

        try:
            if PAYMENTS_CHANNEL:
                channel_text = (
                    "✅ <b>Новая выплата!</b>\n\n"
                    f"💰 <b>Сумма:</b> {amount:.2f} ₽\n"
                    f"👤 <b>Пользователь:</b> {username_display} (ID: <code>{user_id}</code>)"
                )
                bot.send_message(PAYMENTS_CHANNEL, channel_text, parse_mode='HTML')
        except Exception as e:
            logging.error(f"Не удалось отправить в канал выплат: {e}")

    elif action == 'decline':
        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            cursor.execute("UPDATE withdrawals SET status = 'declined' WHERE id = ?", (withdrawal_id,))
            conn.commit()
            conn.close()

        logging.info(f"[ADMIN] [{call.from_user.id}] ОТКЛОНИЛ полуавтоматическую заявку на вывод #{withdrawal_id}: user={user_id}, amount={amount}, средства возвращены")
        bot.edit_message_text(
            call.message.html_text + "\n\n❌ <b>ОТКЛОНЕНО</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
            parse_mode='HTML'
        )
        try:
            bot.send_message(user_id, f"❌ Ваша заявка на вывод {amount:.4f} ₽ отклонена. Средства возвращены на баланс.")
        except Exception:
            pass

    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.text == "🚫 Бан/Разбан" and is_admin(message.from_user.id))
def ban_unban_start(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Начал операцию бан/разбан")
    msg = bot.send_message(message.chat.id, "Введите ID или юзернейм.", reply_markup=get_cancel_keyboard()); bot.register_next_step_handler(msg, process_ban_unban)
def process_ban_unban(message):
    if message.text == "❌ Отмена": 
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил операцию бан/разбан")
        bot.send_message(message.chat.id, "<b>Отмена.</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML'); return
    target_user = find_user_by_id_or_username(message.text)
    if not target_user: 
        logging.warning(f"[ADMIN] [{message.from_user.id}] Пользователь не найден для бан/разбан: {message.text}")
        msg = bot.send_message(message.chat.id, "❌ Пользователь не найден."); bot.register_next_step_handler(msg, process_ban_unban); return
    target_id = target_user[0]
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute('SELECT is_banned FROM users WHERE user_id = ?', (target_id,)); result = cursor.fetchone()
        if not result: 
            logging.warning(f"[ADMIN] [{message.from_user.id}] ID {target_id} не найден в БД")
            conn.close(); bot.send_message(message.chat.id, f"❌ ID <code>{target_id}</code> не найден.", reply_markup=get_admin_keyboard(), parse_mode='HTML'); return
        new_status = 1 if result[0] == 0 else 0
        cursor.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (new_status, target_id)); conn.commit(); conn.close()
    action = 'забанен' if new_status == 1 else 'разбанен'
    logging.info(f"[ADMIN] [{message.from_user.id}] {action.upper()} пользователя {target_id}")
    bot.send_message(message.chat.id, f"✅ Пользователь <code>{target_id}</code> <b>{action}</b>.", reply_markup=get_admin_keyboard(), parse_mode='HTML')
@bot.message_handler(func=lambda message: message.text == "💬 Написать юзеру" and is_admin(message.from_user.id))
def send_message_start(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Начал отправку сообщения пользователю")
    msg = bot.send_message(message.chat.id, "Введите ID или юзернейм.", reply_markup=get_cancel_keyboard()); bot.register_next_step_handler(msg, process_message_user)
def process_message_user(message):
    if message.text == "❌ Отмена": 
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил отправку сообщения")
        bot.send_message(message.chat.id, "<b>Отмена.</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML'); return
    target_user = find_user_by_id_or_username(message.text)
    if not target_user: 
        logging.warning(f"[ADMIN] [{message.from_user.id}] Пользователь не найден для отправки сообщения: {message.text}")
        msg = bot.send_message(message.chat.id, "❌ Пользователь не найден."); bot.register_next_step_handler(msg, process_message_user); return
    msg = bot.send_message(message.chat.id, "Введите текст сообщения.", reply_markup=get_cancel_keyboard()); bot.register_next_step_handler(msg, process_message_text, target_user[0])
def process_message_text(message, target_id):
    if message.text == "❌ Отмена": 
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил отправку сообщения пользователю {target_id}")
        bot.send_message(message.chat.id, "<b>Отмена.</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML'); return
    try:
        logging.info(f"[ADMIN] [{message.from_user.id}] Отправил сообщение пользователю {target_id}: {message.text[:50]}...")
        bot.send_message(target_id, f"💬 <b>Сообщение от администратора:</b>\n\n{escape(message.text)}", parse_mode='HTML')
        bot.send_message(message.chat.id, f"✅ Сообщение отправлено.", reply_markup=get_admin_keyboard())
    except Exception as e: 
        logging.error(f"[ADMIN] [{message.from_user.id}] Ошибка отправки сообщения пользователю {target_id}: {e}")
        bot.send_message(message.chat.id, f"❌ Не удалось отправить. Ошибка: {e}", reply_markup=get_admin_keyboard())
@bot.message_handler(func=lambda message: message.text == "📜 Рефералы юзера" and is_admin(message.from_user.id))
def view_referrals_start(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Запросил просмотр рефералов")
    msg = bot.send_message(message.chat.id, "Введите ID или юзернейм.", reply_markup=get_cancel_keyboard()); bot.register_next_step_handler(msg, process_view_referrals)
def process_view_referrals(message):
    if message.text == "❌ Отмена": 
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил просмотр рефералов")
        bot.send_message(message.chat.id, "<b>Отмена.</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML'); return
    target_user = find_user_by_id_or_username(message.text)
    if not target_user: 
        logging.warning(f"[ADMIN] [{message.from_user.id}] Пользователь не найден для просмотра рефералов: {message.text}")
        msg = bot.send_message(message.chat.id, "❌ Пользователь не найден."); bot.register_next_step_handler(msg, process_view_referrals); return
    target_id, target_name, _ = target_user
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT user_id, first_name, username FROM users WHERE referred_by = ?", (target_id,)); referrals = cursor.fetchall(); conn.close()
    logging.info(f"[ADMIN] [{message.from_user.id}] Просмотр рефералов пользователя {target_id}: найдено {len(referrals)} рефералов")
    if not referrals: bot.send_message(message.chat.id, f"У пользователя <code>{target_id}</code> нет рефералов.", reply_markup=get_admin_keyboard(), parse_mode='HTML'); return
    response_text = f"👥 <b>Рефералы {escape(target_name or '')} (<code>{target_id}</code>)</b> ({len(referrals)}):\n\n"
    for ref_id, name, username in referrals: response_text += f"▪️ {escape(name or 'N/A')} (@{username} | <code>{ref_id}</code>)\n"
    if len(response_text) > 4096: response_text = response_text[:4090] + "\n..."
    bot.send_message(message.chat.id, response_text, reply_markup=get_admin_keyboard(), parse_mode='HTML')
@bot.message_handler(func=lambda message: message.text == "⚙️ ОП Каналы" and is_admin(message.from_user.id))
def manage_op_channels_start(message):
    admin_id = message.from_user.id if hasattr(message, 'from_user') else message.message.from_user.id if isinstance(message, types.CallbackQuery) else None
    if admin_id:
        logging.info(f"[ADMIN] [{admin_id}] Открыл управление ОП каналами")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("SELECT channel_username FROM op_channels ORDER BY channel_username")
        channels = cursor.fetchall()
        conn.close()
    
    text = "📢 <b>Управление каналами для Обязательной Подписки:</b>\n\n"
    if not channels:
        text += "Список каналов пуст."
    else:
        text += "Текущие каналы:\n" + "\n".join([f"<code>{ch[0]}</code>" for ch in channels])
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("➕ Добавить канал", callback_data="op_add_channel"))
    if channels: markup.add(types.InlineKeyboardButton("➖ Удалить канал", callback_data="op_remove_channel"))
    markup.add(types.InlineKeyboardButton("◀️ Назад в админ-меню", callback_data="admin_back"))
    
    if isinstance(message, types.CallbackQuery):
        try:
            bot.edit_message_text(text, message.message.chat.id, message.message.message_id, reply_markup=markup, parse_mode='HTML')
        except:
            bot.send_message(message.message.chat.id, text, reply_markup=markup, parse_mode='HTML')
    else:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='HTML')
@bot.callback_query_handler(func=lambda call: call.data.startswith("op_"))
def handle_op_callbacks(call):
    admin_id = call.from_user.id
    
    if call.data == "op_add_channel":
        logging.info(f"[ADMIN] [{admin_id}] Начал добавление ОП канала")
        bot.answer_callback_query(call.id) 
        msg = bot.send_message(admin_id, "Введите юзернейм канала (напр. @channel).", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_add_op_channel)
        return

    elif call.data == "op_remove_channel":
        logging.info(f"[ADMIN] [{admin_id}] Начал удаление ОП канала")
        bot.answer_callback_query(call.id)
        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
            cursor.execute("SELECT channel_username FROM op_channels ORDER BY channel_username")
            channels = cursor.fetchall()
            conn.close()
        
        if not channels:
            logging.info(f"[ADMIN] [{admin_id}] Список каналов для удаления пуст")
            bot.answer_callback_query(call.id, "Список каналов для удаления пуст.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup()
        for channel_tuple in channels:
            channel_username = channel_tuple[0]
            markup.add(types.InlineKeyboardButton(f"Удалить {channel_username}", callback_data=f"op_confirm_remove_{channel_username}"))
        
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="op_manage_op_channels"))
        try:
            bot.edit_message_text("Выберите канал для удаления:", admin_id, call.message.message_id, reply_markup=markup)
        except Exception as e:
            logging.error(f"[ADMIN] [{admin_id}] Не удалось изменить клавиатуру для удаления канала: {e}")
        return

    elif call.data.startswith("op_confirm_remove_"):
        channel_to_remove = call.data.replace("op_confirm_remove_", "", 1)
        logging.info(f"[ADMIN] [{admin_id}] Удаляет ОП канал: {channel_to_remove}")
        with db_lock:
            conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
            cursor.execute("DELETE FROM op_channels WHERE channel_username = ?", (channel_to_remove,))
            conn.commit(); conn.close()
        logging.info(f"[ADMIN] [{admin_id}] ОП канал {channel_to_remove} успешно удален")
        bot.answer_callback_query(call.id, f"Канал {channel_to_remove} удален.")
        manage_op_channels_start(call) 
        return
        
    elif call.data == "op_manage_op_channels":
        bot.answer_callback_query(call.id)
        manage_op_channels_start(call)
        return
@bot.callback_query_handler(func=lambda call: call.data == "admin_back")
def back_to_admin_menu(call):
    logging.info(f"[ADMIN] [{call.from_user.id}] Вернулся в админ-меню")
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.send_message(call.from_user.id, "👑 <b>Админ-панель</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML')
def process_add_op_channel(message):
    if message.text == "❌ Отмена":
        logging.info(f"[ADMIN] [{message.from_user.id}] Отменил добавление ОП канала")
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_admin_keyboard())
        return

    channel_username = message.text.strip()
    logging.info(f"[ADMIN] [{message.from_user.id}] Пытается добавить ОП канал: {channel_username}")
    if not channel_username.startswith('@'):
        logging.warning(f"[ADMIN] [{message.from_user.id}] Неверный формат канала: {channel_username}")
        msg = bot.send_message(message.chat.id, "❌ <b>Неверный формат.</b> Юзернейм должен начинаться с @.", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
        bot.register_next_step_handler(msg, process_add_op_channel)
        return

    try:
        chat_member = bot.get_chat_member(channel_username, bot.get_me().id)
        if chat_member.status not in ('administrator', 'creator'):
            logging.warning(f"[ADMIN] [{message.from_user.id}] Бот не является администратором канала {channel_username}")
            raise ValueError("Бот не является администратором канала.")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"[ADMIN] [{message.from_user.id}] Ошибка проверки бота в канале {channel_username}: {e}")
        msg = bot.send_message(message.chat.id, f"❌ <b>Не удалось проверить канал.</b> Убедитесь, что юзернейм верный и бот является администратором канала. Ошибка: {e.description}", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
        bot.register_next_step_handler(msg, process_add_op_channel)
        return
    except Exception as e:
        msg = bot.send_message(message.chat.id, f"❌ <b>Неизвестная ошибка при проверке канала:</b> {e}", reply_markup=get_cancel_keyboard(), parse_mode='HTML')
        bot.register_next_step_handler(msg, process_add_op_channel)
        return
        
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10); cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO op_channels (channel_username) VALUES (?)", (channel_username,))
        conn.commit(); conn.close()
    
    bot.send_message(message.chat.id, f"✅ <b>Канал {channel_username} добавлен.</b>", reply_markup=get_admin_keyboard(), parse_mode='HTML')

@bot.message_handler(func=lambda message: message.text == "🔒 Список заблокированных" and is_admin(message.from_user.id))
def list_banned_users(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Запросил список заблокированных пользователей")
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, first_name FROM users WHERE is_banned = 1 ORDER BY user_id")
        banned_users = cursor.fetchall()
        conn.close()
    
    logging.info(f"[ADMIN] [{message.from_user.id}] Найдено {len(banned_users)} заблокированных пользователей")
    if not banned_users:
        bot.send_message(message.chat.id, "📋 <b>Список заблокированных пуст.</b>", parse_mode='HTML', reply_markup=get_admin_keyboard())
        return
    
    text = f"🔒 <b>Заблокированные пользователи ({len(banned_users)}):</b>\n\n"
    for user_id, username, first_name in banned_users:
        username_str = f"@{username}" if username else "нет"
        text += f"▪️ {escape(first_name or 'N/A')} ({username_str}) - <code>{user_id}</code>\n"
    
    if len(text) > 4096:
        # Разбиваем на части
        parts = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for part in parts:
            bot.send_message(message.chat.id, part, parse_mode='HTML')
    else:
        bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=get_admin_keyboard())

def build_settings_menu_text():
    language_check_status = "✅ Включена" if is_language_check_enabled() else "❌ Выключена"
    referral_require_gift_status = "✅ Включено" if is_referral_bonus_require_gift_enabled() else "❌ Выключено"
    referral_require_gift_count = get_referral_bonus_require_gift_count()
    semi_auto_withdraw_status = "✅ Включен" if is_semi_auto_withdraw_enabled() else "❌ Выключен"
    
    return (
        f"⚙️ <b>Настройки бота</b>\n\n"
        f"🌐 Проверка языка: {language_check_status}\n"
        f"🎁 Реферальный бонус после подарка: {referral_require_gift_status}\n"
        f"📊 Количество подарков для реферального бонуса: <b>{referral_require_gift_count}</b>\n"
        f"💳 Полуавтоматический вывод: {semi_auto_withdraw_status}\n"
    )

def get_settings_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🌐 Вкл/Выкл Проверку языка", callback_data="settings_toggle_language"))
    markup.add(types.InlineKeyboardButton("🎁 Вкл/Выкл Реферальный бонус после подарка", callback_data="settings_toggle_referral_gift"))
    markup.add(types.InlineKeyboardButton("📊 Изменить количество подарков для реферального бонуса", callback_data="settings_set_referral_gift_count"))
    markup.add(types.InlineKeyboardButton("💳 Вкл/Выкл Полуавтоматический вывод", callback_data="settings_toggle_semi_auto"))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_back"))
    return markup

@bot.message_handler(func=lambda message: message.text == "⚙️ Настройки" and is_admin(message.from_user.id))
def settings_menu(message):
    logging.info(f"[ADMIN] [{message.from_user.id}] Открыл меню настроек")
    text = build_settings_menu_text()
    markup = get_settings_markup()
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data.startswith('settings_') and is_admin(call.from_user.id))
def handle_settings_callbacks(call):
    action = call.data
    logging.info(f"[ADMIN] [{call.from_user.id}] Изменение настройки: {action}")
    
    if action == 'settings_toggle_language':
        new_state = not is_language_check_enabled()
        set_language_check_enabled(new_state)
        logging.info(f"[ADMIN] [{call.from_user.id}] Проверка языка {'включена' if new_state else 'выключена'}")
        bot.answer_callback_query(call.id, f"Проверка языка {'включена' if new_state else 'выключена'}")
        try:
            bot.edit_message_text(build_settings_menu_text(), call.message.chat.id, call.message.message_id, 
                                reply_markup=get_settings_markup(), parse_mode='HTML')
        except:
            bot.send_message(call.from_user.id, build_settings_menu_text(), reply_markup=get_settings_markup(), parse_mode='HTML')
        return
    
    elif action == 'settings_toggle_referral_gift':
        new_state = not is_referral_bonus_require_gift_enabled()
        set_referral_bonus_require_gift_enabled(new_state)
        logging.info(f"[ADMIN] [{call.from_user.id}] Реферальный бонус после подарка {'включен' if new_state else 'выключен'}")
        bot.answer_callback_query(call.id, f"Реферальный бонус после подарка {'включен' if new_state else 'выключен'}")
        try:
            bot.edit_message_text(build_settings_menu_text(), call.message.chat.id, call.message.message_id, 
                                reply_markup=get_settings_markup(), parse_mode='HTML')
        except:
            bot.send_message(call.from_user.id, build_settings_menu_text(), reply_markup=get_settings_markup(), parse_mode='HTML')
        return
    
    elif action == 'settings_set_referral_gift_count':
        logging.info(f"[ADMIN] [{call.from_user.id}] Запросил изменение количества подарков для реферального бонуса")
        bot.answer_callback_query(call.id)
        current_count = get_referral_bonus_require_gift_count()
        msg = bot.send_message(
            call.from_user.id,
            f"Введите количество подарков, необходимое для получения реферального бонуса.\n\nТекущее значение: <b>{current_count}</b>",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        bot.register_next_step_handler(msg, process_referral_gift_count_input, call.message.chat.id, call.message.message_id)
        return
    
    elif action == 'settings_toggle_semi_auto':
        new_state = not is_semi_auto_withdraw_enabled()
        set_semi_auto_withdraw_enabled(new_state)
        logging.info(f"[ADMIN] [{call.from_user.id}] Полуавтоматический вывод {'включен' if new_state else 'выключен'}")
        bot.answer_callback_query(call.id, f"Полуавтоматический вывод {'включен' if new_state else 'выключен'}")
        try:
            bot.edit_message_text(build_settings_menu_text(), call.message.chat.id, call.message.message_id, 
                                reply_markup=get_settings_markup(), parse_mode='HTML')
        except:
            bot.send_message(call.from_user.id, build_settings_menu_text(), reply_markup=get_settings_markup(), parse_mode='HTML')
        return
    
    bot.answer_callback_query(call.id, "Неизвестная команда", show_alert=True)

def process_referral_gift_count_input(message, menu_chat_id, menu_message_id):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_admin_keyboard())
        return
    
    try:
        count = int(message.text.strip())
        if count < 1:
            msg = bot.send_message(message.chat.id, "❌ Количество должно быть больше 0. Попробуйте снова.", reply_markup=get_cancel_keyboard())
            bot.register_next_step_handler(msg, process_referral_gift_count_input, menu_chat_id, menu_message_id)
            return
        set_referral_bonus_require_gift_count(count)
        bot.send_message(message.chat.id, f"✅ Количество подарков для реферального бонуса установлено: <b>{count}</b>", 
                        reply_markup=get_admin_keyboard(), parse_mode='HTML')
    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ Неверный формат. Введите число.", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, process_referral_gift_count_input, menu_chat_id, menu_message_id)

# =================================================================================
# ----------------------------------- ЗАПУСК --------------------------------------
# =================================================================================
if __name__ == '__main__':
    if async_loop:
        threading.Thread(target=lambda: async_loop.run_forever(), daemon=True).start()
        logging.info(f"Асинхронный цикл для Flyer запущен.")
        
    logging.info(f"Запуск бота-кликера (ID: {BOT_ID}) с токеном ...{TOKEN[-6:]}")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=20)
        except Exception as e:
            logging.critical(f"Критическая ошибка в главном цикле бота: {e}")
            traceback.print_exc()
            time.sleep(15)
            logging.info("Перезапуск бота...")
