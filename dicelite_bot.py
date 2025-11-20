#!/usr/bin/env python3
"""Telegram gaming bot built with pyTelegramBotAPI (telebot).

Features:
- Main menu with sections: Играть, Личный кабинет, О боте
- Inline game flows (dice, ball, darts, basket) with betting logic and payouts
- Personal cabinet with balance, totals, deposit/withdraw buttons
- About section with daily/overall statistics and resource links
- Simplified deposit/withdraw flows (Crypto Bot placeholder & auto-withdraw limits)
- Admin panel for adjusting limits, multipliers, and resource links

The bot stores state in a SQLite database for persistence.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import time
from contextlib import closing
from html import escape
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4

import requests
import telebot
from telebot import types
from telebot.apihelper import ApiException, ApiTelegramException


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("game-bot")


def _parse_admin_ids(value: Optional[str]) -> Set[int]:
    ids: Set[int] = set()
    if not value:
        return ids
    for part in value.replace(";", ",").split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            ids.add(int(candidate))
        except ValueError:
            continue
    return ids


def _normalize_creator_link(value: Optional[str]) -> str:
    if not value:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    if trimmed.startswith("@"):
        return f"https://t.me/{trimmed[1:]}"
    return trimmed


# --- Configuration via environment variables (managed by creator) ---
BOT_TOKEN = (
    os.getenv("DICELITE_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or "PASTE_TELEGRAM_BOT_TOKEN_HERE"
).strip()
if not BOT_TOKEN or BOT_TOKEN == "PASTE_TELEGRAM_BOT_TOKEN_HERE":
    raise RuntimeError("Set DICELITE_BOT_TOKEN environment variable to your Telegram bot token")

ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS"))
if not ADMIN_IDS:
    ADMIN_IDS = {7585735331}

DATABASE_PATH = os.getenv(
    "DICELITE_DB",
    os.getenv("BOT_DB_PATH", os.path.join(os.path.dirname(__file__), "bot.db")),
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
CREATOR_BRANDING_ENABLED = (
    os.getenv("CREATOR_BRANDING", "false").strip().lower() in _TRUE_VALUES
)
CREATOR_CONTACT_URL = _normalize_creator_link(
    os.getenv("CREATOR_CONTACT_URL", "https://t.me/MinxoCreatorBot")
)
CREATOR_CONTACT_BUTTON_LABEL = os.getenv(
    "CREATOR_CONTACT_BUTTON_LABEL", "🤖 Хочу такого же бота"
).strip() or "🤖 Хочу такого же бота"
CREATOR_BRANDING_MESSAGE_TEMPLATE = os.getenv(
    "CREATOR_BRANDING_MESSAGE",
    "🤖 Бот создан с помощью <a href='{link}'>Minxo Creator</a>.\n"
    "Хотите такой же? Нажмите кнопку ниже!",
)
VIP_FEATURES_ENABLED = not CREATOR_BRANDING_ENABLED


DEFAULT_SETTINGS: Dict[str, str] = {
    "chat_link": "https://t.me/your_chat",
    "channel_link": "https://t.me/your_channel",
    "big_win_link": "https://t.me/your_big_wins",
    "reviews_link": "https://t.me/your_reviews",
    "games_channel": "",  # Канал для отправки контролируемых игр
    "wins_channel": "",  # Канал для публикации победных результатов
    "crypto_bot_username": "CryptoBot",
    "crypto_pay_api_token": "",
    "crypto_pay_asset": "USDT",
    "crypto_pay_currency_type": "crypto",
    "crypto_pay_invoice_ttl": "900",
    "crypto_pay_description": "Пополнение баланса",
    "crypto_pay_fiat": "USD",
    "crypto_pay_accepted_assets": "",
    "welcome_text": "Добро пожаловать! Используйте меню ниже, чтобы управлять ботом.",
    "min_deposit": "5.0",
    "min_withdraw": "5.0",
    "min_bet": "0.50",
    "max_daily_auto_withdrawals": "3",
    "max_auto_withdraw_amount": "1.0",
    "withdraw_profit_margin": "0.0",
    "owner_profit_margin": "100.0",
    "auto_withdraw_enabled": "true",
    "profit_target": "1000.0",
    "referral_percentage": "15.0",  # Процент от выводов рефералов
    "min_reserve_topup": "1.0",  # Минимальная сумма пополнения резерва
    # Game multipliers
    "dice_multiplier_number": "2.0",
    "dice_multiplier_hilo": "1.7",
    "dice_multiplier_evenodd": "1.5",
    "ball_outcome_multiplier_hit": "1.55",
    "ball_outcome_multiplier_miss": "1.95",
    "darts_outcome_multiplier_hit": "1.35",
    "darts_outcome_multiplier_edge": "1.05",
    "darts_outcome_multiplier_miss": "1.95",
    "basket_outcome_multiplier_hit": "1.55",
    "basket_outcome_multiplier_swish": "3.0",
    "basket_outcome_multiplier_miss": "1.95",
    "mines_multiplier_3": "1.25",
    "mines_multiplier_5": "1.40",
    "mines_multiplier_7": "1.55",
    "mines_multiplier_10": "1.65",
    "mines_multiplier_12": "1.80",
    "mines_multiplier_17": "2.00",
    "mines_safe_chance": "0",
}

ENV_WELCOME = os.getenv("DICELITE_WELCOME_TEXT")
if ENV_WELCOME:
    DEFAULT_SETTINGS["welcome_text"] = ENV_WELCOME

ADMIN_SETTING_TITLES: Dict[str, str] = {
    "welcome_text": "Текст приветствия",
    "min_deposit": "Минимальное пополнение",
    "min_withdraw": "Минимальный вывод",
    "min_bet": "Минимальная ставка",
    "max_daily_auto_withdrawals": "Лимит выводов в день",
    "max_auto_withdraw_amount": "Лимит суммы автовывода",
    "withdraw_profit_margin": "Профит с вывода",
    "owner_profit_margin": "Профит владельца",
    "auto_withdraw_enabled": "Автовывод включен",
    "profit_target": "Цель по прибыли",
    "referral_percentage": "Процент реферальной системы",
    "min_reserve_topup": "Минимальное пополнение резерва",
    "chat_link": "Ссылка на чат",
    "channel_link": "Ссылка на канал",
    "big_win_link": "Ссылка на крупные выигрыши",
    "reviews_link": "Ссылка на отзывы",
    "games_channel": "Канал для контроля игр",
    "wins_channel": "Канал для публикации побед",
    "crypto_bot_username": "Ник Crypto Bot",
    "crypto_pay_api_token": "Crypto Pay API токен",
    "darts_outcome_multiplier_edge": "Дартс — попадание по краю",
    "basket_outcome_multiplier_hit": "Баскет — попадание",
    "basket_outcome_multiplier_swish": "Баскет — чистый свиш",
    "basket_outcome_multiplier_miss": "Баскет — промах",
    "mines_multiplier_3": "Мины — 3 мины",
    "mines_multiplier_5": "Мины — 5 мин",
    "mines_multiplier_7": "Мины — 7 мин",
    "mines_multiplier_10": "Мины — 10 мин",
    "mines_multiplier_12": "Мины — 12 мин",
    "mines_multiplier_17": "Мины — 17 мин",
    "mines_safe_chance": "Мины — шанс безопасной клетки (%)",
}


CANCEL_KEYWORDS = {"cancel", "отмена", "назад", "stop"}


def mask_sensitive_value(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return "не задано"
    if len(trimmed) <= 4:
        return "*" * len(trimmed)
    return f"{trimmed[:4]}...{trimmed[-4:]}"


def setting_display_name(setting_key: str) -> str:
    return ADMIN_SETTING_TITLES.get(setting_key, setting_key)


def format_setting_display(setting_key: str, value: Optional[str]) -> str:
    if value is None or value == "":
        return "не задано"
    lowered = setting_key.lower()
    if "token" in lowered or "secret" in lowered or "password" in lowered:
        return mask_sensitive_value(value)
    if setting_key == "mines_safe_chance":
        return f"{value}%"
    return value


def admin_setting_button_label(setting_key: str) -> str:
    title = setting_display_name(setting_key)
    if title == setting_key:
        return setting_key
    return f"{title} ({setting_key})"


MONEY_QUANT = Decimal("0.01")
CRYPTOPAY_API_TOKEN = (
    os.getenv("DICELITE_CRYPTO_PAY_TOKEN")
    or os.getenv("CRYPTOPAY_TOKEN", "")
).strip()
CRYPTOPAY_USE_TESTNET = (
    os.getenv("DICELITE_CRYPTOPAY_USE_TESTNET", os.getenv("CRYPTOPAY_USE_TESTNET", "false"))
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)
CRYPTOPAY_BASE_URL = (
    "https://testnet-pay.crypt.bot/api"
    if CRYPTOPAY_USE_TESTNET
    else "https://pay.crypt.bot/api"
)
CRYPTOPAY_TIMEOUT = float(os.getenv("CRYPTOPAY_TIMEOUT", "10"))

CRYPTO_CHECK_ACTIVE_STATUSES: Set[str] = {"active", "not_paid"}
CRYPTO_CHECK_DELETABLE_STATUSES: Set[str] = {"active", "not_paid"}
CRYPTO_CHECK_STATUS_LABELS: Dict[str, str] = {
    "active": "активен",
    "not_paid": "ожидает оплаты",
    "paid": "оплачен",
    "completed": "завершен",
    "cancelled": "отменен",
    "canceled": "отменен",
    "expired": "истек",
}


def check_user_subscription(user_id: int, required_channels: List[sqlite3.Row]) -> Tuple[bool, List[sqlite3.Row]]:
    """
    Check if user is subscribed to all required channels.
    Returns (is_subscribed: bool, not_subscribed_channels: List[Row])
    """
    not_subscribed = []
    
    for channel in required_channels:
        try:
            # Пробуем получить информацию о членстве пользователя
            member = bot.get_chat_member(channel["channel_id"], user_id)
            # Если статус не 'left' и не 'kicked', то пользователь подписан
            if member.status in ['left', 'kicked']:
                not_subscribed.append(channel)
        except ApiException as e:
            # Если бот не может проверить подписку (например, не админ канала)
            logger.warning(f"Cannot check subscription for channel {channel['channel_id']}: {e}")
            # В этом случае считаем, что пользователь не подписан
            not_subscribed.append(channel)
    
    return len(not_subscribed) == 0, not_subscribed


def build_subscription_required_markup(not_subscribed_channels: List[sqlite3.Row]) -> types.InlineKeyboardMarkup:
    """
    Build inline keyboard with subscription buttons for required channels.
    """
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for channel in not_subscribed_channels:
        # Добавляем кнопку подписки на канал
        markup.add(
            types.InlineKeyboardButton(
                f"📢 {channel['channel_name']}",
                url=channel['channel_link']
            )
        )
    
    # Добавляем кнопку "Я подписался"
    markup.add(
        types.InlineKeyboardButton(
            "✅ Я подписался!",
            callback_data="check_subscription"
        )
    )
    
    return markup


def check_and_enforce_subscription(user_id: int, chat_id: int) -> bool:
    """
    Check if user is subscribed to all required channels.
    If not subscribed, sends subscription prompt and returns False.
    Returns True if subscribed (or admin, or no required channels).
    """
    # Skip check for admins
    if db.is_admin(user_id):
        return True
    
    required_channels = db.get_all_required_channels()
    if not required_channels:
        return True
    
    is_subscribed, not_subscribed = check_user_subscription(user_id, required_channels)
    
    if not is_subscribed:
        logger.info("User %s attempted to use bot without required subscriptions", user_id)
        text_lines = [
            "📢 <b>Обязательная подписка</b>",
            "",
            "Для использования бота необходимо подписаться на следующие каналы:",
            "",
        ]
        for channel in not_subscribed:
            text_lines.append(f"• {channel['channel_name']}")
        
        text_lines.append("")
        text_lines.append("После подписки нажмите кнопку ниже для проверки.")
        
        markup = build_subscription_required_markup(not_subscribed)
        bot.send_message(
            chat_id,
            "\n".join(text_lines),
            reply_markup=markup,
            parse_mode="HTML"
        )
        return False
    
    return True


def is_creator_branding_active() -> bool:
    return CREATOR_BRANDING_ENABLED and bool(CREATOR_CONTACT_URL)


def build_creator_branding_markup() -> Optional[types.InlineKeyboardMarkup]:
    if not CREATOR_CONTACT_URL:
        return None
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            CREATOR_CONTACT_BUTTON_LABEL,
            url=CREATOR_CONTACT_URL,
        )
    )
    return markup


def render_creator_branding_text() -> Optional[str]:
    if not CREATOR_BRANDING_ENABLED:
        return None
    template = CREATOR_BRANDING_MESSAGE_TEMPLATE or ""
    if not template.strip():
        return None
    return template.replace("{link}", CREATOR_CONTACT_URL or "#")


def send_creator_branding_banner(chat_id: int) -> None:
    if not is_creator_branding_active():
        return
    message_text = render_creator_branding_text()
    if not message_text:
        return
    markup = build_creator_branding_markup()
    try:
        bot.send_message(
            chat_id,
            message_text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except ApiException as exc:
        logger.debug("Failed to send creator branding banner: %s", exc)


GAME_RULES: Dict[str, Dict[str, Any]] = {
    "dice": {
        "emoji": "🎲",
        "label": "Кубик",
        "min_value": 1,
        "max_value": 6,
        "tagline": "Угадай число 1–6",
        "bet_types": {
            "number": {
                "title": "🎯 На число",
                "description": "Полная отдача при точном попадании.",
                "target_prompt": "Выберите число:",
                "target_type": "number",
            },
            "hilo": {
                "title": "📈 Больше / Меньше",
                "description": "Выберите сторону кубика.",
                "target_prompt": "Выберите диапазон:",
                "target_type": "choice",
                "row_width": 1,
                "targets": [
                    {"key": "low", "label": "⬇️ 1–3", "values": [1, 2, 3]},
                    {"key": "high", "label": "⬆️ 4–6", "values": [4, 5, 6]},
                ],
            },
            "evenodd": {
                "title": "🔁 Чёт / Нечёт",
                "description": "Испытай интуицию на парности.",
                "target_prompt": "Выберите парность:",
                "target_type": "choice",
                "row_width": 2,
                "targets": [
                    {"key": "even", "label": "⚪ Чёт", "values": [2, 4, 6]},
                    {"key": "odd", "label": "⚫ Нечёт", "values": [1, 3, 5]},
                ],
            },
        },
    },
    "ball": {
        "emoji": "⚽",
        "label": "Мяч",
        "min_value": 1,
        "max_value": 5,
        "tagline": "Забей точный гол",
        "bet_types": {
          "outcome": {
              "title": "🥅 Исход удара",
              "description": "Выбери: попадание или промах.",
              "target_prompt": "Выберите исход удара:",
              "target_type": "choice",
              "row_width": 2,
              "targets": [
                  {
                      "key": "hit",
                      "label": "🥳 Попадание",
                      "values": [3, 4, 5],
                      "multiplier_key": "ball_outcome_multiplier_hit",
                  },
                  {
                      "key": "miss",
                      "label": "😵 Промах",
                      "values": [1, 2],
                      "multiplier_key": "ball_outcome_multiplier_miss",
                  },
              ],
          },
        },
    },
    "darts": {
        "emoji": "🎯",
        "label": "Дартс",
        "min_value": 1,
        "max_value": 6,
        "tagline": "Попади в нужный сектор",
        "bet_types": {
          "outcome": {
              "title": "🎯 Попадание или промах",
              "description": "Ставь на точность броска.",
              "target_prompt": "Выберите исход броска:",
              "target_type": "choice",
              "row_width": 2,
              "targets": [
                  {
                      "key": "hit",
                      "label": "🎯 Попадание",
                      "values": [2, 3, 4, 5, 6],
                      "multiplier_key": "darts_outcome_multiplier_hit",
                  },
                  {
                      "key": "edge",
                      "label": "🟠 По краю",
                      "values": [2, 3, 4, 5],
                      "multiplier_key": "darts_outcome_multiplier_edge",
                      "hidden": True,
                  },
                  {
                      "key": "miss",
                      "label": "🚫 Промах",
                      "values": [1],
                      "multiplier_key": "darts_outcome_multiplier_miss",
                  },
              ],
          },
        },
    },
    "basket": {
        "emoji": "🏀",
        "label": "Баскет",
        "min_value": 1,
        "max_value": 5,
        "tagline": "Меткость на паркете",
        "bet_types": {
          "outcome": {
              "title": "🏀 Исход броска",
              "description": "Угадай, попадёт ли мяч в кольцо.",
              "target_prompt": "Выберите исход броска:",
              "target_type": "choice",
              "row_width": 2,
              "targets": [
                  {
                      "key": "hit",
                      "label": "🥳 Попадание (чистый свиш ×3.0)",
                      "values": [4, 5],
                      "multiplier_key": "basket_outcome_multiplier_hit",
                  },
                  {
                      "key": "swish",
                      "label": "🎯 Чистый свиш",
                      "values": [5],
                      "multiplier_key": "basket_outcome_multiplier_swish",
                      "hidden": True,
                  },
                  {
                      "key": "miss",
                      "label": "😵 Промах",
                      "values": [1, 2, 3],
                      "multiplier_key": "basket_outcome_multiplier_miss",
                  },
              ],
          },
        },
    },
    "mines": {
        "emoji": "💣",
        "label": "Мины",
        "tagline": "Жми на клетки, избегай мин и забирай множитель.",
        "bet_types": {
            "mine_count": {
                "title": "⚙️ Сложность",
                "description": "Чем больше мин, тем выше множитель за каждую открытую безопасную клетку.",
                "target_prompt": "Выберите количество мин:",
                "target_type": "choice",
                "row_width": 3,
                "targets": [
                    {
                        "key": "3",
                        "label": "💣 3 мины",
                        "multiplier_key": "mines_multiplier_3",
                        "default_multiplier": "1.25",
                        "mine_count": 3,
                    },
                    {
                        "key": "5",
                        "label": "💣 5 мин",
                        "multiplier_key": "mines_multiplier_5",
                        "default_multiplier": "1.40",
                        "mine_count": 5,
                    },
                    {
                        "key": "7",
                        "label": "💣 7 мин",
                        "multiplier_key": "mines_multiplier_7",
                        "default_multiplier": "1.55",
                        "mine_count": 7,
                    },
                    {
                        "key": "10",
                        "label": "💣 10 мин",
                        "multiplier_key": "mines_multiplier_10",
                        "default_multiplier": "1.65",
                        "mine_count": 10,
                    },
                    {
                        "key": "12",
                        "label": "💣 12 мин",
                        "multiplier_key": "mines_multiplier_12",
                        "default_multiplier": "1.80",
                        "mine_count": 12,
                    },
                    {
                        "key": "17",
                        "label": "💣 17 мин",
                        "multiplier_key": "mines_multiplier_17",
                        "default_multiplier": "2.00",
                        "mine_count": 17,
                    },
                ],
            },
        },
    },
}


DARTS_BULLSEYE_VALUE = 6
DARTS_BULLSEYE_MULTIPLIER = Decimal("5.0")


def get_bet_types(game_key: str) -> Dict[str, Dict[str, Any]]:
    rules = GAME_RULES.get(game_key, {})
    return rules.get("bet_types", {})


def get_bet_config(game_key: str, bet_type: str) -> Optional[Dict[str, Any]]:
    bet_types = get_bet_types(game_key)
    return bet_types.get(bet_type)


def find_target_option(bet_config: Dict[str, Any], target_key: str) -> Optional[Dict[str, Any]]:
    targets = bet_config.get("targets") or []
    for option in targets:
        if option.get("key") == target_key:
            return option
    return None


GAME_OUTCOME_LABELS: Dict[str, Dict[int, str]] = {
    "dice": {
        1: "🎲 Выпало 1",
        2: "🎲 Выпало 2",
        3: "🎲 Выпало 3",
        4: "🎲 Выпало 4",
        5: "🎲 Выпало 5",
        6: "🎲 Выпало 6",
    },
    "ball": {
        1: "🧤 Вратарь поймал удар",
        2: "🪵 Мяч в перекладину",
        3: "🥅 Гол по центру",
        4: "🎯 Гол в правую девятку",
        5: "🔥 Гол в левую девятку",
    },
    "darts": {
        1: "🚫 Мимо мишени",
        2: "🟠 Попадание по краю",
        3: "🟡 Внешнее кольцо",
        4: "🔴 Внутреннее кольцо",
        5: "💥 Трипл-сектор",
        6: "🎯 Буллсай",
    },
    "basket": {
        1: "↙️ Мимо слева",
        2: "🪫 Мяч соскользнул с кольца",
        3: "🔒 Мяч застрял над кольцом",
        4: "🪵 Попадание от щита",
        5: "🎯 Чистый свиш",
    },
}


def describe_outcome(game_key: str, result_value: int) -> Optional[str]:
    return GAME_OUTCOME_LABELS.get(game_key, {}).get(result_value)


def possible_values_for_game(game_key: str) -> Set[int]:
    """Return the full set of result values Telegram can return for a game."""
    rules = GAME_RULES.get(game_key, {})
    min_value = rules.get("min_value")
    max_value = rules.get("max_value")
    if isinstance(min_value, int) and isinstance(max_value, int) and min_value <= max_value:
        return set(range(int(min_value), int(max_value) + 1))
    return set()


def winning_values_for_bet(game_key: str, bet_type: str, target: str) -> Set[int]:
    """Return the set of values that correspond to a winning outcome for the bet."""
    bet_config = get_bet_config(game_key, bet_type)
    if not bet_config:
        return set()

    target_type = bet_config.get("target_type")
    if target_type == "number":
        try:
            return {int(target)}
        except (TypeError, ValueError):
            logger.warning("winning_values_for_bet: invalid numeric target '%s' for %s/%s", target, game_key, bet_type)
            return set()
    if target_type == "choice":
        option = find_target_option(bet_config, target)
        if not option:
            logger.warning(
                "winning_values_for_bet: no option found for target '%s' in %s/%s",
                target,
                game_key,
                bet_type,
            )
            return set()
        values = option.get("values") or []
        try:
            return {int(value) for value in values}
        except (TypeError, ValueError):
            logger.warning(
                "winning_values_for_bet: invalid value list %s for %s/%s target '%s'",
                values,
                game_key,
                bet_type,
                target,
            )
            return set()

    logger.warning("winning_values_for_bet: unsupported target_type '%s' for %s/%s", target_type, game_key, bet_type)
    return set()


def determine_forced_outcome(
    game_key: str,
    winning_values: Set[int],
    should_reduce: bool,
    chance_multiplier: float,
) -> Optional[bool]:
    """
    Decide whether to force a win (True), force a loss (False) or keep the natural roll (None)
    based on profit protection state.
    """
    if not should_reduce:
        return None

    if not winning_values:
        logger.debug("Profit guard: no winning values for %s; cannot enforce outcome.", game_key)
        return None

    possible_values = possible_values_for_game(game_key)
    if not possible_values:
        logger.debug("Profit guard: unknown possible values for %s; fallback to natural roll.", game_key)
        return None

    random_value = random.random()
    allow_win = random_value < chance_multiplier
    if not allow_win and winning_values == possible_values:
        logger.warning(
            "Profit guard: cannot force loss for %s because winning set covers all possible values.",
            game_key,
        )
        return None

    logger.info(
        "Profit guard decision: game=%s, chance_multiplier=%.2f, random=%.2f -> %s",
        game_key,
        chance_multiplier,
        random_value,
        "force WIN" if allow_win else "force LOSS",
    )
    return allow_win


# Хранилище последних игр из канала (в памяти)
# Структура: {game_key: {"chat_id": int, "message_id": int, "result": int}}
channel_games_cache: Dict[str, Dict[str, Any]] = {}

_games_channel_cache: Dict[str, Optional[Union[int, str]]] = {"raw": None, "resolved": None}


def normalize_channel_reference(raw: Optional[str]) -> Optional[Union[int, str]]:
    """Приводит ссылку или идентификатор канала к формату, пригодному для Telegram API."""
    if not raw:
        return None

    reference = str(raw).strip()
    if not reference:
        return None

    lowered = reference.lower()
    if "t.me/" in lowered:
        try:
            _, tail = reference.split("t.me/", 1)
        except ValueError:
            tail = reference
        reference = tail.split("/", 1)[0]
    reference = reference.split("?", 1)[0]
    reference = reference.split("#", 1)[0]
    reference = reference.strip()

    if not reference:
        return None

    if reference.startswith("+"):
        reference = reference[1:].strip()

    if reference.startswith("@"):
        return reference

    numeric_candidate = reference.lstrip("-")
    if numeric_candidate.isdigit():
        try:
            return int(reference)
        except ValueError:
            logger.debug("Failed to parse numeric channel reference '%s'", reference)

    return f"@{reference}"


def resolve_games_channel_target() -> Optional[Union[int, str]]:
    """Возвращает нормализованный идентификатор канала для контролируемых игр."""
    try:
        settings = db.get_settings()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load settings when resolving games channel: %s", exc, exc_info=True)
        return None

    raw_value = (settings.get("games_channel") or "").strip()

    cached_raw = _games_channel_cache.get("raw")
    if raw_value == cached_raw:
        return _games_channel_cache.get("resolved")

    normalized = normalize_channel_reference(raw_value)
    _games_channel_cache["raw"] = raw_value
    _games_channel_cache["resolved"] = normalized
    return normalized


def _try_controlled_roll_in_aux_chat(
    target_chat: Union[int, str],
    emoji: str,
    game_key: str,
    winning_values: Set[int],
    force_win: bool,
    max_attempts: int,
    delay: float,
) -> Optional[Tuple[int, types.Message]]:
    """
    Пытается выполнить контролируемый бросок в дополнительном чате (канале),
    чтобы не спамить пользователя промежуточными результатами.
    """
    attempts = 0
    last_message: Optional[types.Message] = None
    last_value = 0

    while attempts < max_attempts:
        attempts += 1
        try:
            dice_message = bot.send_dice(chat_id=target_chat, emoji=emoji)
        except ApiException as exc:
            logger.warning(
                "Failed to send dice to control chat %s for game %s: %s",
                target_chat,
                game_key,
                exc,
            )
            return None

        last_message = dice_message
        last_value = dice_message.dice.value
        is_win_value = last_value in winning_values

        if is_win_value == force_win:
            logger.debug(
                "Controlled roll succeeded in aux chat after %s attempts (game=%s, value=%s)",
                attempts,
                game_key,
                last_value,
            )
            return last_value, dice_message

        try:
            bot.delete_message(chat_id=dice_message.chat.id, message_id=dice_message.message_id)
        except ApiException:
            logger.debug(
                "Failed to delete interim dice message %s in aux chat %s for game %s",
                dice_message.message_id,
                dice_message.chat.id,
                game_key,
            )

        if attempts < max_attempts:
            time.sleep(delay)

    logger.warning(
        "Controlled roll in aux chat reached max attempts for game %s (force_win=%s, last_value=%s)",
        game_key,
        force_win,
        last_value,
    )
    return None


def save_channel_game(game_key: str, chat_id: int, message_id: int, result: int) -> None:
    """Сохраняет информацию о последней игре из канала."""
    channel_games_cache[game_key] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "result": result,
    }
    logger.info(f"Saved channel game: {game_key} = {result} (msg_id: {message_id})")


def get_channel_game(game_key: str) -> Optional[Dict[str, Any]]:
    """Получает информацию о последней игре из канала."""
    return channel_games_cache.get(game_key)


def roll_controlled_dice(
    chat_id: int,
    emoji: str,
    game_key: str,
    winning_values: Set[int],
    force_win: Optional[bool],
    *,
    max_attempts: int = 40,
    delay: float = 0.45,
) -> Tuple[int, types.Message]:
    """
    Roll Telegram dice until the outcome matches the requested win/loss directive.
    Returns (result_value, message).
    """
    if force_win is not None:
        aux_chat = resolve_games_channel_target()
        if aux_chat is not None:
            aux_result = _try_controlled_roll_in_aux_chat(
                target_chat=aux_chat,
                emoji=emoji,
                game_key=game_key,
                winning_values=winning_values,
                force_win=force_win,
                max_attempts=max_attempts,
                delay=delay,
            )
            if aux_result is not None:
                result_value, aux_message = aux_result
                try:
                    copied_message = bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=aux_message.chat.id,
                        message_id=aux_message.message_id,
                    )
                except ApiException as copy_exc:
                    logger.warning(
                        "Failed to copy controlled dice from aux chat for game %s: %s",
                        game_key,
                        copy_exc,
                    )
                    try:
                        copied_message = bot.forward_message(
                            chat_id=chat_id,
                            from_chat_id=aux_message.chat.id,
                            message_id=aux_message.message_id,
                        )
                    except ApiException as forward_exc:
                        logger.warning(
                            "Failed to forward controlled dice message for game %s: %s",
                            game_key,
                            forward_exc,
                        )
                    else:
                        try:
                            save_channel_game(
                                game_key,
                                aux_message.chat.id,
                                aux_message.message_id,
                                result_value,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "Failed to cache controlled game after forward: %s",
                                exc,
                            )
                        return result_value, copied_message
                else:
                    if isinstance(copied_message, types.Message):
                        try:
                            save_channel_game(
                                game_key,
                                aux_message.chat.id,
                                aux_message.message_id,
                                result_value,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "Failed to cache controlled game after copy: %s",
                                exc,
                            )
                        return result_value, copied_message
                    logger.debug(
                        "copy_message returned unexpected response type %s; falling back to direct roll",
                        type(copied_message),
                    )
            else:
                logger.debug(
                    "Unable to obtain controlled roll in aux chat for game %s; falling back to direct roll",
                    game_key,
                )

    attempts = 0
    last_value = 0
    last_message: Optional[types.Message] = None
    interim_message_ids: List[int] = []

    target_attempts = max_attempts if force_win is not None else 1

    while attempts < target_attempts:
        attempts += 1
        dice_message = bot.send_dice(chat_id, emoji=emoji)
        last_message = dice_message
        last_value = dice_message.dice.value
        is_win_value = last_value in winning_values

        if force_win is None or is_win_value == force_win:
            break

        logger.debug(
            "Profit guard reroll: game=%s attempt=%s value=%s win_value=%s",
            game_key,
            attempts,
            last_value,
            is_win_value,
        )
        interim_message_ids.append(dice_message.message_id)
        try:
            bot.delete_message(chat_id, dice_message.message_id)
        except ApiException as delete_exc:
            logger.debug(
                "Profit guard: failed to delete interim dice message %s for game %s: %s",
                dice_message.message_id,
                game_key,
                delete_exc,
            )
        if attempts < target_attempts:
            time.sleep(delay)
    else:
        logger.warning(
            "Profit guard: max attempts reached enforcing outcome for %s; final value=%s (may not match directive)",
            game_key,
            last_value,
        )

    if interim_message_ids:
        time.sleep(0.25)
        for message_id in interim_message_ids:
            try:
                bot.delete_message(chat_id, message_id)
            except ApiException:
                logger.debug(
                    "Profit guard: unable to delete deferred interim dice message %s for game %s",
                    message_id,
                    game_key,
                )

    if last_message is None:
        # Safety net; should not happen, but avoids returning None.
        last_message = bot.send_dice(chat_id, emoji=emoji)
        last_value = last_message.dice.value

    return last_value, last_message


class CryptoPayError(RuntimeError):
    """Raised when Crypto Pay API returns an error or is misconfigured."""


class CryptoPayClient:
    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        timeout: float = 10.0,
    ) -> None:
        self._token = token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    def _sanitize_mapping(self, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not data:
            return {}
        sanitized: Dict[str, Any] = {}
        for key, value in data.items():
            if key in {"payload"} and value is not None:
                value_str = str(value)
                sanitized[key] = f"<len={len(value_str)} chars>"
            elif key in {"hash", "secret"} and isinstance(value, str):
                trimmed = value.strip()
                if len(trimmed) > 10:
                    sanitized[key] = f"{trimmed[:5]}...{trimmed[-5:]}"
                else:
                    sanitized[key] = trimmed
            else:
                sanitized[key] = value
        return sanitized

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    def set_token(self, token: str) -> None:
        self._token = token.strip()

    def _request(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.is_configured:
            logger.error("CryptoPayClient._request() called but token not configured!")
            raise CryptoPayError("Crypto Pay API token is not configured")

        url = f"{self._base_url}/{method}"
        safe_payload = self._sanitize_mapping(payload)
        logger.info("Crypto Pay API request: method=%s, url=%s, payload=%s", method, url, safe_payload)
        try:
            logger.info("Sending POST request to %s...", url)
            # Explicitly encode JSON with UTF-8 to handle emoji and unicode characters
            json_data = json.dumps(payload or {}, ensure_ascii=False)
            response = self._session.post(
                url,
                headers={
                    "Crypto-Pay-API-Token": self._token,
                    "Content-Type": "application/json; charset=utf-8",
                },
                data=json_data.encode('utf-8'),
                timeout=self._timeout,
            )
            logger.info("Got response from Crypto Pay: status=%s", response.status_code)
        except requests.RequestException as exc:  # pragma: no cover - network failure paths
            logger.error("Crypto Pay network error: %s", exc, exc_info=True)
            raise CryptoPayError(f"Crypto Pay network error: {exc}") from exc

        logger.info(
            "Crypto Pay response %s status=%s", method, getattr(response, "status_code", "?")
        )
        try:
            data = response.json()
            logger.info("Parsed JSON response from Crypto Pay")
        except ValueError as exc:
            logger.error("Crypto Pay returned non-JSON response: %s", exc, exc_info=True)
            raise CryptoPayError("Crypto Pay returned non-JSON response") from exc

        logger.info("Crypto Pay response data %s: %s", method, self._sanitize_mapping(data))
        if not data.get("ok"):
            error_msg = data.get("error", "Unknown Crypto Pay error")
            logger.error("Crypto Pay API error: %s, full data: %s", error_msg, data)
            raise CryptoPayError(error_msg)
        result = data.get("result")
        if result is None:
            logger.error("Crypto Pay response missing 'result' field! Full data: %s", data)
            raise CryptoPayError("Crypto Pay response missing result field")
        logger.info("Crypto Pay request successful, returning result")
        return result

    def create_invoice(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("createInvoice", params)

    def get_invoice(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        result = self._request("getInvoices", {"invoice_ids": str(invoice_id), "count": 1})
        if isinstance(result, dict):
            items = result.get("items") or result.get("invoices")
        else:
            items = result
        if not items:
            return None
        return items[0]

    def delete_invoice(self, invoice_id: int) -> bool:
        result = self._request("deleteInvoice", {"invoice_id": invoice_id})
        return bool(result)

    def create_check(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a check for withdrawal."""
        return self._request("createCheck", params)

    def get_checks(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get list of checks with optional filters."""
        return self._request("getChecks", params or {})

    def delete_check(self, check_id: int) -> bool:
        """Delete a check by ID."""
        result = self._request("deleteCheck", {"check_id": check_id})
        return bool(result)

    def get_balance(self) -> List[Dict[str, Any]]:
        """Fetch current Crypto Pay balances."""
        result = self._request("getBalance")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            balances = result.get("balances")
            if isinstance(balances, list):
                return balances
            return [result]
        return []

    def create_transfer(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("transfer", params)


def decimal_to_str(amount: Decimal, quant: Decimal = MONEY_QUANT) -> str:
    quantized = amount.quantize(quant, rounding=ROUND_DOWN)
    return f"{quantized:.2f}"


def format_money(amount: Decimal) -> str:
    return decimal_to_str(amount)


def resolve_reserve_asset(settings: Dict[str, str]) -> str:
    asset_setting = settings.get("crypto_pay_asset", DEFAULT_SETTINGS["crypto_pay_asset"])
    asset = (asset_setting or DEFAULT_SETTINGS["crypto_pay_asset"]).strip().upper()
    if not asset:
        asset = DEFAULT_SETTINGS["crypto_pay_asset"]
    return asset


def safe_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def format_balance_component(value: Any) -> str:
    if value is None:
        return "0"
    decimal_value = safe_decimal(value)
    value_str = format(decimal_value, "f")
    if "." in value_str:
        value_str = value_str.rstrip("0").rstrip(".")
    return value_str or "0"


def get_reserve_balance_summary(settings: Dict[str, str]) -> Dict[str, Optional[str]]:
    asset_code = resolve_reserve_asset(settings)
    summary: Dict[str, Optional[str]] = {
        "asset": asset_code,
        "available": None,
        "onhold": None,
        "total": None,
        "error": None,
    }
    if not crypto_pay_client.is_configured:
        summary["error"] = "⚠️ Crypto Pay не настроен. Укажите API токен в настройках."
        return summary
    try:
        balances = crypto_pay_client.get_balance()
    except CryptoPayError as exc:
        logger.warning("Failed to fetch Crypto Pay balance: %s", exc)
        summary["error"] = f"⚠️ Не удалось получить баланс через Crypto Pay: {exc}"
        return summary
    if not balances:
        logger.warning("Crypto Pay getBalance returned empty result")
        summary["error"] = "⚠️ Crypto Pay не вернул данные о балансе."
        return summary
    balance_entry: Optional[Dict[str, Any]] = None
    for balance_item in balances:
        code = str(
            balance_item.get("currency_code")
            or balance_item.get("asset")
            or balance_item.get("currency")
            or balance_item.get("ticker")
            or ""
        ).upper()
        if code == asset_code:
            balance_entry = balance_item
            break
    if balance_entry is None:
        logger.warning("Crypto Pay balance for asset %s not found in response", asset_code)
        summary["error"] = f"⚠️ Баланс для актива {asset_code} не найден в Crypto Pay."
        return summary
    available_dec = safe_decimal(balance_entry.get("available", "0"))
    onhold_dec = safe_decimal(balance_entry.get("onhold", "0"))
    summary["available"] = format_balance_component(available_dec)
    if onhold_dec > Decimal("0"):
        summary["onhold"] = format_balance_component(onhold_dec)
    total_dec = available_dec + onhold_dec
    summary["total"] = format_balance_component(total_dec)
    return summary


def send_win_to_channel(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    game_key: str,
    payout: Decimal,
    multiplier: Decimal,
) -> None:
    """
    Отправляет красиво оформленное сообщение о победе в канал побед.
    
    Args:
        user_id: ID пользователя
        username: Username пользователя (может быть None)
        first_name: Имя пользователя (может быть None)
        game_key: Ключ игры (dice, ball, darts, basket, mines)
        payout: Сумма выигрыша
        multiplier: Множитель выигрыша
    """
    try:
        settings = db.get_settings()
        wins_channel = settings.get("wins_channel", "").strip()
        
        if not wins_channel:
            logger.warning("⚠️ Канал побед не настроен! Укажите 'wins_channel' в админ-панели для публикации побед.")
            return
        
        # Получаем фото для раздела побед
        photo = db.get_section_photo("wins")
        
        # Получаем информацию об игре
        rules = GAME_RULES.get(game_key, {})
        game_emoji = rules.get("emoji", "🎮")
        game_label = rules.get("label", "Игра")
        
        # Формируем информацию о пользователе
        if username:
            user_display = f"@{username}"
        else:
            user_display = f"ID: {user_id}"
        
        user_name = first_name or "Игрок"
        
        # Формируем красивое сообщение с цитатой
        caption_lines = [
            f"🎉 <b>Новая победа!</b>",
            "",
            f"<blockquote>🏆 <b>Победа в игре {game_emoji} {game_label}</b>",
            f"",
            f"Множитель: <b>× {multiplier:.2f}</b>",
            f"Выигрыш: <b>{format_money(payout)} $</b></blockquote>",
            "",
            f"👤 Игрок: {user_name} ({user_display})",
        ]
        
        caption = "\n".join(caption_lines)
        
        # Отправляем в канал
        if photo:
            # Если есть фото - отправляем с фото
            bot.send_photo(
                chat_id=wins_channel,
                photo=photo["file_id"],
                caption=caption,
                parse_mode="HTML",
            )
            logger.info(f"✅ Отправлена победа в канал {wins_channel} (с фото)")
        else:
            # Если нет фото - отправляем просто текстовое сообщение
            bot.send_message(
                chat_id=wins_channel,
                text=caption,
                parse_mode="HTML",
            )
            logger.info(f"✅ Отправлена победа в канал {wins_channel} (без фото)")
    
    except ApiException as e:
        logger.error(f"❌ Ошибка при отправке победы в канал: {e}")
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при отправке победы в канал: {e}")


def row_decimal(row: sqlite3.Row, key: str) -> Decimal:
    value = row[key]
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def build_cancel_keyboard(action: str, data: Optional[str] = None) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    callback_data = f"cancel:{action}"
    if data:
        callback_data = f"{callback_data}:{data}"
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=callback_data))
    return markup


def send_withdraw_response_message(
    message: types.Message,
    text: str,
    *,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> None:
    photo = db.get_section_photo("withdraw")
    try:
        if photo:
            bot.send_photo(
                message.chat.id,
                photo["file_id"],
                caption=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                reply_to_message_id=message.message_id,
            )
        else:
            bot.reply_to(
                message,
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
    except ApiException as exc:
        logger.debug("Failed to send withdraw response with media: %s", exc)
        bot.reply_to(
            message,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


class Database:
    """Lightweight SQLite wrapper with thread-safe operations."""

    def __init__(self, db_path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("Connected to database %s", db_path)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id        INTEGER PRIMARY KEY,
                    username       TEXT,
                    first_name     TEXT,
                    balance        REAL NOT NULL DEFAULT 0,
                    deposited_total REAL NOT NULL DEFAULT 0,
                    withdrawn_total REAL NOT NULL DEFAULT 0,
                    winnings_total REAL NOT NULL DEFAULT 0,
                    bets_total     INTEGER NOT NULL DEFAULT 0,
                    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bets (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL,
                    game_key      TEXT NOT NULL,
                    bet_type      TEXT NOT NULL,
                    bet_target    TEXT,
                    stake         REAL NOT NULL,
                    multiplier    REAL NOT NULL,
                    result_value  INTEGER NOT NULL,
                    payout        REAL NOT NULL,
                    result        TEXT NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL,
                    direction     TEXT NOT NULL CHECK(direction IN ('deposit','withdraw','reserve_deposit')),
                    amount        REAL NOT NULL,
                    status        TEXT NOT NULL,
                    comment       TEXT,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS section_photos (
                    section_key   TEXT PRIMARY KEY,
                    file_id       TEXT NOT NULL,
                    description   TEXT,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    user_id       INTEGER PRIMARY KEY,
                    username      TEXT,
                    added_by      INTEGER,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code          TEXT PRIMARY KEY,
                    amount        REAL NOT NULL,
                    max_uses      INTEGER NOT NULL DEFAULT 1,
                    used_count    INTEGER NOT NULL DEFAULT 0,
                    created_by    INTEGER NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at    TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_activations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL,
                    promo_code    TEXT NOT NULL,
                    amount        REAL NOT NULL,
                    activated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id),
                    FOREIGN KEY(promo_code) REFERENCES promo_codes(code)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS required_channels (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id    TEXT NOT NULL UNIQUE,
                    channel_name  TEXT,
                    channel_link  TEXT,
                    added_by      INTEGER NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(added_by) REFERENCES admins(user_id)
                )
                """
            )
        self._bootstrap_settings()
        self._ensure_transactions_columns()
        self._ensure_transactions_direction_constraint()
        self._ensure_admins_columns()
        self._ensure_users_blocked_column()
        self._ensure_referral_tables()
        self._update_admin_permissions_migration()
        self._bootstrap_admins()

    def _bootstrap_settings(self) -> None:
        with self._lock, self._conn:
            for key, default_value in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, default_value),
                )

    def _ensure_transactions_columns(self) -> None:
        with self._lock, self._conn:
            cur = self._conn.execute("PRAGMA table_info(transactions)")
            existing_columns = {row[1] for row in cur.fetchall()}
            alterations = {
                "external_id": "ALTER TABLE transactions ADD COLUMN external_id TEXT",
                "external_url": "ALTER TABLE transactions ADD COLUMN external_url TEXT",
                "asset": "ALTER TABLE transactions ADD COLUMN asset TEXT",
                "payload": "ALTER TABLE transactions ADD COLUMN payload TEXT",
                "updated_at": "ALTER TABLE transactions ADD COLUMN updated_at TEXT",
            }
            for column, ddl in alterations.items():
                if column not in existing_columns:
                    logger.info(
                        "Adding missing column '%s' to transactions table", column
                    )
                    self._conn.execute(ddl)
                    existing_columns.add(column)
    
    def _ensure_transactions_direction_constraint(self) -> None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
            )
            row = cur.fetchone()
            if not row:
                return

            table_sql = row[0]
            if not table_sql or "'reserve_deposit'" in table_sql:
                return

            constraint_variants = [
                "CHECK(direction IN ('deposit','withdraw'))",
                "CHECK(direction IN('deposit','withdraw'))",
                "CHECK(direction IN ( 'deposit','withdraw'))",
                "CHECK(direction IN ('deposit', 'withdraw'))",
            ]

            new_sql = None
            for variant in constraint_variants:
                if variant in table_sql:
                    new_sql = table_sql.replace(
                        variant,
                        "CHECK(direction IN ('deposit','withdraw','reserve_deposit'))",
                    )
                    break

            if not new_sql:
                tuple_variants = [
                    "('deposit','withdraw')",
                    "('deposit', 'withdraw')",
                ]
                for variant in tuple_variants:
                    if variant in table_sql:
                        new_sql = table_sql.replace(
                            variant,
                            "('deposit','withdraw','reserve_deposit')",
                        )
                        break

            if not new_sql:
                logger.warning(
                    "Could not locate transactions direction CHECK constraint; skipping migration"
                )
                return

            columns_cur = self._conn.execute("PRAGMA table_info(transactions)")
            column_names = [row_info[1] for row_info in columns_cur.fetchall()]
            if not column_names:
                logger.error(
                    "Unable to read transactions table columns during constraint migration"
                )
                return

            columns_csv = ", ".join(f'"{name}"' for name in column_names)

            logger.info(
                "Updating transactions.direction constraint to allow 'reserve_deposit'"
            )
            self._conn.execute("PRAGMA foreign_keys=off")
            begun = False
            renamed = False
            try:
                self._conn.execute("BEGIN")
                begun = True
                self._conn.execute("ALTER TABLE transactions RENAME TO transactions_old")
                renamed = True
                self._conn.execute(new_sql)
                self._conn.execute(
                    f"INSERT INTO transactions ({columns_csv}) "
                    f"SELECT {columns_csv} FROM transactions_old"
                )
                self._conn.execute("DROP TABLE transactions_old")
                self._conn.execute("COMMIT")
            except Exception:
                if begun:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error as exc:
                        logger.error(
                            "Failed to rollback transactions constraint migration: %s",
                            exc,
                        )
                if renamed:
                    try:
                        self._conn.execute(
                            "ALTER TABLE transactions_old RENAME TO transactions"
                        )
                    except sqlite3.Error as exc:
                        logger.error(
                            "Failed to restore original transactions table after migration failure: %s",
                            exc,
                        )
                raise
            finally:
                self._conn.execute("PRAGMA foreign_keys=on")

    def _ensure_admins_columns(self) -> None:
        with self._lock, self._conn:
            cur = self._conn.execute("PRAGMA table_info(admins)")
            existing_columns = {row[1] for row in cur.fetchall()}
            if "permissions" not in existing_columns:
                logger.info("Adding 'permissions' column to admins table")
                self._conn.execute("ALTER TABLE admins ADD COLUMN permissions TEXT")
    
    def _ensure_users_blocked_column(self) -> None:
        """Add blocked column to users table if it doesn't exist."""
        with self._lock, self._conn:
            cur = self._conn.execute("PRAGMA table_info(users)")
            existing_columns = {row[1] for row in cur.fetchall()}
            if "blocked" not in existing_columns:
                logger.info("Adding 'blocked' column to users table")
                self._conn.execute("ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    
    def _ensure_referral_tables(self) -> None:
        """Create referral tables if they don't exist."""
        with self._lock, self._conn:
            # Add referrer_id and ref_earnings to users if not exist
            cur = self._conn.execute("PRAGMA table_info(users)")
            existing_columns = {row[1] for row in cur.fetchall()}
            if "referrer_id" not in existing_columns:
                logger.info("Adding 'referrer_id' column to users table")
                self._conn.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
            if "ref_earnings" not in existing_columns:
                logger.info("Adding 'ref_earnings' column to users table")
                self._conn.execute("ALTER TABLE users ADD COLUMN ref_earnings REAL NOT NULL DEFAULT 0")
            if "ref_count" not in existing_columns:
                logger.info("Adding 'ref_count' column to users table")
                self._conn.execute("ALTER TABLE users ADD COLUMN ref_count INTEGER NOT NULL DEFAULT 0")
            
            # Create referral_transactions table for tracking referral earnings
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS referral_transactions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id   INTEGER NOT NULL,
                    referred_id   INTEGER NOT NULL,
                    amount        REAL NOT NULL,
                    reason        TEXT NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(referrer_id) REFERENCES users(user_id),
                    FOREIGN KEY(referred_id) REFERENCES users(user_id)
                )
                """
            )
    
    def _update_admin_permissions_migration(self) -> None:
        """Update existing admins to include new permission sections."""
        with self._lock, self._conn:
            # Get all admins with old permission strings
            cur = self._conn.execute("SELECT user_id, permissions FROM admins WHERE permissions IS NOT NULL AND permissions != ''")
            admins_to_update = []
            
            for row in cur.fetchall():
                user_id, perms = row[0], row[1]
                if not perms:
                    continue
                
                # Check if permissions are missing the new sections
                perm_list = perms.split(',')
                needs_update = False
                
                # Add missing sections
                if 'crypto_checks' not in perm_list:
                    perm_list.append('crypto_checks')
                    needs_update = True
                if 'top_balance' not in perm_list:
                    perm_list.append('top_balance')
                    needs_update = True
                if 'required_channels' not in perm_list:
                    perm_list.append('required_channels')
                    needs_update = True
                if VIP_FEATURES_ENABLED and 'mines_chance' not in perm_list:
                    perm_list.append('mines_chance')
                    needs_update = True
                
                if needs_update:
                    new_perms = ','.join(perm_list)
                    admins_to_update.append((new_perms, user_id))
            
            # Update admins in batch
            if admins_to_update:
                logger.info("Updating permissions for %d admins to include new sections", len(admins_to_update))
                self._conn.executemany(
                    "UPDATE admins SET permissions = ? WHERE user_id = ?",
                    admins_to_update
                )

    def _bootstrap_admins(self) -> None:
        """Add initial admins from ADMIN_IDS to database."""
        with self._lock, self._conn:
            for admin_id in ADMIN_IDS:
                self._conn.execute(
                    "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
                    (admin_id,)
                )
    
    def get_all_admins(self) -> List[sqlite3.Row]:
        """Get all admins from database."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM admins ORDER BY created_at")
            return cur.fetchall()
    
    def add_admin(self, user_id: int, username: Optional[str] = None, added_by: Optional[int] = None, permissions: Optional[str] = None) -> None:
        """Add a new admin to database."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO admins (user_id, username, added_by, permissions) VALUES (?, ?, ?, ?)",
                (user_id, username, added_by, permissions)
            )
    
    def remove_admin(self, user_id: int) -> bool:
        """Remove an admin from database. Returns True if removed, False if not found."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            return cur.rowcount > 0
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is an admin."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            return cur.fetchone() is not None
    
    def get_admin_permissions(self, user_id: int) -> List[str]:
        """Get admin permissions. Returns list of allowed sections or all if None."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT permissions FROM admins WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                # Если permissions не установлен, возвращаем все разделы
                base_permissions = [
                    "financial",
                    "multipliers",
                    "links",
                    "design",
                    "manage_admins",
                    "balance_management",
                    "reserve",
                    "crypto_checks",
                    "stats",
                    "top_balance",
                    "reviews",
                    "test_dice",
                    "broadcast",
                    "promo_codes",
                    "required_channels",
                ]
                if VIP_FEATURES_ENABLED:
                    base_permissions.append("mines_chance")
                return base_permissions
            return row[0].split(",")
    
    def update_admin_permissions(self, user_id: int, permissions: str) -> None:
        """Update admin permissions."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE admins SET permissions = ? WHERE user_id = ?",
                (permissions, user_id)
            )
    
    def set_section_photo(self, section_key: str, file_id: str, description: Optional[str] = None) -> None:
        """Set or update a photo for a section."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO section_photos (section_key, file_id, description) VALUES (?, ?, ?)",
                (section_key, file_id, description)
            )
    
    def get_section_photo(self, section_key: str) -> Optional[sqlite3.Row]:
        """Get photo for a section."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM section_photos WHERE section_key = ?", (section_key,))
            return cur.fetchone()
    
    def remove_section_photo(self, section_key: str) -> bool:
        """Remove photo for a section. Returns True if removed, False if not found."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM section_photos WHERE section_key = ?", (section_key,))
            return cur.rowcount > 0
    
    def get_all_section_photos(self) -> List[sqlite3.Row]:
        """Get all section photos."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM section_photos ORDER BY section_key")
            return cur.fetchall()
    
    # Promo code methods
    def create_promo_code(self, code: str, amount: Decimal, max_uses: int, created_by: int, expires_at: Optional[str] = None) -> None:
        """Create a new promo code."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO promo_codes (code, amount, max_uses, created_by, expires_at) VALUES (?, ?, ?, ?, ?)",
                (code, float(amount), max_uses, created_by, expires_at)
            )
    
    def get_promo_code(self, code: str) -> Optional[sqlite3.Row]:
        """Get promo code by code."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM promo_codes WHERE code = ?", (code,))
            return cur.fetchone()
    
    def get_all_promo_codes(self) -> List[sqlite3.Row]:
        """Get all promo codes."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
            return cur.fetchall()
    
    def delete_promo_code(self, code: str) -> bool:
        """Delete promo code. Returns True if deleted, False if not found."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM promo_codes WHERE code = ?", (code,))
            return cur.rowcount > 0
    
    def activate_promo_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        """
        Activate promo code for user.
        Returns (success: bool, message: str)
        """
        with self._lock, self._conn:
            # Check if promo code exists
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM promo_codes WHERE code = ?", (code,))
            promo = cur.fetchone()
            
            if not promo:
                return False, "Промокод не найден"
            
            # Check if expired
            if promo["expires_at"]:
                try:
                    expires_at = datetime.fromisoformat(promo["expires_at"])
                    if datetime.now(UTC) > expires_at:
                        return False, "Промокод истёк"
                except ValueError:
                    pass
            
            # Check if max uses reached
            if promo["used_count"] >= promo["max_uses"]:
                return False, "Промокод уже использован максимальное количество раз"
            
            # Check if user already used this promo
            cur.execute(
                "SELECT 1 FROM promo_activations WHERE user_id = ? AND promo_code = ?",
                (user_id, code)
            )
            if cur.fetchone():
                return False, "Вы уже использовали этот промокод"
            
            # Activate promo code
            amount = Decimal(str(promo["amount"]))
            
            # Add balance to user
            self._conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (float(amount), user_id)
            )
            
            # Increment used count
            self._conn.execute(
                "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
                (code,)
            )
            
            # Record activation
            self._conn.execute(
                "INSERT INTO promo_activations (user_id, promo_code, amount) VALUES (?, ?, ?)",
                (user_id, code, float(amount))
            )
            
            return True, f"Промокод активирован! На ваш баланс зачислено {amount} $"
    
    def get_user_promo_activations(self, user_id: int) -> List[sqlite3.Row]:
        """Get all promo activations for a user."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM promo_activations WHERE user_id = ? ORDER BY activated_at DESC",
                (user_id,)
            )
            return cur.fetchall()
    
    def add_required_channel(self, channel_id: str, channel_name: str, channel_link: str, added_by: int) -> Tuple[bool, str]:
        """
        Add a required channel.
        Returns (success: bool, message: str)
        """
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO required_channels (channel_id, channel_name, channel_link, added_by) VALUES (?, ?, ?, ?)",
                    (channel_id, channel_name, channel_link, added_by)
                )
                return True, f"✅ Канал {channel_name} добавлен в обязательные подписки"
            except sqlite3.IntegrityError:
                return False, "❌ Этот канал уже добавлен в обязательные подписки"
    
    def remove_required_channel(self, channel_id: str) -> bool:
        """Remove a required channel. Returns True if removed, False if not found."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM required_channels WHERE channel_id = ?", (channel_id,))
            return cur.rowcount > 0
    
    def get_all_required_channels(self) -> List[sqlite3.Row]:
        """Get all required channels."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM required_channels ORDER BY created_at")
            return cur.fetchall()
    
    def get_required_channel(self, channel_id: str) -> Optional[sqlite3.Row]:
        """Get a required channel by ID."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM required_channels WHERE channel_id = ?", (channel_id,))
            return cur.fetchone()

    def ensure_user(self, telegram_user: telebot.types.User) -> sqlite3.Row:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO users (user_id, username, first_name)
                VALUES (?, ?, ?)
                """,
                (telegram_user.id, telegram_user.username, telegram_user.first_name),
            )
            self._conn.execute(
                "UPDATE users SET username = ?, first_name = ?, last_seen = ? WHERE user_id = ?",
                (
                    telegram_user.username,
                    telegram_user.first_name,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    telegram_user.id,
                ),
            )
        return self.get_user(telegram_user.id)

    def get_user(self, user_id: int) -> sqlite3.Row:
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"User {user_id} not found")
        return row

    def update_user_balance(
        self,
        user_id: int,
        *,
        delta_balance: Decimal = Decimal("0"),
        delta_deposit: Decimal = Decimal("0"),
        delta_withdraw: Decimal = Decimal("0"),
        delta_winnings: Decimal = Decimal("0"),
        delta_bets: int = 0,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE users
                SET balance = balance + ?,
                    deposited_total = deposited_total + ?,
                    withdrawn_total = withdrawn_total + ?,
                    winnings_total = winnings_total + ?,
                    bets_total = bets_total + ?,
                    last_seen = ?
                WHERE user_id = ?
                """,
                (
                    float(delta_balance),
                    float(delta_deposit),
                    float(delta_withdraw),
                    float(delta_winnings),
                    delta_bets,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    user_id,
                ),
            )

    def record_bet(
        self,
        *,
        user_id: int,
        game_key: str,
        bet_type: str,
        bet_target: Optional[str],
        stake: Decimal,
        multiplier: Decimal,
        result_value: int,
        payout: Decimal,
        result: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO bets (
                    user_id, game_key, bet_type, bet_target, stake,
                    multiplier, result_value, payout, result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    game_key,
                    bet_type,
                    bet_target,
                    float(stake),
                    float(multiplier),
                    result_value,
                    float(payout),
                    result,
                ),
            )

    def create_transaction(
        self,
        user_id: int,
        direction: str,
        amount: Decimal,
        status: str,
        comment: Optional[str] = None,
        *,
        asset: Optional[str] = None,
        external_id: Optional[str] = None,
        external_url: Optional[str] = None,
        payload: Optional[str] = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO transactions (user_id, direction, amount, status, comment)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, direction, float(amount), status, comment),
            )
            transaction_id = int(cur.lastrowid)

        try:
            self.update_transaction(
                transaction_id,
                asset=asset,
                external_id=external_id,
                external_url=external_url,
                payload=payload,
                skip_timestamp=True,
            )
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc).lower():
                logger.warning(
                    "Transactions table missing expected columns (%s). Attempting schema sync...",
                    exc,
                )
                self._ensure_transactions_columns()
                try:
                    self.update_transaction(
                        transaction_id,
                        asset=asset,
                        external_id=external_id,
                        external_url=external_url,
                        payload=payload,
                        skip_timestamp=True,
                    )
                except sqlite3.Error as retry_exc:  # pragma: no cover - diagnostic safeguard
                    logger.error(
                        "Failed to update transaction %s after schema sync: %s",
                        transaction_id,
                        retry_exc,
                        exc_info=True,
                    )
            else:
                raise

        return transaction_id

    def update_transaction(
        self,
        transaction_id: int,
        *,
        skip_timestamp: bool = False,
        **fields: Any,
    ) -> None:
        if not fields and skip_timestamp:
            return

        if not skip_timestamp:
            fields["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        else:
            # Ensure we do not leave updated_at null when other updates happen later
            if fields:
                fields.setdefault("updated_at", datetime.now(UTC).isoformat(timespec="seconds"))

        assignments = [f"{column} = ?" for column in fields]
        values = [fields[column] for column in fields]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE transactions SET {', '.join(assignments)} WHERE id = ?",
                (*values, transaction_id),
            )

    def get_transaction(self, transaction_id: int) -> sqlite3.Row:
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"Transaction {transaction_id} not found")
        return row

    def count_withdrawals_today(self, user_id: int) -> int:
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM transactions
                WHERE user_id = ?
                  AND direction = 'withdraw'
                    AND status NOT IN ('cancelled', 'failed')
                  AND DATE(created_at, 'localtime') = DATE('now', 'localtime')
                """,
                (user_id,),
            )
            count = cur.fetchone()[0]
        return int(count)

    def get_bot_stats(self) -> Dict[str, Decimal | int]:
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM users
                WHERE DATE(created_at, 'localtime') = DATE('now', 'localtime')
                """
            )
            new_today = cur.fetchone()[0]

            cur.execute(
                "SELECT IFNULL(SUM(amount),0) FROM transactions WHERE direction='deposit' AND status='completed'"
            )
            total_deposits = cur.fetchone()[0]

            cur.execute(
                "SELECT IFNULL(SUM(amount),0) FROM transactions WHERE direction='withdraw' AND status IN ('processing','completed')"
            )
            total_withdrawals = cur.fetchone()[0]

        return {
            "total_users": int(total_users),
            "new_today": int(new_today),
            "total_deposits": Decimal(str(total_deposits)),
            "total_withdrawals": Decimal(str(total_withdrawals)),
        }
    
    def get_all_user_ids(self) -> List[int]:
        """Get all user IDs for broadcast."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT user_id FROM users ORDER BY user_id")
            return [row[0] for row in cur.fetchall()]

    def get_bet_profit_stats(self) -> Dict[str, Dict[str, Decimal | int]]:
        """Aggregate bet statistics grouped by game."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                SELECT
                    game_key,
                    COUNT(*) AS total_bets,
                    SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS wins_count,
                    SUM(CASE WHEN result='lose' THEN 1 ELSE 0 END) AS losses_count,
                    SUM(stake) AS total_stake,
                    SUM(CASE WHEN result='win' THEN payout ELSE 0 END) AS total_wins_amount,
                    SUM(CASE WHEN result='lose' THEN stake ELSE 0 END) AS total_losses_amount
                FROM bets
                GROUP BY game_key
                """
            )
            rows = cur.fetchall()

        stats: Dict[str, Dict[str, Decimal | int]] = {}
        for row in rows:
            game_key = row["game_key"]
            stats[game_key] = {
                "total_bets": int(row["total_bets"] or 0),
                "wins_count": int(row["wins_count"] or 0),
                "losses_count": int(row["losses_count"] or 0),
                "total_stake": Decimal(str(row["total_stake"] or 0)),
                "total_wins_amount": Decimal(str(row["total_wins_amount"] or 0)),
                "total_losses_amount": Decimal(str(row["total_losses_amount"] or 0)),
            }
        return stats

    def block_user(self, user_id: int) -> None:
        """Block a user."""
        with self._lock, self._conn:
            self._conn.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
    
    def unblock_user(self, user_id: int) -> None:
        """Unblock a user."""
        with self._lock, self._conn:
            self._conn.execute("UPDATE users SET blocked = 0 WHERE user_id = ?", (user_id,))
    
    def is_user_blocked(self, user_id: int) -> bool:
        """Check if user is blocked."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if not row:
                return False
            return bool(row[0])
    
    def set_user_referrer(self, user_id: int, referrer_id: int) -> None:
        """Set referrer for a user."""
        with self._lock, self._conn:
            self._conn.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer_id, user_id))
            # Increment referrer's ref_count
            self._conn.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?", (referrer_id,))
    
    def add_referral_earning(self, referrer_id: int, referred_id: int, amount: Decimal, reason: str) -> None:
        """Add referral earning to referrer."""
        with self._lock, self._conn:
            # Add to referrer's balance and ref_earnings
            self._conn.execute(
                "UPDATE users SET balance = balance + ?, ref_earnings = ref_earnings + ? WHERE user_id = ?",
                (float(amount), float(amount), referrer_id)
            )
            # Record transaction
            self._conn.execute(
                "INSERT INTO referral_transactions (referrer_id, referred_id, amount, reason) VALUES (?, ?, ?, ?)",
                (referrer_id, referred_id, float(amount), reason)
            )
    
    def get_user_referrals(self, user_id: int) -> List[sqlite3.Row]:
        """Get all referrals for a user."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM users WHERE referrer_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return cur.fetchall()
    
    def get_top_players_by_games(self, limit: int = 5) -> List[sqlite3.Row]:
        """Get top players by number of games played."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM users WHERE bets_total > 0 ORDER BY bets_total DESC LIMIT ?",
                (limit,)
            )
            return cur.fetchall()
    
    def get_top_users_by_balance(self, limit: int = 20) -> List[sqlite3.Row]:
        """Get top users by balance."""
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM users ORDER BY balance DESC LIMIT ?",
                (limit,)
            )
            return cur.fetchall()
    
    def reset_all_stats(self) -> tuple[int, int, int]:
        """
        Reset all user statistics and clear game history.
        Returns (users_reset, bets_deleted, transactions_deleted)
        """
        with self._lock, self._conn:
            # Count before deletion
            cursor = self._conn.cursor()
            
            # Count users that will be reset
            cursor.execute("SELECT COUNT(*) FROM users WHERE balance != 0 OR bets_total != 0 OR winnings_total != 0")
            users_count = cursor.fetchone()[0]
            
            # Count bets to delete
            cursor.execute("SELECT COUNT(*) FROM bets")
            bets_count = cursor.fetchone()[0]
            
            # Count transactions to delete
            cursor.execute("SELECT COUNT(*) FROM transactions")
            transactions_count = cursor.fetchone()[0]
            
            # Reset all user stats (except user_id, username, first_name, created_at, last_seen)
            self._conn.execute(
                """
                UPDATE users SET
                    balance = 0,
                    deposited_total = 0,
                    withdrawn_total = 0,
                    winnings_total = 0,
                    bets_total = 0,
                    ref_earnings = 0
                """
            )
            
            # Delete all bets
            self._conn.execute("DELETE FROM bets")
            
            # Delete all transactions
            self._conn.execute("DELETE FROM transactions")
            
            # Delete all referral transactions
            self._conn.execute("DELETE FROM referral_transactions")
            
            logger.info(f"Stats reset: {users_count} users, {bets_count} bets deleted, {transactions_count} transactions deleted")
            
            return users_count, bets_count, transactions_count

    def get_settings(self) -> Dict[str, str]:
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT key, value FROM settings")
            return {row[0]: row[1] for row in cur.fetchall()}

    def get_setting(self, key: str) -> str:
        with self._lock, closing(self._conn.cursor()) as cur:
            cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def resolve_crypto_pay_token(database: Database, env_token: str) -> str:
    try:
        stored_token = database.get_setting("crypto_pay_api_token").strip()
    except KeyError:
        stored_token = ""
    if env_token and not stored_token:
        database.set_setting("crypto_pay_api_token", env_token)
        return env_token
    return stored_token or env_token


@dataclass
class PendingState:
    state: str
    payload: Dict[str, Any]


class StateManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._storage: Dict[int, PendingState] = {}

    def set(self, user_id: int, state: str, **payload: Any) -> None:
        with self._lock:
            self._storage[user_id] = PendingState(state=state, payload=payload)
            logger.info("StateManager.set() - user %s -> state '%s', payload keys: %s", 
                       user_id, state, list(payload.keys()))

    def pop(self, user_id: int) -> Optional[PendingState]:
        with self._lock:
            result = self._storage.pop(user_id, None)
            if result:
                logger.info("StateManager.pop() - user %s had state '%s' (cleared)", user_id, result.state)
            else:
                logger.info("StateManager.pop() - user %s had NO state", user_id)
            return result

    def peek(self, user_id: int) -> Optional[PendingState]:
        with self._lock:
            result = self._storage.get(user_id)
            if result:
                logger.debug("StateManager.peek() - user %s has state '%s'", user_id, result.state)
            else:
                logger.debug("StateManager.peek() - user %s has NO state", user_id)
            return result


def get_configured_mines_safe_chance(
    settings_override: Optional[Dict[str, str]] = None,
) -> Optional[float]:
    """
    Returns normalized probability (0..1) for VIP-controlled mines safe chance.
    """
    if not VIP_FEATURES_ENABLED:
        return None
    settings = settings_override or db.get_settings()
    raw_value = (settings.get("mines_safe_chance") or "").strip()
    if not raw_value:
        return None
    try:
        numeric_value = float(raw_value.replace(",", "."))
    except ValueError:
        return None
    numeric_value = max(0.0, min(numeric_value, 100.0))
    if numeric_value <= 0:
        return None
    return numeric_value / 100.0


@dataclass
class MinesSession:
    session_id: str
    user_id: int
    chat_id: int
    stake: Decimal
    base_multiplier: Decimal
    mine_count: int
    bet_type: str
    target: str
    target_label: str
    multiplier_key: Optional[str]
    mine_positions: Set[int] = field(default_factory=set)
    revealed: Dict[int, int] = field(default_factory=dict)
    safe_steps: int = 0
    current_multiplier: Decimal = Decimal("1.00")
    current_payout: Decimal = Decimal("0.00")
    board_message_id: Optional[int] = None
    board_uses_photo: bool = False
    is_active: bool = True
    created_at: float = field(default_factory=time.time)
    play_panel_message_id: Optional[int] = None
    safe_chance: Optional[float] = None


MINES_BOARD_SIZE = 5
MINES_TOTAL_CELLS = MINES_BOARD_SIZE * MINES_BOARD_SIZE
MINES_NOOP_CALLBACK = "mines:noop"

mines_lock = threading.RLock()
mines_sessions: Dict[str, MinesSession] = {}
mines_sessions_by_user: Dict[int, str] = {}


def register_mines_session(session: MinesSession) -> None:
    with mines_lock:
        mines_sessions[session.session_id] = session
        mines_sessions_by_user[session.user_id] = session.session_id


def get_mines_session(session_id: str) -> Optional[MinesSession]:
    with mines_lock:
        return mines_sessions.get(session_id)


def get_mines_session_for_user(user_id: int) -> Optional[MinesSession]:
    with mines_lock:
        session_id = mines_sessions_by_user.get(user_id)
        if not session_id:
            return None
        return mines_sessions.get(session_id)


def remove_mines_session(session: MinesSession) -> None:
    with mines_lock:
        mines_sessions.pop(session.session_id, None)
        registered_session_id = mines_sessions_by_user.get(session.user_id)
        if registered_session_id == session.session_id:
            mines_sessions_by_user.pop(session.user_id, None)


def format_multiplier_value(multiplier: Decimal) -> str:
    return f"{multiplier.quantize(Decimal('0.01'), rounding=ROUND_DOWN):.2f}"


def build_mines_caption(session: MinesSession, final_message: Optional[str] = None) -> str:
    total_safe = MINES_TOTAL_CELLS - session.mine_count
    lines = [
        "💣 <b>Игра «Мины»</b>",
        f"Ставка: <b>{format_money(session.stake)} $</b>",
        f"Мин: <b>{session.mine_count}</b> • шаг <b>x{format_multiplier_value(session.base_multiplier)}</b>",
        f"Открыто безопасных клеток: <b>{session.safe_steps}</b> / {total_safe}",
        f"Текущий множитель: <b>x{format_multiplier_value(session.current_multiplier)}</b>",
        f"Потенциал: <b>{format_money(session.current_payout)} $</b>",
    ]
    if session.is_active:
        if session.safe_steps:
            lines.append("Вы можете забрать выигрыш кнопкой ниже.")
        else:
            lines.append("Выберите первую клетку, чтобы увеличить ставку.")
    if final_message:
        lines.append("")
        lines.append(final_message)
    return "\n".join(lines)


def build_mines_markup(
    session: MinesSession,
    *,
    reveal_all: bool = False,
    highlight_mine: Optional[int] = None,
) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=MINES_BOARD_SIZE)
    buttons: List[types.InlineKeyboardButton] = []
    for idx in range(MINES_TOTAL_CELLS):
        if reveal_all:
            if idx in session.mine_positions:
                label = "💥" if highlight_mine == idx else "💣"
            elif idx in session.revealed:
                label = "💸"
            else:
                label = "▫️"
            callback_data = MINES_NOOP_CALLBACK
        else:
            if idx in session.revealed:
                label = "💸"
                callback_data = MINES_NOOP_CALLBACK
            else:
                label = "▫️"
                callback_data = (
                    f"mines:o:{session.session_id}:{idx}"
                    if session.is_active
                    else MINES_NOOP_CALLBACK
                )
        buttons.append(types.InlineKeyboardButton(label, callback_data=callback_data))
    for row_start in range(0, len(buttons), MINES_BOARD_SIZE):
        markup.row(*buttons[row_start : row_start + MINES_BOARD_SIZE])
    if session.is_active and session.safe_steps > 0 and not reveal_all:
        cashout_label = (
            f"💰 Забрать {format_money(session.current_payout)} $ "
            f"(x{format_multiplier_value(session.current_multiplier)})"
        )
        markup.add(
            types.InlineKeyboardButton(
                cashout_label,
                callback_data=f"mines:c:{session.session_id}",
            )
        )
    return markup


def update_mines_message(
    session: MinesSession,
    caption: str,
    markup: types.InlineKeyboardMarkup,
) -> None:
    if session.board_message_id is None:
        return
    try:
        if session.board_uses_photo:
            bot.edit_message_caption(
                chat_id=session.chat_id,
                message_id=session.board_message_id,
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
        else:
            bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.board_message_id,
                text=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
    except ApiException as exc:
        logger.debug(
            "Failed to update mines board message %s: %s",
            session.board_message_id,
            exc,
        )


def restore_play_panel_for_session(session: MinesSession) -> None:
    if not session.play_panel_message_id:
        return
    try:
        user_row = db.get_user(session.user_id)
        settings = db.get_settings()
        overview_text = build_play_overview_text(user_row, settings)
        play_markup = build_play_keyboard(settings)
        bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=int(session.play_panel_message_id),
            text=overview_text,
            reply_markup=play_markup,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Failed to restore play panel for mines session %s: %s",
            session.session_id,
            exc,
        )


MEDIA_CONTENT_TYPES = {
    "photo",
    "video",
    "animation",
    "document",
    "audio",
    "voice",
    "video_note",
}


def message_has_media(message: types.Message) -> bool:
    content_type = getattr(message, "content_type", None)
    if content_type in MEDIA_CONTENT_TYPES:
        return True
    for attr in ("photo", "video", "animation", "document", "audio", "voice", "video_note"):
        media = getattr(message, attr, None)
        if media:
            if isinstance(media, list):
                if len(media) > 0:
                    return True
            else:
                return True
    return False


def edit_message_html(
    message: types.Message,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> None:
    kwargs = {"reply_markup": reply_markup, "parse_mode": "HTML"}
    try:
        if message_has_media(message):
            bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.message_id,
                caption=text,
                **kwargs,
            )
        else:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=text,
                **kwargs,
            )
    except ApiException as exc:
        logger.debug(
            "edit_message_html fallback for message %s in chat %s: %s",
            message.message_id,
            message.chat.id,
            exc,
        )
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


logger.info("Initializing database at %s", DATABASE_PATH)
db = Database(DATABASE_PATH)
logger.info("Database initialized successfully")

logger.info("Resolving Crypto Pay token...")
crypto_pay_token = resolve_crypto_pay_token(db, CRYPTOPAY_API_TOKEN)
logger.info("Crypto Pay token resolved: %s", "SET" if crypto_pay_token else "NOT_SET")

logger.info("Creating CryptoPayClient with base_url=%s, timeout=%s", CRYPTOPAY_BASE_URL, CRYPTOPAY_TIMEOUT)
crypto_pay_client = CryptoPayClient(
    token=crypto_pay_token,
    base_url=CRYPTOPAY_BASE_URL,
    timeout=CRYPTOPAY_TIMEOUT,
)
logger.info("CryptoPayClient created, is_configured=%s", crypto_pay_client.is_configured)

if crypto_pay_client.is_configured:
    logger.info("✅ Crypto Pay client configured; deposit and withdraw flows enabled")
else:
    logger.warning("⚠️ Crypto Pay client token not configured; deposit and withdraw flows disabled")

logger.info("Creating Telegram bot instance...")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logger.info("Telegram bot created successfully")

logger.info("Creating StateManager...")
states = StateManager()
logger.info("StateManager created successfully")

logger.info("=== BOT INITIALIZATION COMPLETE ===")


def decimal_from_text(text: str) -> Decimal:
    cleaned = text.strip().replace(",", ".")
    return Decimal(cleaned)


def multiplier_setting_key(game_key: str, bet_type: str, target_key: Optional[str] = None) -> str:
    if target_key:
        return f"{game_key}_{bet_type}_multiplier_{target_key}"
    return f"{game_key}_multiplier_{bet_type}"


def resolve_multiplier(
    settings: Dict[str, str],
    game_key: str,
    bet_type: str,
    *,
    target_key: Optional[str] = None,
    explicit_key: Optional[str] = None,
    fallback_value: Optional[str] = None,
) -> Tuple[Decimal, str]:
    candidates = []
    if explicit_key:
        candidates.append(explicit_key)
    if target_key:
        candidates.append(multiplier_setting_key(game_key, bet_type, target_key))
    candidates.append(multiplier_setting_key(game_key, bet_type))

    for index, key in enumerate(candidates):
        if not key:
            continue
        value = settings.get(key)
        if value is None:
            value = DEFAULT_SETTINGS.get(key)
        if value is None and index == 0 and fallback_value is not None:
            value = fallback_value
        if value is None:
            continue
        try:
            multiplier = Decimal(str(value))
        except InvalidOperation:
            continue
        capped = min(multiplier, Decimal("2.0"))
        return capped, key

    # Fallback to default safe multiplier when nothing configured
    return Decimal("1.50"), candidates[0] if candidates else ""


def get_multiplier(
    settings: Dict[str, str],
    game_key: str,
    bet_type: str,
    target_key: Optional[str] = None,
) -> Decimal:
    multiplier, _ = resolve_multiplier(settings, game_key, bet_type, target_key=target_key)
    return multiplier


def get_option_multiplier(
    settings: Dict[str, str],
    game_key: str,
    bet_type: str,
    option: Dict[str, Any],
) -> Tuple[Decimal, str]:
    target_key = option.get("key")
    explicit_key = option.get("multiplier_key")
    fallback_value = option.get("default_multiplier")
    fallback = None
    if fallback_value is not None:
        fallback = str(fallback_value)
    return resolve_multiplier(
        settings,
        game_key,
        bet_type,
        target_key=target_key,
        explicit_key=explicit_key,
        fallback_value=fallback,
    )


def collect_option_multipliers(
    settings: Dict[str, str],
    game_key: str,
    bet_type: str,
    bet_config: Dict[str, Any],
) -> List[Decimal]:
    values: List[Decimal] = []
    for option in bet_config.get("targets") or []:
        if not option.get("key"):
            continue
        multiplier, _ = get_option_multiplier(settings, game_key, bet_type, option)
        values.append(multiplier)
        if (
            game_key == "darts"
            and bet_type == "outcome"
            and option.get("key") == "hit"
        ):
            if DARTS_BULLSEYE_MULTIPLIER not in values:
                values.append(DARTS_BULLSEYE_MULTIPLIER)
    if values:
        return values
    return [get_multiplier(settings, game_key, bet_type)]


def build_main_menu() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🎮 Играть"))
    markup.row(
        types.KeyboardButton("👤 Личный кабинет"),
        types.KeyboardButton("ℹ️ О боте"),
    )
    return markup


def build_play_keyboard(settings: Optional[Dict[str, str]] = None) -> types.InlineKeyboardMarkup:
    if settings is None:
        settings = db.get_settings()
    markup = types.InlineKeyboardMarkup(row_width=2)
    game_buttons: List[Tuple[Decimal, types.InlineKeyboardButton]] = []
    for game_key, rules in GAME_RULES.items():
        bet_types = get_bet_types(game_key)
        if not bet_types:
            continue
        multipliers: List[Decimal] = []
        for bet_key, bet_config in bet_types.items():
            multipliers.extend(
                collect_option_multipliers(settings, game_key, bet_key, bet_config)
            )
        if not multipliers:
            continue
        peak = max(multipliers)
        button_text = f"{rules['emoji']} {rules['label']} • до x{peak:.2f}"
        game_buttons.append(
            (peak, types.InlineKeyboardButton(button_text, callback_data=f"game:{game_key}"))
        )
    if game_buttons:
        sorted_buttons = [button for _, button in sorted(game_buttons, key=lambda item: item[0], reverse=True)]
        markup.add(*sorted_buttons)
    return markup


def build_bet_type_keyboard(game_key: str, settings: Dict[str, str]) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    bet_types = get_bet_types(game_key)
    bet_buttons: List[Tuple[Decimal, types.InlineKeyboardButton]] = []
    for bet_key, bet_config in bet_types.items():
        title = bet_config.get("title", bet_key)
        multipliers = collect_option_multipliers(settings, game_key, bet_key, bet_config)
        if not multipliers:
            continue
        peak = max(multipliers)
        bet_buttons.append(
            (
                peak,
                types.InlineKeyboardButton(
                    f"{title} • до x{peak:.2f}", callback_data=f"bet:{game_key}:{bet_key}"
                ),
            )
        )
    sorted_buttons = [button for _, button in sorted(bet_buttons, key=lambda item: item[0], reverse=True)]
    if sorted_buttons:
        markup.add(*sorted_buttons)
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="play:home"))
    return markup


def build_number_keyboard(game_key: str) -> types.InlineKeyboardMarkup:
    rules = GAME_RULES[game_key]
    min_value = rules["min_value"]
    max_value = rules["max_value"]
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(
            str(value), callback_data=f"target:{game_key}:number:{value}"
        )
        for value in range(min_value, max_value + 1)
    ]
    markup.add(*buttons)
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"game:{game_key}"))
    return markup


def build_choice_keyboard(
    game_key: str, bet_type: str, bet_config: Dict[str, Any]
) -> types.InlineKeyboardMarkup:
    targets = bet_config.get("targets") or []
    row_width = bet_config.get("row_width", 2)
    markup = types.InlineKeyboardMarkup(row_width=row_width)
    buttons = [
        types.InlineKeyboardButton(
            option.get("label", option.get("key", "?")),
            callback_data=f"target:{game_key}:{bet_type}:{option.get('key')}",
        )
        for option in targets
        if option.get("key") and not option.get("hidden")
    ]
    if buttons:
        for idx in range(0, len(buttons), row_width):
            markup.row(*buttons[idx : idx + row_width])
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"game:{game_key}"))
    return markup


def build_personal_cabinet_view(user: sqlite3.Row) -> tuple[str, types.InlineKeyboardMarkup]:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("▶️ Играть", callback_data="play:home"))
    markup.add(
        types.InlineKeyboardButton("💳 Пополнить", callback_data="wallet:deposit"),
        types.InlineKeyboardButton("💸 Вывести", callback_data="wallet:withdraw"),
    )
    markup.add(types.InlineKeyboardButton("💼 Партнерская программа", callback_data="show_referral"))
    balance = format_money(row_decimal(user, "balance"))
    deposited = format_money(row_decimal(user, "deposited_total"))
    withdrawn = format_money(row_decimal(user, "withdrawn_total"))
    winnings = format_money(row_decimal(user, "winnings_total"))
    bets_total = int(user["bets_total"] or 0)
    username = user["username"] or ""
    username_display = f"@{username}" if username else "—"
    text = (
        "<b>👤 Личный кабинет</b>\n"
        f"ID: <code>{user['user_id']}</code> | {username_display}\n\n"
        "<b>💼 Баланс</b>\n"
        f"• Текущий баланс: <b>{balance} $</b>\n"
        f"• Выигрыш всего: {winnings} $\n"
        f"• Пополнено / Выведено: {deposited} $ / {withdrawn} $\n\n"
        "<b>📊 Активность</b>\n"
        f"• Ставок сделано: {bets_total}\n"
        "<blockquote>💡 Играй, чтобы закрепить серию побед!</blockquote>"
    )
    return text, markup


def send_personal_cabinet(message: types.Message) -> None:
    user = db.ensure_user(message.from_user)
    
    # Check subscription before allowing access
    if not check_and_enforce_subscription(user["user_id"], message.chat.id):
        return
    
    text, markup = build_personal_cabinet_view(user)
    
    # Проверяем наличие фото для раздела "Личный кабинет"
    photo = db.get_section_photo("cabinet")
    if photo:
        try:
            bot.send_photo(message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for cabinet (invalid file_id): {e}")
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def refresh_personal_cabinet(call: types.CallbackQuery) -> None:
    render_personal_cabinet_inline(
        call.message.chat.id,
        call.message.message_id,
        call.from_user,
        message=call.message,
    )


def render_personal_cabinet_inline(
    chat_id: int,
    message_id: int,
    telegram_user: types.User,
    *,
    message: Optional[types.Message] = None,
) -> None:
    user = db.ensure_user(telegram_user)
    text, markup = build_personal_cabinet_view(user)
    
    # Проверяем наличие фото для раздела "Личный кабинет"
    photo = db.get_section_photo("cabinet")
    
    if message is not None:
        if message_has_media(message) and photo:
            try:
                bot.edit_message_media(
                    media=types.InputMediaPhoto(
                        media=photo["file_id"],
                        caption=text,
                        parse_mode="HTML",
                    ),
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=markup,
                )
                return
            except ApiException as exc:
                logger.debug("Failed to update cabinet media: %s", exc)
        
        try:
            edit_message_html(message, text, reply_markup=markup)
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to edit cabinet inline message: %s", exc)
    
    if photo:
        try:
            bot.edit_message_media(
                media=types.InputMediaPhoto(
                    media=photo["file_id"],
                    caption=text,
                    parse_mode="HTML",
                ),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=markup,
            )
            return
        except ApiException as exc:
            logger.debug("Failed to update cabinet media by id: %s", exc)
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML",
        )
    except ApiException as exc:
        logger.debug("Failed to edit personal cabinet by id: %s", exc)
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def build_about_view() -> tuple[str, types.InlineKeyboardMarkup]:
    """Builds the about section view."""
    settings = db.get_settings()
    stats = db.get_bot_stats()
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🎮 Играть", callback_data="play:home"))
    markup.add(types.InlineKeyboardButton("🏆 ТОП игроков", callback_data="show_top"))
    buttons: List[types.InlineKeyboardButton] = []
    if settings.get("chat_link"):
        buttons.append(types.InlineKeyboardButton("💬 Чат", url=settings["chat_link"]))
    if settings.get("channel_link"):
        buttons.append(types.InlineKeyboardButton("📢 Канал", url=settings["channel_link"]))
    if settings.get("big_win_link"):
        buttons.append(
            types.InlineKeyboardButton("🏆 Выигрыши", url=settings["big_win_link"])
        )
    if settings.get("reviews_link"):
        buttons.append(types.InlineKeyboardButton("⭐️ Отзывы", url=settings["reviews_link"]))
    if buttons:
        markup.add(*buttons)
    if CREATOR_BRANDING_ENABLED and CREATOR_CONTACT_URL:
        markup.add(
            types.InlineKeyboardButton(
                CREATOR_CONTACT_BUTTON_LABEL,
                url=CREATOR_CONTACT_URL,
            )
        )

    text = (
        "<b>ℹ️ О боте</b>\n"
        "<blockquote>Статистика обновляется в реальном времени.</blockquote>\n\n"
        "<b>📊 Статистика</b>\n"
        f"• Новых игроков сегодня: <b>{stats['new_today']}</b>\n"
        f"• Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"• Пополнено всего: <b>{stats['total_deposits']:.2f} $</b>\n"
        f"• Выведено всего: <b>{stats['total_withdrawals']:.2f} $</b>"
    )
    
    return text, markup


def send_about(message: types.Message) -> None:
    # Check subscription before allowing access
    user = db.ensure_user(message.from_user)
    if not check_and_enforce_subscription(user["user_id"], message.chat.id):
        return
    
    text, markup = build_about_view()
    
    # Проверяем наличие фото для раздела "О боте"
    photo = db.get_section_photo("about")
    if photo:
        try:
            bot.send_photo(message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for about (invalid file_id): {e}")
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def render_about_inline(message: types.Message) -> None:
    """Renders the about section inline (edits existing message)."""
    text, markup = build_about_view()
    
    # Проверяем наличие фото для раздела "О боте"
    photo = db.get_section_photo("about")
    
    if message_has_media(message) and photo:
        try:
            bot.edit_message_media(
                media=types.InputMediaPhoto(
                    media=photo["file_id"],
                    caption=text,
                    parse_mode="HTML",
                ),
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
            )
            return
        except ApiException as exc:
            logger.debug("Failed to update about media: %s", exc)
    
    try:
        edit_message_html(message, text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001 - fallback already handled inside helper
        logger.debug("Failed to edit about inline message: %s", exc)


def build_referral_view(user: sqlite3.Row) -> tuple[str, types.InlineKeyboardMarkup]:
    """Builds the referral program view."""
    settings = db.get_settings()
    
    # Получаем процент реферальной системы
    try:
        ref_percentage = Decimal(settings.get("referral_percentage", "15.0"))
    except (InvalidOperation, ValueError):
        ref_percentage = Decimal("15.0")
    
    # Генерируем реферальную ссылку
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start=ref{user['user_id']}"
    
    # Получаем статистику рефералов
    referrals = db.get_user_referrals(user["user_id"])
    ref_count = len(referrals)
    ref_earnings = row_decimal(user, "ref_earnings") if "ref_earnings" in user.keys() else Decimal("0")
    
    text = (
        "<b>💼 Партнерская программа</b>\n\n"
        f"<b>💰 Зарабатывайте с нами!</b>\n"
        f"Получайте <b>{ref_percentage}%</b> от проигрышей ваших рефералов!\n\n"
        f"<b>📊 Ваша статистика:</b>\n"
        f"• Приглашено друзей: <b>{ref_count}</b>\n"
        f"• Заработано: <b>{format_money(ref_earnings)} $</b>\n\n"
        f"<b>🔗 Ваша партнёрская ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"Делитесь ссылкой с друзьями и получайте пассивный доход!"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    # Share button with shortened link appearance
    share_text = f"🎮 Присоединяйся ко мне в боте!"
    share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"
    markup.add(
        types.InlineKeyboardButton("📤 Поделиться ссылкой", url=share_url)
    )
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cabinet"))
    
    return text, markup


def send_referral_system(message: types.Message) -> None:
    """Показывает информацию о реферальной системе."""
    user = db.ensure_user(message.from_user)
    text, markup = build_referral_view(user)
    
    # Проверяем наличие фото для раздела "Партнерка"
    photo = db.get_section_photo("referral")
    if photo:
        try:
            bot.send_photo(message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for referral (invalid file_id): {e}")
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def render_referral_inline(message: types.Message, user: sqlite3.Row) -> None:
    """Renders the referral program inline (edits existing message)."""
    text, markup = build_referral_view(user)
    
    # Проверяем наличие фото для раздела "Партнерка"
    photo = db.get_section_photo("referral")
    if message_has_media(message) and photo:
        try:
            bot.edit_message_media(
                media=types.InputMediaPhoto(
                    media=photo["file_id"],
                    caption=text,
                    parse_mode="HTML",
                ),
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
            )
            return
        except ApiException as exc:
            logger.debug("Failed to update referral media: %s", exc)
    
    try:
        edit_message_html(message, text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to edit referral inline message: %s", exc)


def build_top_players_view() -> tuple[str, types.InlineKeyboardMarkup]:
    """Builds the TOP players view."""
    top_players = db.get_top_players_by_games(5)
    
    if not top_players:
        text = (
            "<b>🏆 ТОП игроков</b>\n\n"
            "Пока никто не сыграл ни одной игры.\n"
            "Станьте первым!"
        )
    else:
        lines = [
            "<b>🏆 ТОП 5 игроков</b>",
            "<blockquote>По количеству сыгранных игр</blockquote>\n",
        ]
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        
        for idx, player in enumerate(top_players):
            medal = medals[idx] if idx < len(medals) else f"{idx + 1}."
            username = player["username"] if "username" in player.keys() else None
            first_name = player["first_name"] if "first_name" in player.keys() else "Игрок"
            
            # Формируем имя игрока
            if username:
                player_name = f"@{username}"
            else:
                player_name = first_name or "Игрок"
            
            games_count = player["bets_total"] if "bets_total" in player.keys() else 0
            winnings = row_decimal(player, "winnings_total")
            
            lines.append(
                f"{medal} <b>{player_name}</b>\n"
                f"   Игр: {games_count} | Выиграно: {format_money(winnings)} $"
            )
        
        text = "\n".join(lines)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_about"))
    markup.add(types.InlineKeyboardButton("🎮 Играть", callback_data="play:home"))
    
    return text, markup


def send_top_players(message: types.Message) -> None:
    """Показывает ТОП 5 игроков по количеству сыгранных игр."""
    text, markup = build_top_players_view()
    
    # Проверяем наличие фото для раздела "ТОП"
    photo = db.get_section_photo("top")
    if photo:
        try:
            bot.send_photo(message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for top (invalid file_id): {e}")
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def render_top_players_inline(message: types.Message) -> None:
    """Renders the TOP players inline (edits existing message)."""
    text, markup = build_top_players_view()
    
    # Проверяем наличие фото для раздела "ТОП"
    photo = db.get_section_photo("top")
    if message_has_media(message) and photo:
        try:
            bot.edit_message_media(
                media=types.InputMediaPhoto(
                    media=photo["file_id"],
                    caption=text,
                    parse_mode="HTML",
                ),
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
            )
            return
        except ApiException as exc:
            logger.debug("Failed to update top media: %s", exc)
    
    try:
        edit_message_html(message, text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001 - fallback already handled inside helper
        logger.debug("Failed to edit top players inline message: %s", exc)


def build_play_overview_text(user: sqlite3.Row, settings: Dict[str, str]) -> str:
    balance = format_money(row_decimal(user, "balance"))
    winnings = format_money(row_decimal(user, "winnings_total"))
    bets_total = user["bets_total"]
    lines = [
        "<b>🎰 Игровой зал</b>",
        f"Баланс: <b>{balance} $</b> | Выигрыш: {winnings} $",
        f"Ставок сделано: {bets_total}",
        "",
        "<b>Выберите игру:</b>",
        "<blockquote>Множители указаны прямо на кнопках ниже — нажмите, чтобы открыть подробности.</blockquote>",
    ]
    return "\n".join(lines)


def send_play(message: types.Message) -> None:
    user = db.ensure_user(message.from_user)
    
    # Check subscription before allowing access
    if not check_and_enforce_subscription(user["user_id"], message.chat.id):
        return
    
    settings = db.get_settings()
    text = build_play_overview_text(user, settings)
    markup = build_play_keyboard(settings)
    
    # Проверяем наличие фото для раздела "Играть"
    photo = db.get_section_photo("play")
    if photo:
        try:
            bot.send_photo(message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for play (invalid file_id): {e}")
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def present_play_home(call: types.CallbackQuery) -> None:
    user = db.ensure_user(call.from_user)
    
    # Check subscription before allowing access
    if not check_and_enforce_subscription(user["user_id"], call.message.chat.id):
        bot.answer_callback_query(call.id, "Необходима подписка на каналы!")
        return
    
    bot.answer_callback_query(call.id)
    settings = db.get_settings()
    text = build_play_overview_text(user, settings)
    markup = build_play_keyboard(settings)
    
    # Проверяем наличие фото для раздела "Играть"
    photo = db.get_section_photo("play")
    if photo:
        # Если есть фото - удаляем старое сообщение и отправляем новое с фото
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except ApiException:
            pass
        try:
            bot.send_photo(call.message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for play (invalid file_id): {e}")
            bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        if message_has_media(call.message):
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except ApiException:
                pass
            bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
        else:
            edit_message_html(call.message, text, reply_markup=markup)
    states.pop(call.from_user.id)


def show_future_game_results(message: types.Message) -> None:
    """
    Админ-функция для предпросмотра будущих результатов игр.
    Опирается на текущий профит и целевую маржу владельца, чтобы показать,
    нужно ли удерживать проигрыши или можно разрешить выигрыши.
    """
    settings = db.get_settings()
    stats = db.get_bot_stats()

    total_deposits = stats.get("total_deposits", Decimal("0"))
    total_withdrawals = stats.get("total_withdrawals", Decimal("0"))

    if not isinstance(total_deposits, Decimal):
        total_deposits = Decimal(str(total_deposits))
    if not isinstance(total_withdrawals, Decimal):
        total_withdrawals = Decimal(str(total_withdrawals))

    current_profit = total_deposits - total_withdrawals

    try:
        owner_profit_margin = Decimal(
            settings.get("owner_profit_margin", DEFAULT_SETTINGS["owner_profit_margin"])
        )
    except (InvalidOperation, ValueError, TypeError):
        owner_profit_margin = Decimal("0")

    should_reduce = False
    chance_multiplier = 1.0
    bet_stats = db.get_bet_profit_stats()

    planned_outcome_label = "🎲 Полный рандом (100%)"
    rationale = "✅ Контроль бросков отключён — все результаты полностью случайные."

    lines = ["<b>🔮 Контроль игр</b>", ""]
    lines.append(f"Пополнения: <b>{format_money(total_deposits)} $</b>")
    lines.append(f"Выводы: <b>{format_money(total_withdrawals)} $</b>")
    lines.append(f"Текущий профит: <b>{format_money(current_profit)} $</b>")

    if owner_profit_margin > Decimal("0"):
        lines.append(f"Цель профита: <b>{format_money(owner_profit_margin)} $</b>")
        diff = current_profit - owner_profit_margin
        if diff >= Decimal("0"):
            lines.append(f"Запас над целью: <b>{format_money(diff)} $</b>")
        else:
            lines.append(f"Дефицит профита: <b>{format_money(diff.copy_abs())} $</b>")

    lines.append("")
    lines.append("✅ Режим защиты профита отключён. Все правильные ставки рассматриваются честно и случайно.")

    lines.append("")
    lines.append("<b>План для правильных ставок:</b>")
    lines.append("")

    for game_key, rules in GAME_RULES.items():
        emoji = rules.get("emoji", "🎮")
        label = rules.get("label", game_key)
        lines.append(f"{emoji} <b>{label}</b>: {planned_outcome_label}")

    lines.append("")
    lines.append("<b>Фактические суммы по играм:</b>")
    lines.append("")

    overall_losses = Decimal("0")
    overall_wins = Decimal("0")
    overall_bets = 0
    overall_wins_count = 0
    overall_losses_count = 0

    for game_key, rules in GAME_RULES.items():
        emoji = rules.get("emoji", "🎮")
        label = rules.get("label", game_key)
        game_stat = bet_stats.get(game_key)

        if not game_stat or int(game_stat.get("total_bets", 0)) == 0:
            lines.append(f"{emoji} <b>{label}</b>: ставок ещё не было.")
            continue

        total_bets = int(game_stat.get("total_bets", 0))
        wins_count = int(game_stat.get("wins_count", 0))
        losses_count = int(game_stat.get("losses_count", 0))

        losses_amount = game_stat.get("total_losses_amount", Decimal("0"))
        if not isinstance(losses_amount, Decimal):
            losses_amount = Decimal(str(losses_amount))
        wins_amount = game_stat.get("total_wins_amount", Decimal("0"))
        if not isinstance(wins_amount, Decimal):
            wins_amount = Decimal(str(wins_amount))

        net_profit = losses_amount - wins_amount

        lines.append(
            f"{emoji} <b>{label}</b>: проигрыши игроков <b>{format_money(losses_amount)} $</b> • "
            f"выплаты <b>{format_money(wins_amount)} $</b> • профит <b>{format_money(net_profit)} $</b> "
            f"({wins_count} побед / {losses_count} поражений, ставок {total_bets})"
        )

        overall_losses += losses_amount
        overall_wins += wins_amount
        overall_bets += total_bets
        overall_wins_count += wins_count
        overall_losses_count += losses_count

    lines.append("")
    if overall_bets:
        overall_net = overall_losses - overall_wins
        lines.append(
            "Итог: ставок <b>{bets}</b>, побед {wins}, поражений {losses}. "
            "Профит казино: <b>{profit} $</b> "
            "(проигрыши игроков {lost} $, выплаты {paid} $).".format(
                bets=overall_bets,
                wins=overall_wins_count,
                losses=overall_losses_count,
                profit=format_money(overall_net),
                lost=format_money(overall_losses),
                paid=format_money(overall_wins),
            )
        )
    else:
        lines.append("Итог: ставок ещё не было.")

    lines.append("")
    lines.append(rationale)
    if should_reduce:
        lines.append(
            f"<i>💡 Система автоматически удерживает профит, ограничивая выигрыши до {chance_multiplier * 100:.0f}%.</i>"
        )

    # Просто показываем статистику, без кнопок
    settings = db.get_settings()
    games_channel = settings.get("games_channel", "")
    
    if games_channel:
        lines.append("")
        lines.append(f"📤 <b>Канал для витрины:</b> <code>{games_channel}</code>")
        lines.append("")
        lines.append("💡 <i>При низком профите бот автоматически:</i>")
        lines.append("• Отправляет в канал разные результаты (витрина честности)")
        lines.append("• Отправляет игроку проигрышный результат")
        lines.append("• Игрок думает что всё рандомно!")
    else:
        lines.append("")
        lines.append("⚠️ <b>Канал для витрины не установлен!</b>")
        lines.append("Настройте канал в разделе: /admin → Ссылки и чаты → Канал для контроля игр")
    
    bot.send_message(
        message.chat.id,
        "\n".join(lines),
    )


def build_admin_menu_markup(user_id: int) -> types.InlineKeyboardMarkup:
    """Builds the admin menu markup with permission filtering."""
    # Получаем разрешения админа
    permissions = db.get_admin_permissions(user_id)
    
    # Все доступные разделы с их идентификаторами
    all_sections = [
        ("financial", "💰 Финансовые настройки"),
        ("multipliers", "🎮 Игровые множители"),
    ]
    if VIP_FEATURES_ENABLED:
        all_sections.append(("mines_chance", "🎯 Шанс в «Минах»"))
    all_sections.extend([
        ("links", "🔗 Ссылки и чаты"),
        ("design", "🎨 Оформление"),
        ("manage_admins", "👥 Управление админами"),
        ("balance_management", "💳 Управление балансом"),
        ("reserve", "💎 Резерв приложения"),
        ("crypto_checks", "🧾 Чеки Crypto Pay"),
        ("stats", "📊 Статистика"),
        ("top_balance", "🏆 Топ 20 по балансу"),
        ("reviews", "📝 Отзывы"),
        ("test_dice", "🎲 Тест кубика"),
        ("broadcast", "📢 Рассылка"),
        ("promo_codes", "🎁 Промокоды"),
        ("required_channels", "📢 ОП каналы (подписка)"),
    ])
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for section_id, section_name in all_sections:
        if section_id in permissions:
            markup.add(
                types.InlineKeyboardButton(
                    section_name, callback_data=f"admin:{section_id}"
                )
            )
    
    return markup


def handle_admin_command(message: types.Message) -> None:
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Эта команда доступна только администраторам.")
        return

    markup = build_admin_menu_markup(message.from_user.id)
    
    bot.send_message(
        message.chat.id,
        "<b>🔧 Админ-панель</b>\n\nВыберите раздел для настройки:",
        reply_markup=markup,
    )


@bot.message_handler(commands=["start"])
def command_start(message: types.Message) -> None:
    # Игнорировать сообщения из групповых чатов
    if message.chat.type != 'private':
        return
    
    user = db.ensure_user(message.from_user)
    logger.info("User %s started bot", user["user_id"])
    
    # Check if user is blocked
    if db.is_user_blocked(user["user_id"]):
        logger.info("Blocked user %s tried to use bot", user["user_id"])
        return
    
    # Check required channels subscription (skip for admins)
    if not db.is_admin(user["user_id"]):
        required_channels = db.get_all_required_channels()
        if required_channels:
            is_subscribed, not_subscribed = check_user_subscription(user["user_id"], required_channels)
            if not is_subscribed:
                logger.info("User %s is not subscribed to required channels", user["user_id"])
                text_lines = [
                    "📢 <b>Обязательная подписка</b>",
                    "",
                    "Для использования бота необходимо подписаться на следующие каналы:",
                    "",
                ]
                for channel in not_subscribed:
                    text_lines.append(f"• {channel['channel_name']}")
                
                text_lines.append("")
                text_lines.append("После подписки нажмите кнопку ниже для проверки.")
                
                markup = build_subscription_required_markup(not_subscribed)
                bot.send_message(
                    message.chat.id,
                    "\n".join(text_lines),
                    reply_markup=markup,
                    parse_mode="HTML"
                )
                return
    
    # Handle referral code from deep link
    if message.text and len(message.text.split()) > 1:
        parts = message.text.split()
        if len(parts) == 2 and parts[1].startswith("ref"):
            try:
                referrer_id = int(parts[1][3:])  # Extract ID from ref123456
                # Only set referrer if user is new (no referrer set yet)
                user_referrer_id = user["referrer_id"] if "referrer_id" in user.keys() else None
                if user_referrer_id is None and referrer_id != user["user_id"]:
                    db.set_user_referrer(user["user_id"], referrer_id)
                    logger.info("User %s referred by %s", user["user_id"], referrer_id)
            except (ValueError, IndexError):
                pass
    
    # Получаем настраиваемый текст приветствия
    settings = db.get_settings()
    welcome_text = settings.get("welcome_text", "Добро пожаловать! Используйте меню ниже, чтобы управлять ботом.")
    
    # Проверяем наличие фото для приветствия
    photo = db.get_section_photo("start")
    if photo:
        try:
            bot.send_photo(
                message.chat.id, 
                photo["file_id"], 
                caption=welcome_text, 
                reply_markup=build_main_menu(),
                parse_mode="HTML"
            )
        except ApiTelegramException as e:
            logger.warning(f"Failed to send welcome photo (invalid file_id): {e}")
            bot.send_message(
                message.chat.id,
                welcome_text,
                reply_markup=build_main_menu(),
                parse_mode="HTML"
            )
    else:
        bot.send_message(
            message.chat.id,
            welcome_text,
            reply_markup=build_main_menu(),
            parse_mode="HTML"
        )
    
    if is_creator_branding_active():
        send_creator_branding_banner(message.chat.id)
    
    # Сразу показываем игры
    send_play(message)


@bot.message_handler(commands=["admin"])
def command_admin(message: types.Message) -> None:
    # Игнорировать сообщения из групповых чатов
    if message.chat.type != 'private':
        return
    
    handle_admin_command(message)


@bot.message_handler(commands=["promo"])
def command_promo(message: types.Message) -> None:
    """Handle /promo command to activate promo codes."""
    # Игнорировать сообщения из групповых чатов
    if message.chat.type != 'private':
        return
    
    # Extract promo code from command
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "❌ Использование: /promo [промокод]\n\n"
            "Пример: /promo WELCOME2024"
        )
        return
    
    promo_code = parts[1].strip().upper()
    
    # Ensure user exists
    db.ensure_user(message.from_user)
    
    # Activate promo code
    success, msg = db.activate_promo_code(message.from_user.id, promo_code)
    
    if success:
        bot.reply_to(message, f"✅ {msg}")
    else:
        bot.reply_to(message, f"❌ {msg}")


@bot.message_handler(content_types=["text"])
def handle_text(message: types.Message) -> None:
    # Игнорировать сообщения из групповых чатов
    if message.chat.type != 'private':
        return
    
    logger.info("Received text message from user %s: '%s'", message.from_user.id, message.text)
    
    # Check if user is blocked
    if db.is_user_blocked(message.from_user.id):
        logger.info("Blocked user %s tried to send message", message.from_user.id)
        return
    
    user_state = states.peek(message.from_user.id)
    logger.info("User %s state: %s", message.from_user.id, user_state.state if user_state else "NO_STATE")
    if user_state:
        if user_state.state == "awaiting_bet_amount":
            logger.info("Routing to process_bet_amount for user %s", message.from_user.id)
            process_bet_amount(message, user_state)
            return
        if user_state.state == "awaiting_deposit_amount":
            logger.info("Routing to process_deposit_amount for user %s", message.from_user.id)
            process_deposit_amount(message, user_state)
            return
        if user_state.state == "awaiting_withdraw_amount":
            logger.info("Routing to process_withdraw_amount for user %s", message.from_user.id)
            process_withdraw_amount(message, user_state)
            return
        if user_state.state == "awaiting_admin_setting":
            logger.info("Routing to process_admin_setting for user %s", message.from_user.id)
            process_admin_setting(message, user_state)
            return
        if user_state.state == "awaiting_manual_withdraw_link":
            logger.info("Routing to process_manual_withdraw_link for admin %s", message.from_user.id)
            process_manual_withdraw_link(message, user_state)
            return
        if user_state.state == "awaiting_reserve_amount":
            logger.info("Routing to process_reserve_amount for user %s", message.from_user.id)
            process_reserve_amount(message, user_state)
            return
        if user_state.state == "awaiting_add_admin_id":
            logger.info("Routing to process_add_admin for user %s", message.from_user.id)
            process_add_admin(message, user_state)
            return
        if user_state.state == "awaiting_remove_admin_id":
            logger.info("Routing to process_remove_admin for user %s", message.from_user.id)
            process_remove_admin(message, user_state)
            return
        if user_state.state == "awaiting_add_balance_user_id":
            logger.info("Routing to process_add_balance_user_id for user %s", message.from_user.id)
            process_add_balance_user_id(message, user_state)
            return
        if user_state.state == "awaiting_add_balance_amount":
            logger.info("Routing to process_add_balance_amount for user %s", message.from_user.id)
            process_add_balance_amount(message, user_state)
            return
        if user_state.state == "awaiting_subtract_balance_user_id":
            logger.info("Routing to process_subtract_balance_user_id for user %s", message.from_user.id)
            process_subtract_balance_user_id(message, user_state)
            return
        if user_state.state == "awaiting_subtract_balance_amount":
            logger.info("Routing to process_subtract_balance_amount for user %s", message.from_user.id)
            process_subtract_balance_amount(message, user_state)
            return
        if user_state.state == "awaiting_section_photo":
            # Обработка отмены загрузки фото
            text = (message.text or "").strip().lower()
            if text in CANCEL_KEYWORDS:
                section_key = user_state.payload.get("section_key", "неизвестный")
                states.pop(message.from_user.id)
                bot.reply_to(message, f"❌ Загрузка фото для раздела отменена.")
                return
            else:
                bot.reply_to(message, "Пожалуйста, отправьте фото или нажмите кнопку 'Отмена'.")
                return
        if user_state.state == "awaiting_dice_test_chat_id":
            logger.info("Routing to process_dice_test_chat_id for user %s", message.from_user.id)
            process_dice_test_chat_id(message, user_state)
            return
        if user_state.state == "awaiting_dice_test_number":
            logger.info("Routing to process_dice_test for user %s", message.from_user.id)
            process_dice_test(message, user_state)
            return
        if user_state.state == "awaiting_broadcast_message":
            logger.info("Routing to process_broadcast for user %s", message.from_user.id)
            process_broadcast(message, user_state)
            return
        if user_state.state == "awaiting_promo_code":
            logger.info("Routing to process_promo_code for user %s", message.from_user.id)
            process_promo_code(message, user_state)
            return
        if user_state.state == "awaiting_promo_amount":
            logger.info("Routing to process_promo_amount for user %s", message.from_user.id)
            process_promo_amount(message, user_state)
            return
        if user_state.state == "awaiting_promo_max_uses":
            logger.info("Routing to process_promo_max_uses for user %s", message.from_user.id)
            process_promo_max_uses(message, user_state)
            return
        if user_state.state == "awaiting_block_user_id":
            logger.info("Routing to process_block_user for user %s", message.from_user.id)
            process_block_user(message, user_state)
            return
        if user_state.state == "awaiting_unblock_user_id":
            logger.info("Routing to process_unblock_user for user %s", message.from_user.id)
            process_unblock_user(message, user_state)
            return
        if user_state.state == "awaiting_required_channel_info":
            logger.info("Routing to process_required_channel_info for user %s", message.from_user.id)
            process_required_channel_info(message, user_state)
            return
        if user_state.state == "awaiting_check_id_to_delete":
            logger.info("Routing to process_delete_check for user %s", message.from_user.id)
            process_delete_check(message, user_state)
            return

    text = (message.text or "").strip().lower()
    
    # Проверяем, есть ли ключевые слова отмены
    if text in CANCEL_KEYWORDS:
        # Только если есть состояние, обрабатываем отмену
        if states.peek(message.from_user.id):
            states.pop(message.from_user.id)
            bot.send_message(
                message.chat.id,
                "Действие отменено.",
                reply_markup=build_main_menu(),
            )
            return
    
    # Проверяем админ-ключ для просмотра будущих результатов игр
    if message.text and message.text.strip() == "8513":
        if db.is_admin(message.from_user.id):
            show_future_game_results(message)
            return
    
    # Обрабатываем команды меню
    if text.startswith("🎮") or text == "играть":
        send_play(message)
    elif text.startswith("👤") or "личный кабинет" in text:
        send_personal_cabinet(message)
    elif text.startswith("ℹ️") or "о боте" in text:
        send_about(message)
    elif text.startswith("🤝") or text.startswith("💼") or "реферальная" in text or "партнерка" in text:
        send_referral_system(message)
    elif text.startswith("🏆") or "топ" in text.lower():
        send_top_players(message)
    elif text.startswith("/admin"):
        handle_admin_command(message)
    else:
        # Если есть состояние - просто игнорируем неправильный ввод (пользователь должен нажать "Назад")
        # Если нет состояния - показываем меню
        user_state = states.peek(message.from_user.id)
        if not user_state:
            bot.send_message(
                message.chat.id,
                "Пожалуйста, используйте кнопки меню.",
                reply_markup=build_main_menu(),
            )


@bot.message_handler(content_types=["photo"])
def handle_photo(message: types.Message) -> None:
    """Handle photo uploads for section design."""
    # Игнорировать сообщения из групповых чатов
    if message.chat.type != 'private':
        return
    
    logger.info("Received photo from user %s", message.from_user.id)
    user_state = states.peek(message.from_user.id)
    
    if not user_state or user_state.state != "awaiting_section_photo":
        bot.reply_to(message, "Фото получено, но не ожидалось. Используйте меню.")
        return
    
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    section_key = user_state.payload.get("section_key")
    if not section_key:
        bot.reply_to(message, "Ошибка: не указан раздел")
        states.pop(message.from_user.id)
        return
    
    # Get the largest photo
    photo = message.photo[-1]
    file_id = photo.file_id
    
    # Save to database
    db.set_section_photo(section_key, file_id, message.caption)
    
    states.pop(message.from_user.id)
    
    section_names = {
        "start": "🚀 Приветствие",
        "play": "🎮 Играть",
        "cabinet": "👤 Личный кабинет", 
        "about": "ℹ️ О боте",
        "referral": "💼 Партнерка",
        "top": "🏆 ТОП игроков",
        "dice": "🎲 Кубик",
        "ball": "⚽ Футбол",
        "darts": "🎯 Дартс",
        "basket": "🏀 Баскет",
        "mines": "💣 Мины",
        "withdraw": "💸 Вывод",
        "wins": "🏆 Победы"
    }
    section_name = section_names.get(section_key, section_key)
    
    bot.reply_to(message, f"✅ Фото для раздела '{section_name}' успешно сохранено!")
    
    # Показываем раздел с новым фото и кнопками
    if section_key == "play":
        send_play(message)
    elif section_key == "cabinet":
        send_cabinet(message)
    elif section_key == "about":
        send_about(message)


@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call: types.CallbackQuery) -> None:
    # Игнорировать callback'и из групповых чатов
    if call.message and call.message.chat.type != 'private':
        bot.answer_callback_query(call.id)
        return
    
    logger.info("Received callback from user %s: '%s'", call.from_user.id, call.data)
    try:
        if call.data.startswith("play:"):
            _, action = call.data.split(":", 1)
            if action == "home":
                present_play_home(call)
            else:
                bot.answer_callback_query(call.id, "Действие недоступно")
        elif call.data.startswith("game:"):
            _, game_key = call.data.split(":", 1)
            present_game_options(call, game_key)
        elif call.data.startswith("bet:"):
            _, game_key, bet_type = call.data.split(":")
            present_bet_targets(call, game_key, bet_type)
        elif call.data.startswith("betback:"):
            _, game_key, bet_type = call.data.split(":", 2)
            states.pop(call.from_user.id)
            present_bet_targets(call, game_key, bet_type)
        elif call.data.startswith("target:"):
            _, game_key, bet_type, target = call.data.split(":", 3)
            prompt_for_bet_amount(call, game_key, bet_type, target)
        elif call.data.startswith("mines:"):
            handle_mines_callback(call)
        elif call.data.startswith("wallet:"):
            _, action = call.data.split(":", 1)
            logger.info("Wallet action '%s' for user %s", action, call.from_user.id)
            if action == "deposit":
                begin_deposit_flow(call)
            elif action == "withdraw":
                begin_withdraw_flow(call)
            elif action == "back":
                states.pop(call.from_user.id)
                bot.answer_callback_query(call.id, "Возврат")
                refresh_personal_cabinet(call)
            elif action == "refresh":
                states.pop(call.from_user.id)
                bot.answer_callback_query(call.id, "Обновлено")
                refresh_personal_cabinet(call)
            else:
                bot.answer_callback_query(call.id, "Раздел в разработке")
        elif call.data.startswith("invoice:"):
            handle_invoice_callback(call)
        elif call.data.startswith("cancel:"):
            handle_cancel_callback(call)
        elif call.data.startswith("admin:"):
            handle_admin_callback(call)
        elif call.data.startswith("game_send:"):
            # Обработка выбора игры для отправки
            user_state = states.peek(call.from_user.id)
            if not user_state or user_state.state != "awaiting_game_type":
                bot.answer_callback_query(call.id, "Сессия истекла. Начните заново.")
                return
            
            _, game_key = call.data.split(":", 1)
            if game_key not in GAME_RULES:
                bot.answer_callback_query(call.id, "Неизвестная игра")
                return
            
            rules = GAME_RULES[game_key]
            emoji = rules.get("emoji", "🎮")
            label = rules.get("label", game_key)
            
            # Обновляем payload с выбранной игрой
            payload = dict(user_state.payload)
            payload["game_key"] = game_key
            states.set(call.from_user.id, "awaiting_game_outcome", **payload)
            
            bot.answer_callback_query(call.id)
            
            # Показываем выбор исхода
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ ВЫИГРЫШ", callback_data=f"game_outcome:{game_key}:win"),
                types.InlineKeyboardButton("❌ ПРОИГРЫШ", callback_data=f"game_outcome:{game_key}:lose"),
            )
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cancel:game_send"))
            
            try:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=(
                        f"Игра: {emoji} <b>{label}</b>\n\n"
                        f"Выберите нужный результат:\n"
                        f"• <b>ВЫИГРЫШ</b> - будут отправляться только выигрышные результаты\n"
                        f"• <b>ПРОИГРЫШ</b> - будут отправляться только проигрышные результаты\n\n"
                        f"⚠️ Все неподходящие попытки будут автоматически удалены."
                    ),
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except ApiException:
                bot.send_message(
                    call.message.chat.id,
                    f"Игра: {emoji} <b>{label}</b>\n\nВыберите нужный результат:",
                    parse_mode="HTML",
                    reply_markup=markup,
                )
        elif call.data.startswith("game_outcome:"):
            # Обработка выбора исхода и запуск отправки
            user_state = states.peek(call.from_user.id)
            if not user_state or user_state.state != "awaiting_game_outcome":
                bot.answer_callback_query(call.id, "Сессия истекла. Начните заново.")
                return
            
            _, game_key, outcome = call.data.split(":", 2)
            if outcome not in {"win", "lose"}:
                bot.answer_callback_query(call.id, "Некорректный выбор")
                return
            
            # Запускаем отправку игр
            process_game_send_execution(call, user_state, game_key, outcome)
        elif call.data == "show_top":
            bot.answer_callback_query(call.id)
            render_top_players_inline(call.message)
        elif call.data == "back_to_about":
            bot.answer_callback_query(call.id)
            render_about_inline(call.message)
        elif call.data == "show_referral":
            bot.answer_callback_query(call.id)
            user = db.ensure_user(call.from_user)
            render_referral_inline(call.message, user)
        elif call.data == "back_to_cabinet":
            bot.answer_callback_query(call.id)
            render_personal_cabinet_inline(
                call.message.chat.id,
                call.message.message_id,
                call.from_user,
                message=call.message,
            )
        elif call.data == "check_subscription":
            # Re-check subscription when user clicks "I subscribed" button
            bot.answer_callback_query(call.id, "Проверяем подписку...")
            
            # Check required channels subscription (skip for admins)
            if db.is_admin(call.from_user.id):
                # Admins don't need to subscribe
                try:
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text="✅ Добро пожаловать, администратор!",
                        parse_mode="HTML"
                    )
                except ApiException:
                    pass
                
                # Send welcome message
                user = db.ensure_user(call.from_user)
                settings = db.get_settings()
                welcome_text = settings.get("welcome_text", "Добро пожаловать! Используйте меню ниже, чтобы управлять ботом.")
                bot.send_message(
                    call.message.chat.id,
                    welcome_text,
                    reply_markup=build_main_menu(),
                    parse_mode="HTML"
                )
                
                # Автоматически показать меню "Играть" после успешной проверки ОП
                text = build_play_overview_text(user, settings)
                markup = build_play_keyboard(settings)
                
                play_photo = db.get_section_photo("play")
                if play_photo:
                    try:
                        bot.send_photo(
                            call.message.chat.id, 
                            play_photo["file_id"], 
                            caption=text, 
                            reply_markup=markup, 
                            parse_mode="HTML"
                        )
                    except ApiTelegramException as e:
                        logger.warning(f"Failed to send photo for play (invalid file_id): {e}")
                        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
                else:
                    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
                return
            
            required_channels = db.get_all_required_channels()
            if not required_channels:
                # No required channels, proceed normally
                try:
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text="✅ Проверка пройдена!",
                        parse_mode="HTML"
                    )
                except ApiException:
                    pass
                
                user = db.ensure_user(call.from_user)
                settings = db.get_settings()
                welcome_text = settings.get("welcome_text", "Добро пожаловать! Используйте меню ниже, чтобы управлять ботом.")
                bot.send_message(
                    call.message.chat.id,
                    welcome_text,
                    reply_markup=build_main_menu(),
                    parse_mode="HTML"
                )
                
                # Автоматически показать меню "Играть" после успешной проверки ОП
                text = build_play_overview_text(user, settings)
                markup = build_play_keyboard(settings)
                
                play_photo = db.get_section_photo("play")
                if play_photo:
                    try:
                        bot.send_photo(
                            call.message.chat.id, 
                            play_photo["file_id"], 
                            caption=text, 
                            reply_markup=markup, 
                            parse_mode="HTML"
                        )
                    except ApiTelegramException as e:
                        logger.warning(f"Failed to send photo for play (invalid file_id): {e}")
                        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
                else:
                    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
                return
            
            is_subscribed, not_subscribed = check_user_subscription(call.from_user.id, required_channels)
            
            if is_subscribed:
                # User is subscribed to all channels
                logger.info("User %s passed subscription check", call.from_user.id)
                try:
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text="✅ Отлично! Вы подписаны на все необходимые каналы.\n\nДобро пожаловать!",
                        parse_mode="HTML"
                    )
                except ApiException:
                    pass
                
                # Send welcome message with main menu
                user = db.ensure_user(call.from_user)
                settings = db.get_settings()
                welcome_text = settings.get("welcome_text", "Добро пожаловать! Используйте меню ниже, чтобы управлять ботом.")
                
                photo = db.get_section_photo("start")
                if photo:
                    try:
                        bot.send_photo(
                            call.message.chat.id, 
                            photo["file_id"], 
                            caption=welcome_text, 
                            reply_markup=build_main_menu(),
                            parse_mode="HTML"
                        )
                    except ApiTelegramException as e:
                        logger.warning(f"Failed to send welcome photo: {e}")
                        bot.send_message(
                            call.message.chat.id,
                            welcome_text,
                            reply_markup=build_main_menu(),
                            parse_mode="HTML"
                        )
                else:
                    bot.send_message(
                        call.message.chat.id,
                        welcome_text,
                        reply_markup=build_main_menu(),
                        parse_mode="HTML"
                    )
                
                # Автоматически показать меню "Играть" после успешной проверки ОП
                text = build_play_overview_text(user, settings)
                markup = build_play_keyboard(settings)
                
                play_photo = db.get_section_photo("play")
                if play_photo:
                    try:
                        bot.send_photo(
                            call.message.chat.id, 
                            play_photo["file_id"], 
                            caption=text, 
                            reply_markup=markup, 
                            parse_mode="HTML"
                        )
                    except ApiTelegramException as e:
                        logger.warning(f"Failed to send photo for play (invalid file_id): {e}")
                        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
                else:
                    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
            else:
                # User is still not subscribed to all channels
                logger.info("User %s still not subscribed to all channels", call.from_user.id)
                text_lines = [
                    "❌ <b>Подписка не подтверждена</b>",
                    "",
                    "Вы ещё не подписаны на все необходимые каналы:",
                    "",
                ]
                for channel in not_subscribed:
                    text_lines.append(f"• {channel['channel_name']}")
                
                text_lines.append("")
                text_lines.append("Пожалуйста, подпишитесь на все каналы и нажмите кнопку ниже для проверки.")
                
                markup = build_subscription_required_markup(not_subscribed)
                try:
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text="\n".join(text_lines),
                        reply_markup=markup,
                        parse_mode="HTML"
                    )
                except ApiException:
                    bot.send_message(
                        call.message.chat.id,
                        "\n".join(text_lines),
                        reply_markup=markup,
                        parse_mode="HTML"
                    )
        else:
            bot.answer_callback_query(call.id, "Команда не поддерживается")
    except Exception as exc:  # broad catch to avoid crashing callback processing
        logger.exception("Failed to handle callback: %s", exc)
        bot.answer_callback_query(call.id, "Ошибка выполнения")


def present_game_options(call: types.CallbackQuery, game_key: str) -> None:
    if game_key not in GAME_RULES:
        bot.answer_callback_query(call.id, "Игра недоступна")
        return
    
    # Check subscription before allowing access
    user = db.ensure_user(call.from_user)
    if not check_and_enforce_subscription(user["user_id"], call.message.chat.id):
        bot.answer_callback_query(call.id, "Необходима подписка на каналы!")
        return
    
    states.pop(call.from_user.id)
    bot.answer_callback_query(call.id)
    settings = db.get_settings()
    rules = GAME_RULES[game_key]
    markup = build_bet_type_keyboard(game_key, settings)
    lines = [f"{rules['emoji']} <b>{rules['label']}</b>"]
    tagline = rules.get("tagline")
    if tagline:
        lines.append(f"<blockquote>{tagline}</blockquote>")
    lines.append("")
    lines.append("<b>Выберите тип ставки:</b>")
    bet_types = get_bet_types(game_key)
    bet_entries: List[Tuple[Decimal, str, Dict[str, Any]]] = []
    for bet_key, bet_config in bet_types.items():
        multipliers = collect_option_multipliers(settings, game_key, bet_key, bet_config)
        if not multipliers:
            continue
        bet_entries.append((max(multipliers), bet_key, bet_config))
    for peak, bet_key, bet_config in sorted(bet_entries, key=lambda item: item[0], reverse=True):
        title = bet_config.get("title", bet_key)
        description = bet_config.get("description")
        lines.append(f"{title} — до <b>x{peak:.2f}</b>")
        if description:
            lines.append(f"<i>{description}</i>")
        options = bet_config.get("targets") or []
        option_details: List[Tuple[Decimal, str, Dict[str, Any]]] = []
        for option in options:
            if option.get("hidden"):
                continue
            label = option.get("label") or option.get("key")
            if not label:
                continue
            multiplier, _ = get_option_multiplier(settings, game_key, bet_key, option)
            option_details.append((multiplier, label, option))
        if option_details:
            for multiplier, label, option in sorted(option_details, key=lambda item: item[0], reverse=True):
                line = f"• {label} — x{multiplier:.2f}"
                if (
                    game_key == "darts"
                    and bet_key == "outcome"
                    and option.get("key") == "hit"
                ):
                    line += f" (🎯 буллсай до x{DARTS_BULLSEYE_MULTIPLIER:.2f})"
                lines.append(line)
        else:
            multiplier = get_multiplier(settings, game_key, bet_key)
            lines.append(f"• Коэффициент: x{multiplier:.2f}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    
    text = "\n".join(lines)
    
    # Проверяем наличие фото для игры
    photo = db.get_section_photo(game_key)
    if photo:
        # Если есть фото - удаляем старое сообщение и отправляем новое с фото
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except ApiException:
            pass
        try:
            bot.send_photo(call.message.chat.id, photo["file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for game {game_key} (invalid file_id): {e}")
            bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        if message_has_media(call.message):
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except ApiException:
                pass
            bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
        else:
            edit_message_html(call.message, text, reply_markup=markup)


def present_bet_targets(call: types.CallbackQuery, game_key: str, bet_type: str) -> None:
    if game_key not in GAME_RULES:
        bot.answer_callback_query(call.id, "Ошибка игры")
        return
    
    # Check subscription before allowing bets
    user = db.ensure_user(call.from_user)
    if not check_and_enforce_subscription(user["user_id"], call.message.chat.id):
        bot.answer_callback_query(call.id, "Необходима подписка на каналы!")
        return
    
    rules = GAME_RULES[game_key]
    settings = db.get_settings()
    bet_config = get_bet_config(game_key, bet_type)
    if not bet_config:
        bot.answer_callback_query(call.id, "Ставка временно недоступна")
        return
    header = f"{rules['emoji']} <b>{rules['label']}</b> → {bet_config.get('title', bet_type)}"
    description = bet_config.get("description", "")
    target_type = bet_config.get("target_type")
    if target_type == "number":
        markup = build_number_keyboard(game_key)
    elif target_type == "choice":
        markup = build_choice_keyboard(game_key, bet_type, bet_config)
    else:
        bot.answer_callback_query(call.id, "Ставка временно недоступна")
        return

    bot.answer_callback_query(call.id)
    lines = [header]
    if description:
        lines.append(description)
    lines.append("")
    prompt = bet_config.get("target_prompt", "Сделайте выбор")
    lines.append(prompt)
    if target_type == "choice":
        options = bet_config.get("targets") or []
        if options:
            option_details: List[Tuple[Decimal, str, Dict[str, Any]]] = []
            for option in options:
                if option.get("hidden"):
                    continue
                label = option.get("label") or option.get("key")
                if not label:
                    continue
                multiplier, _ = get_option_multiplier(settings, game_key, bet_type, option)
                option_details.append((multiplier, label, option))
            for multiplier, label, option in sorted(option_details, key=lambda item: item[0], reverse=True):
                line = f"• {label} — x{multiplier:.2f}"
                if (
                    game_key == "darts"
                    and bet_type == "outcome"
                    and option.get("key") == "hit"
                ):
                    line += f" (🎯 буллсай до x{DARTS_BULLSEYE_MULTIPLIER:.2f})"
                lines.append(line)
    edit_message_html(call.message, "\n".join(lines), reply_markup=markup)


def prompt_for_bet_amount(call: types.CallbackQuery, game_key: str, bet_type: str, target: str) -> None:
    # Check subscription before allowing bets
    user = db.ensure_user(call.from_user)
    if not check_and_enforce_subscription(user["user_id"], call.message.chat.id):
        bot.answer_callback_query(call.id, "Необходима подписка на каналы!")
        return
    
    settings = db.get_settings()
    min_bet = Decimal(settings.get("min_bet", DEFAULT_SETTINGS["min_bet"]))
    bet_config = get_bet_config(game_key, bet_type)
    if not bet_config:
        bot.answer_callback_query(call.id, "Ставка недоступна")
        return

    rules = GAME_RULES[game_key]
    target_type = bet_config.get("target_type")
    target_label = target
    if target_type == "choice":
        option = find_target_option(bet_config, target)
        if not option:
            bot.answer_callback_query(call.id, "Опция недоступна")
            return
        target_label = option.get("label") or option.get("key") or target
        multiplier, multiplier_key = get_option_multiplier(settings, game_key, bet_type, option)
    elif target_type == "number":
        try:
            numeric_target = int(target)
        except (TypeError, ValueError):
            bot.answer_callback_query(call.id, "Некорректный выбор")
            return
        if numeric_target < rules["min_value"] or numeric_target > rules["max_value"]:
            bot.answer_callback_query(call.id, "Выбор вне диапазона")
            return
        target_label = f"№ {numeric_target}"
        multiplier = get_multiplier(settings, game_key, bet_type, target_key=target)
        multiplier_key = multiplier_setting_key(game_key, bet_type, target)
    else:
        bot.answer_callback_query(call.id, "Ставка недоступна")
        return

    states.set(
        call.from_user.id,
        "awaiting_bet_amount",
        game_key=game_key,
        bet_type=bet_type,
        target=target,
        multiplier=str(multiplier),
        multiplier_key=multiplier_key,
        message_id=call.message.message_id,
    )
    bot.answer_callback_query(call.id)
    lines = [
        f"{rules['emoji']} <b>{rules['label']}</b>",
        bet_config.get("title", ""),
    ]
    if target_label:
        lines.append(f"Ваш выбор: <b>{target_label}</b>")
    instruction_lines = [
        "",
        f"Минимальная ставка: <b>{format_money(min_bet)} $</b>",
        f"Коэффициент: <b>x{multiplier:.2f}</b>",
    ]
    if (
        game_key == "darts"
        and bet_type == "outcome"
        and target == "hit"
    ):
        instruction_lines.append(
            f"🎯 Буллсай оплачивается по <b>x{DARTS_BULLSEYE_MULTIPLIER:.2f}</b>"
        )
    instruction_lines.extend(
        [
            "<blockquote>Введите сумму одним сообщением, например: 2.5</blockquote>",
            "Используйте «Назад», чтобы выбрать заново.",
        ]
    )
    lines.extend(instruction_lines)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Назад", callback_data=f"betback:{game_key}:{bet_type}"
        )
    )
    edit_message_html(
        call.message,
        "\n".join(line for line in lines if line),
        reply_markup=markup,
    )


def initialize_mines_session(
    telegram_user: types.User,
    chat_id: int,
    stake: Decimal,
    base_multiplier: Decimal,
    mine_count: int,
    bet_type: str,
    target: str,
    target_label: str,
    multiplier_key: Optional[str],
    play_panel_message_id: Optional[int],
    safe_chance: Optional[float] = None,
) -> MinesSession:
    if mine_count >= MINES_TOTAL_CELLS:
        raise ValueError("Mine count must be less than total cells")
    stake_amount = stake.quantize(MONEY_QUANT, rounding=ROUND_DOWN)
    session_id = uuid4().hex[:8]
    mine_positions = set(random.sample(range(MINES_TOTAL_CELLS), mine_count))
    session = MinesSession(
        session_id=session_id,
        user_id=telegram_user.id,
        chat_id=chat_id,
        stake=stake_amount,
        base_multiplier=base_multiplier,
        mine_count=mine_count,
        bet_type=bet_type,
        target=target,
        target_label=target_label,
        multiplier_key=multiplier_key,
        mine_positions=mine_positions,
        current_multiplier=Decimal("1.00"),
        current_payout=stake_amount,
        play_panel_message_id=play_panel_message_id,
        safe_chance=safe_chance,
    )
    register_mines_session(session)
    return session


def start_mines_game(
    message: types.Message,
    user_state: PendingState,
    stake: Decimal,
    base_multiplier: Decimal,
) -> None:
    base_multiplier = base_multiplier.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    bet_type = user_state.payload["bet_type"]
    target = user_state.payload["target"]
    multiplier_key = user_state.payload.get("multiplier_key")
    panel_message_id = user_state.payload.get("message_id")

    bet_config = get_bet_config("mines", bet_type)
    if not bet_config:
        bot.reply_to(message, "Игра недоступна. Попробуйте позже.")
        states.pop(message.from_user.id)
        return

    option = find_target_option(bet_config, target)
    if not option:
        bot.reply_to(message, "Выбранная сложность недоступна.")
        states.pop(message.from_user.id)
        return

    mine_count = option.get("mine_count")
    if mine_count is None:
        try:
            mine_count = int(option.get("key", target))
        except (TypeError, ValueError):
            mine_count = 3
    try:
        mine_count = int(mine_count)
    except (TypeError, ValueError):
        bot.reply_to(message, "Ошибка настройки игры.")
        states.pop(message.from_user.id)
        return

    if mine_count <= 0 or mine_count >= MINES_TOTAL_CELLS:
        bot.reply_to(message, "Некорректное количество мин.")
        states.pop(message.from_user.id)
        return

    existing_session = get_mines_session_for_user(message.from_user.id)
    if existing_session and existing_session.is_active:
        bot.reply_to(
            message,
            "У вас уже есть активная игра «Мины». Завершите её, чтобы начать новую.",
        )
        states.pop(message.from_user.id)
        return
    if existing_session and not existing_session.is_active:
        remove_mines_session(existing_session)

    target_label = option.get("label") or f"{mine_count} мин"
    safe_chance = get_configured_mines_safe_chance()

    try:
        session = initialize_mines_session(
            telegram_user=message.from_user,
            chat_id=message.chat.id,
            stake=stake,
            base_multiplier=base_multiplier,
            mine_count=mine_count,
            bet_type=bet_type,
            target=target,
            target_label=target_label,
            multiplier_key=multiplier_key,
            play_panel_message_id=panel_message_id,
            safe_chance=safe_chance,
        )
    except ValueError:
        bot.reply_to(message, "Не удалось подготовить поле. Попробуйте другой уровень сложности.")
        states.pop(message.from_user.id)
        return

    states.pop(message.from_user.id)

    if panel_message_id:
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=int(panel_message_id),
                text="💣 Игра «Мины» запущена! Поле с кнопками ниже.",
            )
        except ApiException:
            logger.debug("Failed to update bet prompt message for mines game.")

    caption = build_mines_caption(session)
    markup = build_mines_markup(session)
    photo = db.get_section_photo("mines")
    try:
        if photo:
            board_message = bot.send_photo(
                message.chat.id,
                photo["file_id"],
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
            session.board_uses_photo = True
        else:
            board_message = bot.send_message(
                message.chat.id,
                caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
            session.board_uses_photo = False
        session.board_message_id = board_message.message_id
    except ApiException as exc:
        logger.error("Failed to send mines board: %s", exc)
        bot.reply_to(message, "Не удалось запустить игру «Мины». Попробуйте ещё раз позже.")
        remove_mines_session(session)
        return

    bot.reply_to(
        message,
        f"Ставка принята! Играем с {session.target_label}. Удачи! 💣",
    )


def send_mines_summary(
    session: MinesSession,
    balance_after: float,
    did_win: bool,
    result_message: str,
    payout: Decimal,
) -> None:
    try:
        bet_config = get_bet_config("mines", session.bet_type)
    except KeyError:
        bet_config = None

    if bet_config:
        markup = build_choice_keyboard("mines", session.bet_type, bet_config)
    else:
        settings = db.get_settings()
        markup = build_play_keyboard(settings)

    balance_decimal = Decimal(str(balance_after)).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
    lines = [result_message]
    if did_win:
        lines.append(
            f"<blockquote>Выигрыш: <b>{format_money(payout)} $</b> • x{format_multiplier_value(session.current_multiplier)}</blockquote>"
        )
    else:
        lines.append(
            f"<blockquote>Проигрыш: <b>{format_money(session.stake)} $</b></blockquote>"
        )
    lines.append(
        f"Ставка: <b>{format_money(session.stake)} $</b> • Мин: <b>{session.target_label}</b>"
    )
    lines.append(f"Открыто безопасных клеток: <b>{session.safe_steps}</b>")
    lines.append(f"Текущий баланс: <b>{format_money(balance_decimal)} $</b>")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("Хотите сыграть ещё раз? Выберите количество мин:")

    try:
        bot.send_message(
            session.chat_id,
            "\n".join(lines),
            reply_markup=markup,
            parse_mode="HTML",
        )
    except ApiException as exc:
        logger.debug("Failed to send mines summary message: %s", exc)


def complete_mines_session_win(
    session: MinesSession,
    call: Optional[types.CallbackQuery],
    result_message: str,
) -> None:
    with mines_lock:
        if not session.is_active:
            logger.debug("Mines session %s already completed (win).", session.session_id)
            active = False
        else:
            session.is_active = False
            active = True
    if not active:
        if call:
            try:
                bot.answer_callback_query(call.id, "Игра уже завершена.")
            except ApiException:
                pass
        return

    caption = build_mines_caption(session, final_message=result_message)
    markup = build_mines_markup(session, reveal_all=True)
    update_mines_message(session, caption, markup)

    payout = session.current_payout
    balance_after = apply_bet_result(
        session.user_id,
        session.stake,
        payout,
        "mines",
        session.bet_type,
        session.target,
        result_value=session.safe_steps,
        did_win=True,
        used_multiplier=session.current_multiplier,
        multiplier_key=session.multiplier_key,
    )

    remove_mines_session(session)
    restore_play_panel_for_session(session)

    if call:
        try:
            bot.answer_callback_query(
                call.id,
                f"💰 Выигрыш: {format_money(payout)} $",
                show_alert=True,
            )
        except ApiException:
            pass

    # Отправляем победу в канал побед
    try:
        user = db.get_user(session.user_id)
        send_win_to_channel(
            user_id=session.user_id,
            username=user["username"],
            first_name=user["first_name"],
            game_key="mines",
            payout=payout,
            multiplier=session.current_multiplier,
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке победы в mines в канал: {e}")

    send_mines_summary(session, balance_after, True, result_message, payout)


def complete_mines_session_loss(
    session: MinesSession,
    call: Optional[types.CallbackQuery],
    triggered_cell: int,
    result_message: str,
) -> None:
    with mines_lock:
        if not session.is_active:
            active = False
        else:
            session.is_active = False
            active = True
    session.current_payout = Decimal("0.00")
    if not active:
        if call:
            try:
                bot.answer_callback_query(call.id, "Игра уже завершена.")
            except ApiException:
                pass
        return

    caption = build_mines_caption(session, final_message=result_message)
    markup = build_mines_markup(session, reveal_all=True, highlight_mine=triggered_cell)
    update_mines_message(session, caption, markup)

    balance_after = apply_bet_result(
        session.user_id,
        session.stake,
        Decimal("0"),
        "mines",
        session.bet_type,
        session.target,
        result_value=-1,
        did_win=False,
        used_multiplier=session.current_multiplier,
        multiplier_key=session.multiplier_key,
    )

    remove_mines_session(session)
    restore_play_panel_for_session(session)

    if call:
        try:
            bot.answer_callback_query(call.id, "💥 Мина! Игра окончена.", show_alert=True)
        except ApiException:
            pass

    send_mines_summary(session, balance_after, False, result_message, Decimal("0"))


def handle_mines_open_cell(
    call: types.CallbackQuery,
    session: MinesSession,
    cell_index: int,
) -> None:
    with mines_lock:
        if not session.is_active:
            active = False
        else:
            active = True
        already_opened = cell_index in session.revealed if active else False
        is_mine = cell_index in session.mine_positions if active else False
        if (
            active
            and not already_opened
            and session.safe_chance is not None
            and 0 < session.safe_chance < 1
        ):
            roll = random.random()
            if roll < session.safe_chance:
                if is_mine:
                    session.mine_positions.discard(cell_index)
                    is_mine = False
            else:
                if not is_mine:
                    session.mine_positions.add(cell_index)
                    is_mine = True
        if active and not already_opened and not is_mine:
            session.safe_steps += 1
            order = session.safe_steps
            session.revealed[cell_index] = order
            session.current_multiplier = (
                session.current_multiplier * session.base_multiplier
            ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            session.current_payout = (
                session.stake * session.current_multiplier
            ).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
            safe_steps = session.safe_steps
            current_multiplier_str = format_multiplier_value(session.current_multiplier)
        else:
            safe_steps = session.safe_steps
            current_multiplier_str = format_multiplier_value(session.current_multiplier)

    if not active:
        try:
            bot.answer_callback_query(call.id, "Игра уже завершена.")
        except ApiException:
            pass
        return

    if already_opened:
        try:
            bot.answer_callback_query(call.id, "Эта клетка уже открыта.")
        except ApiException:
            pass
        return

    if is_mine:
        complete_mines_session_loss(
            session,
            call,
            cell_index,
            result_message="💥 Бум! Вы наткнулись на мину.",
        )
        return

    caption = build_mines_caption(session)
    markup = build_mines_markup(session)
    update_mines_message(session, caption, markup)

    try:
        bot.answer_callback_query(call.id, f"✅ Безопасно! x{current_multiplier_str}")
    except ApiException:
        pass

    total_safe = MINES_TOTAL_CELLS - session.mine_count
    if safe_steps >= total_safe:
        complete_mines_session_win(
            session,
            call=None,
            result_message="🎉 Поле очищено! Вы забрали максимальный выигрыш.",
        )


def handle_mines_cashout(call: types.CallbackQuery, session: MinesSession) -> None:
    with mines_lock:
        if not session.is_active:
            active = False
        else:
            active = True
        safe_steps = session.safe_steps

    if not active:
        try:
            bot.answer_callback_query(call.id, "Игра уже завершена.")
        except ApiException:
            pass
        return

    if safe_steps == 0:
        try:
            bot.answer_callback_query(call.id, "Сначала откройте хотя бы одну клетку.")
        except ApiException:
            pass
        return

    complete_mines_session_win(
        session,
        call,
        result_message="💰 Вы забрали выигрыш! Отличный ход.",
    )


def handle_mines_callback(call: types.CallbackQuery) -> None:
    parts = call.data.split(":")
    if len(parts) < 2:
        bot.answer_callback_query(call.id, "Некорректное действие")
        return
    action = parts[1]
    if action == "noop":
        bot.answer_callback_query(call.id)
        return
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "Некорректное действие")
        return

    session_id = parts[2]
    session = get_mines_session(session_id)
    if not session:
        bot.answer_callback_query(call.id, "Игра уже завершена.")
        return
    if session.user_id != call.from_user.id:
        bot.answer_callback_query(call.id, "Это не ваша игра.")
        return

    if action == "o":
        if len(parts) != 4:
            bot.answer_callback_query(call.id, "Некорректная клетка")
            return
        try:
            cell_index = int(parts[3])
        except ValueError:
            bot.answer_callback_query(call.id, "Некорректная клетка")
            return
        if cell_index < 0 or cell_index >= MINES_TOTAL_CELLS:
            bot.answer_callback_query(call.id, "Некорректная клетка")
            return
        handle_mines_open_cell(call, session, cell_index)
        return

    if action == "c":
        handle_mines_cashout(call, session)
        return

    bot.answer_callback_query(call.id, "Действие не поддерживается")


def begin_deposit_flow(call: types.CallbackQuery) -> None:
    logger.info("=== BEGIN DEPOSIT FLOW === User %s clicked deposit button", call.from_user.id)
    
    # Check subscription before allowing deposit
    user = db.ensure_user(call.from_user)
    if not check_and_enforce_subscription(user["user_id"], call.message.chat.id):
        bot.answer_callback_query(call.id, "Необходима подписка на каналы!")
        return
    
    settings = db.get_settings()
    min_deposit = Decimal(settings.get("min_deposit", DEFAULT_SETTINGS["min_deposit"]))
    logger.info("Min deposit setting: %s", min_deposit)
    logger.info("Crypto Pay configured: %s", crypto_pay_client.is_configured)
    if not crypto_pay_client.is_configured:
        logger.error("Crypto Pay not configured! Cannot start deposit flow")
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "Пополнение временно недоступно. Пожалуйста, попробуйте позже.",
        )
        return
    logger.info("Setting state awaiting_deposit_amount for user %s", call.from_user.id)
    states.set(
        call.from_user.id,
        "awaiting_deposit_amount",
        message_id=call.message.message_id,
    )
    bot.answer_callback_query(call.id)
    logger.info("State set, message_id=%s", call.message.message_id)
    text = (
        "<b>💳 Пополнение</b>\n"
        f"Введите сумму в долларах (мин {format_money(min_deposit)} $).\n"
        "Нажмите «Назад», чтобы вернуться в кабинет."
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="wallet:back"),
    )
    edit_message_html(call.message, text, reply_markup=markup)


def begin_withdraw_flow(call: types.CallbackQuery) -> None:
    # Check subscription before allowing withdraw
    user = db.ensure_user(call.from_user)
    if not check_and_enforce_subscription(user["user_id"], call.message.chat.id):
        bot.answer_callback_query(call.id, "Необходима подписка на каналы!")
        return
    
    settings = db.get_settings()
    min_withdraw = Decimal(settings.get("min_withdraw", DEFAULT_SETTINGS["min_withdraw"]))
    if not crypto_pay_client.is_configured:
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "Вывод временно недоступен. Пожалуйста, свяжитесь с поддержкой.",
        )
        return
    
    bot.answer_callback_query(call.id)
    text = (
        "<b>💸 Вывод средств</b>\n"
        f"Введите сумму в долларах (мин {format_money(min_withdraw)} $).\n"
        "Нажмите «Назад», чтобы вернуться в кабинет."
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="wallet:back"),
    )
    
    # Проверяем наличие фото для раздела "Вывод"
    photo = db.get_section_photo("withdraw")
    new_message_id = None
    
    if photo:
        # Если есть фото - удаляем старое сообщение и отправляем новое с фото
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except ApiException:
            pass
        try:
            sent_msg = bot.send_photo(
                call.message.chat.id,
                photo["file_id"],
                caption=text,
                reply_markup=markup,
                parse_mode="HTML"
            )
            new_message_id = sent_msg.message_id
        except ApiTelegramException as e:
            logger.warning(f"Failed to send photo for withdraw (invalid file_id): {e}")
            sent_msg = bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")
            new_message_id = sent_msg.message_id
    else:
        # Если нет фото - просто редактируем сообщение
        edit_message_html(call.message, text, reply_markup=markup)
        new_message_id = call.message.message_id
    
    states.set(
        call.from_user.id,
        "awaiting_withdraw_amount",
        message_id=new_message_id,
    )


def process_bet_amount(message: types.Message, user_state: PendingState) -> None:
    try:
        amount = decimal_from_text(message.text)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        bot.reply_to(message, "Введите корректную сумму в формате 0.00\nНажмите кнопку «Назад» для отмены.")
        return

    settings = db.get_settings()
    min_bet = Decimal(settings.get("min_bet", DEFAULT_SETTINGS["min_bet"]))
    if amount < min_bet:
        bot.reply_to(message, f"Минимальная ставка {min_bet:.2f} $\nНажмите кнопку «Назад» для отмены.")
        return

    user = db.ensure_user(message.from_user)
    if Decimal(str(user["balance"])) < amount:
        bot.reply_to(message, "Недостаточно средств. Пополните баланс.\nНажмите кнопку «Назад» для отмены.")
        return

    game_key = user_state.payload["game_key"]
    bet_type = user_state.payload["bet_type"]
    target = user_state.payload["target"]
    multiplier = Decimal(user_state.payload["multiplier"])
    multiplier_key = user_state.payload.get("multiplier_key")

    if game_key == "mines":
        start_mines_game(message, user_state, amount, multiplier)
        return

    rules = GAME_RULES.get(game_key)
    if not rules:
        bot.reply_to(message, "Игра недоступна.")
        states.pop(message.from_user.id)
        return

    winning_values = winning_values_for_bet(game_key, bet_type, target)
    bot.reply_to(message, "Ставка принята! Запускаем игру...")

    logger.info(f"🎮 Starting fair roll for {game_key}")
    result_value, _ = roll_controlled_dice(
        chat_id=message.chat.id,
        emoji=rules["emoji"],
        game_key=game_key,
        winning_values=winning_values,
        force_win=None,
    )

    did_win = result_value in winning_values

    effective_multiplier = multiplier
    if did_win:
        effective_multiplier = adjust_multiplier_for_outcome(
            game_key,
            bet_type,
            target,
            result_value,
            multiplier,
        )
    payout = amount * effective_multiplier if did_win else Decimal("0")

    if (
        game_key == "darts"
        and bet_type == "outcome"
        and target == "hit"
        and result_value in [2, 3, 4, 5]
    ):
        multiplier_key = "darts_outcome_multiplier_edge"
    if (
        game_key == "basket"
        and bet_type == "outcome"
        and target == "hit"
        and result_value == 5
    ):
        multiplier_key = "basket_outcome_multiplier_swish"

    new_balance = apply_bet_result(
        message.from_user.id,
        amount,
        payout,
        game_key,
        bet_type,
        target,
        result_value,
        did_win,
        effective_multiplier,
        multiplier_key,
    )

    outcome_text = describe_outcome(game_key, result_value)
    balance_text = format_money(Decimal(str(new_balance)))
    
    # Get target label for display
    bet_config = get_bet_config(game_key, bet_type)
    target_label = target
    if bet_config:
        target_type = bet_config.get("target_type")
        if target_type == "choice":
            option = find_target_option(bet_config, target)
            if option:
                target_label = option.get("label") or option.get("key") or target
        elif target_type == "number":
            target_label = f"№ {target}"
    
    rules = GAME_RULES.get(game_key, {})
    game_emoji = rules.get("emoji", "🎮")
    game_label = rules.get("label", "Игра")
    
    result_lines: List[str] = []
    if outcome_text:
        result_lines.append(outcome_text)
    
    stake_text = format_money(amount)
    if did_win:
        # Format: Victory in game (emoji)
        # Quote with multiplier and winnings
        result_lines.extend(
            [
                f"<blockquote>🥳 <b>Победа в игре {game_emoji}</b>\n× {effective_multiplier:.2f}\nВыигрыш: <b>{format_money(payout)} $</b></blockquote>",
            ]
        )
        
        # Отправляем победу в канал побед
        send_win_to_channel(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            game_key=game_key,
            payout=payout,
            multiplier=effective_multiplier,
        )
    else:
        result_lines.extend(
            [
                f"<blockquote>🌀 <b>Проигрыш в игре {game_emoji}</b>\nПроигрыш: <b>{stake_text} $</b></blockquote>",
            ]
        )
    result_lines.append(f"Текущий баланс: <b>{balance_text} $</b>")

    states.pop(message.from_user.id)
    
    # Show bet section again after result - combined with result message
    try:
        settings = db.get_settings()
        rules = GAME_RULES[game_key]
        bet_config = get_bet_config(game_key, bet_type)
        if bet_config:
            header = f"{rules['emoji']} <b>{rules['label']}</b> → {bet_config.get('title', bet_type)}"
            description = bet_config.get("description", "")
            target_type = bet_config.get("target_type")
            
            if target_type == "number":
                markup = build_number_keyboard(game_key)
            elif target_type == "choice":
                markup = build_choice_keyboard(game_key, bet_type, bet_config)
            else:
                markup = build_bet_type_keyboard(game_key, settings)
                
            # Combine result with game menu
            combined_lines = result_lines.copy()
            combined_lines.append("")
            combined_lines.append("━━━━━━━━━━━━━━━━")
            combined_lines.append("")
            combined_lines.append(header)
            if description:
                combined_lines.append(description)
            combined_lines.append("")
            prompt = bet_config.get("target_prompt", "Сделайте выбор")
            combined_lines.append(prompt)
            
            if target_type == "choice":
                options = bet_config.get("targets") or []
                if options:
                    option_details: List[Tuple[Decimal, str, Dict[str, Any]]] = []
                    for option in options:
                        if option.get("hidden"):
                            continue
                        label = option.get("label") or option.get("key")
                        if not label:
                            continue
                        multiplier, _ = get_option_multiplier(settings, game_key, bet_type, option)
                        option_details.append((multiplier, label, option))
                    for multiplier, label, option in sorted(option_details, key=lambda item: item[0], reverse=True):
                        line = f"• {label} — x{multiplier:.2f}"
                        if (
                            game_key == "darts"
                            and bet_type == "outcome"
                            and option.get("key") == "hit"
                        ):
                            line += f" (🎯 буллсай до x{DARTS_BULLSEYE_MULTIPLIER:.2f})"
                        combined_lines.append(line)
            
            bot.send_message(
                message.chat.id,
                "\n".join(combined_lines),
                reply_markup=markup,
            )
        else:
            # If no bet config, just send result
            bot.send_message(message.chat.id, "\n".join(result_lines))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to show bet section: %s", exc)
        # Fallback to just showing result
        bot.send_message(message.chat.id, "\n".join(result_lines))
    
    panel_message_id = user_state.payload.get("message_id")
    if panel_message_id:
        try:
            user = db.ensure_user(message.from_user)
            settings = db.get_settings()
            overview_text = build_play_overview_text(user, settings)
            markup = build_play_keyboard(settings)
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=int(panel_message_id),
                text=overview_text,
                reply_markup=markup,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to restore play panel: %s", exc)


def should_reduce_win_chance() -> Tuple[bool, float]:
    """
    Проверяет, нужно ли понизить шансы выигрыша на основе owner_profit_margin.
    Возвращает (нужно_ли_понизить, множитель_шанса).
    Множитель_шанса от 0.0 до 1.0, где 1.0 = нормальные шансы, 0.5 = шансы уменьшены на 50%.
    """
    settings = db.get_settings()
    
    # Получаем owner_profit_margin
    try:
        owner_profit_margin = Decimal(settings.get("owner_profit_margin", DEFAULT_SETTINGS["owner_profit_margin"]))
    except (InvalidOperation, ValueError, TypeError):
        owner_profit_margin = Decimal("0")
    
    if owner_profit_margin <= Decimal("0"):
        # Если профит маржа не задана или равна 0, не понижаем шансы
        return False, 1.0
    
    # Получаем статистику
    stats = db.get_bot_stats()
    total_deposits = Decimal(str(stats.get("total_deposits", 0)))
    total_withdrawals = Decimal(str(stats.get("total_withdrawals", 0)))
    
    # Вычисляем текущий профит владельца
    current_profit = total_deposits - total_withdrawals
    
    # Вычисляем процент использования маржи
    if owner_profit_margin > Decimal("0"):
        margin_usage = float(current_profit / owner_profit_margin)
    else:
        margin_usage = 1.0
    
    logger.debug(
        "Profit check: deposits=%s, withdrawals=%s, current_profit=%s, owner_margin=%s, usage=%.2f%%",
        total_deposits,
        total_withdrawals,
        current_profit,
        owner_profit_margin,
        margin_usage * 100,
    )
    
    # Если текущий профит больше маржи, шансы нормальные
    if current_profit >= owner_profit_margin:
        return False, 1.0
    
    # Если профит меньше маржи, понижаем шансы
    # Используем линейное снижение вместо квадратичного для более честной игры
    # margin_usage от 0 до 1, где:
    # 1.0 = профит равен марже (нормальные шансы)
    # 0.5 = профит 50% от маржи (шансы уменьшены до 60%)
    # 0.0 = профит 0 или отрицательный (минимальные шансы 30%)
    
    # Используем формулу: chance_multiplier = 0.3 + 0.7 * margin_usage
    # Это даёт минимальный шанс 30% и максимальный 100%
    chance_multiplier = max(0.3, min(1.0, 0.3 + 0.7 * margin_usage))
    
    return True, chance_multiplier


def evaluate_bet(game_key: str, result_value: int, bet_type: str, target: str) -> bool:
    winning_values = winning_values_for_bet(game_key, bet_type, target)
    if not winning_values:
        logger.warning(
            "evaluate_bet: no winning values for game=%s bet_type=%s target=%s",
            game_key,
            bet_type,
            target,
        )
        return False

    is_win = result_value in winning_values
    logger.info(
        "evaluate_bet: game=%s bet_type=%s target=%s result_value=%s -> %s",
        game_key,
        bet_type,
        target,
        result_value,
        "WIN" if is_win else "LOSS",
    )
    return is_win


def apply_bet_result(
    user_id: int,
    stake: Decimal,
    payout: Decimal,
    game_key: str,
    bet_type: str,
    target: str,
    result_value: int,
    did_win: bool,
    used_multiplier: Decimal,
    multiplier_key: Optional[str],
) -> float:
    """Persist bet result and return updated balance."""

    if multiplier_key:
        try:
            db.get_setting(multiplier_key)
        except KeyError:
            db.set_setting(multiplier_key, f"{used_multiplier:.2f}")
    else:
        fallback_key = multiplier_setting_key(game_key, bet_type)
        try:
            db.get_setting(fallback_key)
        except KeyError:
            db.set_setting(fallback_key, f"{used_multiplier:.2f}")
    net_win = max(payout - stake, Decimal("0"))
    balance_delta = payout - stake

    db.update_user_balance(
        user_id,
        delta_balance=balance_delta,
        delta_winnings=net_win,
        delta_bets=1,
    )
    db.record_bet(
        user_id=user_id,
        game_key=game_key,
        bet_type=bet_type,
        bet_target=target,
        stake=stake,
        multiplier=used_multiplier,
        result_value=result_value,
        payout=net_win,
        result="win" if did_win else "lose",
    )
    
    # Обработка реферальных выплат при проигрыше
    if not did_win:
        user = db.get_user(user_id)
        referrer_id = user["referrer_id"] if "referrer_id" in user.keys() else None
        if referrer_id:
            try:
                settings = db.get_settings()
                ref_percentage = Decimal(settings.get("referral_percentage", "15.0"))
                ref_amount = (stake * ref_percentage / Decimal("100")).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
                if ref_amount > Decimal("0"):
                    db.add_referral_earning(
                        referrer_id, 
                        user_id, 
                        ref_amount, 
                        f"Реферальный доход с проигрыша {format_money(stake)} $"
                    )
                    logger.info(
                        "Referral earning: %s $ added to user %s from user %s loss of %s $",
                        format_money(ref_amount),
                        referrer_id,
                        user_id,
                        format_money(stake)
                    )
            except Exception as exc:
                logger.error("Failed to process referral earning: %s", exc, exc_info=True)
    
    return float(db.get_user(user_id)["balance"])


def adjust_multiplier_for_outcome(
    game_key: str,
    bet_type: str,
    target: str,
    result_value: int,
    base_multiplier: Decimal,
) -> Decimal:
    if (
        game_key == "darts"
        and bet_type == "outcome"
        and target == "hit"
        and result_value == DARTS_BULLSEYE_VALUE
    ):
        return DARTS_BULLSEYE_MULTIPLIER
    if (
        game_key == "darts"
        and bet_type == "outcome"
        and target == "hit"
        and result_value in [2, 3, 4, 5]
    ):
        settings = db.get_settings()
        edge_multiplier, _ = resolve_multiplier(
            settings,
            "darts",
            "outcome",
            target_key="edge",
            explicit_key="darts_outcome_multiplier_edge",
        )
        return edge_multiplier
    if (
        game_key == "basket"
        and bet_type == "outcome"
        and target == "hit"
        and result_value == 5
    ):
        settings = db.get_settings()
        swish_multiplier, _ = resolve_multiplier(
            settings,
            "basket",
            "outcome",
            target_key="swish",
            explicit_key="basket_outcome_multiplier_swish",
        )
        return swish_multiplier
    return base_multiplier


def process_deposit_amount(message: types.Message, user_state: PendingState) -> None:
    logger.info("=== DEPOSIT FLOW START === User %s submitted deposit amount text '%s'", message.from_user.id, message.text)
    logger.info("User state: %s", user_state)
    try:
        amount = decimal_from_text(message.text)
        logger.info("Parsed amount: %s", amount)
        if amount <= 0:
            logger.warning("Amount is zero or negative: %s", amount)
            raise InvalidOperation
    except (InvalidOperation, ValueError) as e:
        logger.error("Failed to parse deposit amount from user %s: %s, error: %s", message.from_user.id, message.text, e)
        bot.send_message(message.chat.id, "Введите корректную сумму\nНажмите кнопку «Назад» для отмены.")
        logger.info("User %s entered invalid amount, waiting for correct input", message.from_user.id)
        return

    settings = db.get_settings()
    min_deposit = Decimal(settings.get("min_deposit", DEFAULT_SETTINGS["min_deposit"]))
    logger.info("Min deposit: %s, requested amount: %s", min_deposit, amount)
    if amount < min_deposit:
        logger.warning("User %s deposit amount %s below minimum %s", message.from_user.id, amount, min_deposit)
        bot.send_message(message.chat.id, f"Минимальная сумма пополнения {min_deposit:.2f} $\nНажмите кнопку «Назад» для отмены.")
        logger.info("User %s entered below minimum, waiting for correct input", message.from_user.id)
        return

    if not crypto_pay_client.is_configured:
        logger.error("Crypto Pay client not configured! User %s cannot deposit", message.from_user.id)
        bot.send_message(
            message.chat.id,
            "Пополнение временно недоступно. Обратитесь к администратору.",
        )
        states.pop(message.from_user.id)
        logger.info("State cleared for user %s - crypto pay not configured", message.from_user.id)
        panel_message_id = user_state.payload.get("message_id")
        if panel_message_id:
            render_personal_cabinet_inline(
                message.chat.id, int(panel_message_id), message.from_user
            )
        return

    user = db.ensure_user(message.from_user)
    amount_str = format_money(amount)
    request_amount = float(amount.quantize(MONEY_QUANT))
    asset_setting = settings.get("crypto_pay_asset", DEFAULT_SETTINGS["crypto_pay_asset"])
    asset = (asset_setting or DEFAULT_SETTINGS["crypto_pay_asset"]).strip().upper()
    if not asset:
        asset = DEFAULT_SETTINGS["crypto_pay_asset"]
    currency_type_setting = settings.get(
        "crypto_pay_currency_type", DEFAULT_SETTINGS["crypto_pay_currency_type"]
    )
    currency_type = (currency_type_setting or "crypto").strip().lower()
    if currency_type not in {"fiat", "crypto"}:
        currency_type = "crypto"
    description = settings.get(
        "crypto_pay_description", DEFAULT_SETTINGS["crypto_pay_description"]
    )
    try:
        invoice_ttl = int(settings.get("crypto_pay_invoice_ttl", "900") or 0)
    except ValueError:
        invoice_ttl = 0
    payload = f"user={user['user_id']}&type=deposit&stamp={uuid4().hex}"
    invoice_params: Dict[str, Any] = {
        "amount": request_amount,
        "description": description,
        "payload": payload,
        "allow_comments": False,
        "allow_anonymous": False,
    }
    if invoice_ttl > 0:
        invoice_params["expires_in"] = invoice_ttl

    if currency_type == "fiat":
        invoice_params["currency_type"] = "fiat"
        fiat_setting = settings.get("crypto_pay_fiat", DEFAULT_SETTINGS["crypto_pay_fiat"])
        fiat_value = (fiat_setting or DEFAULT_SETTINGS["crypto_pay_fiat"]).strip().upper()
        invoice_params["fiat"] = fiat_value
        accepted_assets_raw = settings.get("crypto_pay_accepted_assets", "")
        accepted_assets = [
            part.strip().upper()
            for part in accepted_assets_raw.split(",")
            if part.strip()
        ]
        if accepted_assets:
            invoice_params["accepted_assets"] = ",".join(accepted_assets)
    else:
        invoice_params["currency_type"] = "crypto"
        invoice_params["asset"] = asset

    logger.info(
        "Creating deposit invoice for user %s amount %s (%s) asset=%s ttl=%s",
        user["user_id"],
        amount_str,
        currency_type,
        invoice_params.get("asset") or invoice_params.get("fiat"),
        invoice_ttl,
    )
    logger.info(
        "Full invoice params prepared: %s",
        crypto_pay_client._sanitize_mapping(invoice_params),
    )
    logger.info("About to call crypto_pay_client.create_invoice()...")

    try:
        logger.info("Calling crypto_pay_client.create_invoice() now...")
        invoice = crypto_pay_client.create_invoice(invoice_params)
        logger.info("SUCCESS! Invoice created: %s", crypto_pay_client._sanitize_mapping(invoice))
    except CryptoPayError as exc:
        logger.error("CryptoPayError for user %s: %s", user["user_id"], exc, exc_info=True)
        bot.send_message(message.chat.id, f"Не удалось создать счёт: {exc}")
        states.pop(message.from_user.id)
        logger.info("State cleared for user %s after CryptoPayError", message.from_user.id)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("UNEXPECTED ERROR while creating invoice for user %s: %s", user["user_id"], exc)
        bot.send_message(message.chat.id, "Произошла ошибка при создании счёта. Попробуйте позже.")
        states.pop(message.from_user.id)
        logger.info("State cleared for user %s after unexpected error", message.from_user.id)
        return

    invoice_id = invoice.get("invoice_id")
    invoice_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
        or invoice.get("pay_url")
    )
    invoice_asset = invoice.get("asset") or asset
    invoice_hash = invoice.get("hash")
    logger.info(
        "Invoice details extracted - ID: %s, URL: %s, asset: %s, hash: %s",
        invoice_id,
        invoice_url if invoice_url else "<NO_URL>",
        invoice_asset,
        invoice_hash if invoice_hash else "<NO_HASH>",
    )
    logger.info(
        "Full invoice raw response: %s",
        crypto_pay_client._sanitize_mapping(invoice),
    )
    transaction_comment = (
        f"CryptoPay invoice #{invoice_id}" if invoice_id is not None else "CryptoPay invoice"
    )
    logger.info("Creating transaction record in database...")
    transaction_id = db.create_transaction(
        user["user_id"],
        "deposit",
        amount,
        status="pending",
        comment=transaction_comment,
        asset=invoice_asset,
        external_id=str(invoice_id) if invoice_id is not None else None,
        external_url=invoice_url,
        payload=payload,
    )
    logger.info(
        "SUCCESS! Created Crypto Pay invoice %s for user %s (transaction %s) url=%s",
        invoice_id,
        user["user_id"],
        transaction_id,
        invoice_url or "<no_url>",
    )

    ttl_minutes = invoice_ttl // 60 if invoice_ttl else None
    lines = [
        "<b>💳 Пополнение создано</b>",
        f"Сумма: <b>{amount_str} $</b>",
        f"Актив: {invoice_asset}",
    ]
    if invoice_id is not None:
        lines.append(f"ID счёта: <code>{invoice_id}</code>")
    if invoice_hash:
        lines.append(f"Hash: <code>{invoice_hash}</code>")
    if ttl_minutes:
        lines.append(f"Оплатите в течение {ttl_minutes} мин.")
    lines.append("")
    if invoice_url:
        lines.append("Оплатите счёт через Crypto Bot, затем нажмите «Проверить оплату».")
    else:
        crypto_bot_username = (
            settings.get("crypto_bot_username", DEFAULT_SETTINGS["crypto_bot_username"])
            or DEFAULT_SETTINGS["crypto_bot_username"]
        ).lstrip("@")
        lines.append(
            f"Откройте @{crypto_bot_username} и оплатите счёт, затем нажмите «Проверить оплату»."
        )

    message_text = "\n".join(lines)
    logger.info("Preparing invoice message with %d buttons", 4 if invoice_url else 3)
    markup = types.InlineKeyboardMarkup(row_width=1)
    if invoice_url:
        logger.info("Adding invoice URL button: %s", invoice_url)
        markup.add(types.InlineKeyboardButton("🔗 Открыть счёт", url=invoice_url))
    markup.add(
        types.InlineKeyboardButton(
            "🔄 Проверить оплату", callback_data=f"invoice:check:{transaction_id}"
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            "❌ Отменить счёт", callback_data=f"invoice:cancel:{transaction_id}"
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ В личный кабинет", callback_data="wallet:refresh"
        )
    )
    logger.info("Invoice message prepared, text length: %d chars", len(message_text))

    panel_message_id = user_state.payload.get("message_id")
    logger.info("Panel message ID from state: %s", panel_message_id)
    delivered_inline = False
    if panel_message_id:
        logger.info("Attempting to edit message %s with invoice details...", panel_message_id)
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=int(panel_message_id),
                text=message_text,
                reply_markup=markup,
            )
            delivered_inline = True
            logger.info("SUCCESS! Edited message %s with invoice", panel_message_id)
        except ApiException as exc:  # pragma: no cover - Telegram API edge cases
            logger.warning("Failed to update deposit prompt with invoice: %s", exc, exc_info=True)

    if not delivered_inline:
        logger.info("Sending new message with invoice details...")
        sent_msg = bot.send_message(message.chat.id, message_text, reply_markup=markup)
        logger.info("SUCCESS! Sent new message %s with invoice", sent_msg.message_id)

    logger.info("Clearing state for user %s after successful invoice creation", message.from_user.id)
    states.pop(message.from_user.id)
    logger.info("=== DEPOSIT FLOW END === State cleared, invoice sent to user %s", message.from_user.id)

    if not delivered_inline and panel_message_id:
        logger.info("Attempting to restore personal cabinet in message %s...", panel_message_id)
        try:
            render_personal_cabinet_inline(
                message.chat.id, int(panel_message_id), message.from_user
            )
            logger.info("Personal cabinet restored successfully")
        except Exception as exc:  # noqa: BLE001 - logged for diagnostics only
            logger.warning("Failed to restore personal cabinet: %s", exc, exc_info=True)


def process_withdraw_amount(message: types.Message, user_state: PendingState) -> None:
    try:
        amount = decimal_from_text(message.text)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        bot.reply_to(message, "Введите корректную сумму\nНажмите кнопку «Назад» для отмены.")
        return

    settings = db.get_settings()
    min_withdraw = Decimal(settings.get("min_withdraw", DEFAULT_SETTINGS["min_withdraw"]))
    max_auto = Decimal(settings.get("max_auto_withdraw_amount", DEFAULT_SETTINGS["max_auto_withdraw_amount"]))
    raw_profit_margin = settings.get(
        "withdraw_profit_margin",
        DEFAULT_SETTINGS["withdraw_profit_margin"],
    )
    try:
        profit_margin = Decimal(str(raw_profit_margin)).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, TypeError):
        profit_margin = Decimal("0")
    if profit_margin < Decimal("0"):
        profit_margin = Decimal("0")
    if amount < min_withdraw:
        bot.reply_to(message, f"Минимальная сумма вывода {min_withdraw:.2f} $\nНажмите кнопку «Назад» для отмены.")
        return
    if amount > max_auto:
        bot.reply_to(message, f"Автовывод доступен только до {max_auto:.2f} $\nНажмите кнопку «Назад» для отмены.")
        return

    if not crypto_pay_client.is_configured:
        bot.reply_to(
            message,
            "Вывод временно недоступен. Обратитесь к администратору.",
        )
        states.pop(message.from_user.id)
        panel_message_id = user_state.payload.get("message_id")
        if panel_message_id:
            render_personal_cabinet_inline(
                message.chat.id, int(panel_message_id), message.from_user
            )
        return

    user = db.ensure_user(message.from_user)
    balance = row_decimal(user, "balance")
    if amount > balance:
        bot.reply_to(message, "Недостаточно средств на балансе\nНажмите кнопку «Назад» для отмены.")
        return

    transfer_amount = (amount - profit_margin).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
    if transfer_amount <= Decimal("0"):
        bot.reply_to(message, "Сумма слишком мала для вывода, попробуйте увеличить запрос.\nНажмите кнопку «Назад» для отмены.")
        return

    max_requests = int(settings.get("max_daily_auto_withdrawals", DEFAULT_SETTINGS["max_daily_auto_withdrawals"]))
    today_requests = db.count_withdrawals_today(user["user_id"])
    if today_requests >= max_requests:
        bot.reply_to(message, "Лимит заявок на вывод за день исчерпан\nНажмите кнопку «Назад» для отмены.")
        return

    asset = settings.get("crypto_pay_asset", DEFAULT_SETTINGS["crypto_pay_asset"])
    spend_id = uuid4().hex
    comment = f"Withdraw via CryptoBot"
    amount_str = format_money(amount)
    transfer_amount_str = decimal_to_str(transfer_amount)
    logger.info(
        "User %s requested withdraw %s, transfer %s, hidden profit %s",
        user["user_id"],
        amount_str,
        transfer_amount_str,
        f"{profit_margin:.2f}",
    )

    # Проверяем включен ли автовывод
    auto_withdraw_enabled = settings.get("auto_withdraw_enabled", "true").lower() in {"true", "1", "yes"}
    
    db.update_user_balance(
        user["user_id"],
        delta_balance=-amount,
        delta_withdraw=amount,
    )
    
    if not auto_withdraw_enabled:
        # Автовывод выключен - отправляем заявку админам на ручную обработку
        transaction_id = db.create_transaction(
            user["user_id"],
            "withdraw",
            amount,
            status="pending",
            comment="Manual withdraw - waiting for admin approval",
            asset=asset,
            payload=f"spend_id={spend_id}&profit={profit_margin:.2f}&transfer_amount={transfer_amount_str}&user_id={message.from_user.id}",
        )
        
        # Уведомляем пользователя
        send_withdraw_response_message(
            message,
            (
                f"✅ Заявка на вывод {amount_str} $ принята!\n\n"
                "Ваша заявка отправлена администратору на обработку.\n"
                "Ожидайте, вам будет отправлен платежный чек для вывода средств."
            ),
        )
        
        # Отправляем уведомление всем админам
        user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
        user_name = message.from_user.first_name or "Пользователь"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "✅ Обработать заявку",
                callback_data=f"admin:process_withdraw:{transaction_id}"
            ),
            types.InlineKeyboardButton(
                "❌ Отклонить заявку",
                callback_data=f"admin:reject_withdraw:{transaction_id}"
            )
        )
        
        admin_text = (
            "<b>🔔 Новая заявка на вывод</b>\n\n"
            f"Пользователь: {user_name} ({user_info})\n"
            f"Сумма: <b>{amount_str} $</b>\n"
            f"Баланс пользователя: <code>{balance:.2f} $</code>\n"
            f"Сумма к выплате: <code>{transfer_amount_str} $</code>\n"
            f"Комиссия: <code>{profit_margin:.2f} $</code>\n"
            f"Актив: <code>{asset}</code>\n"
            f"ID заявки: <code>{transaction_id}</code>"
        )
        
        # Отправляем уведомления всем админам из базы
        admins = db.get_all_admins()
        for admin in admins:
            admin_id = admin["user_id"]
            try:
                bot.send_message(admin_id, admin_text, reply_markup=markup)
            except ApiException as exc:
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)
        
        states.pop(message.from_user.id)
        panel_message_id = user_state.payload.get("message_id")
        if panel_message_id:
            render_personal_cabinet_inline(
                message.chat.id, int(panel_message_id), message.from_user
            )
        return
    
    # Автовывод включен - обрабатываем автоматически
    transaction_id = db.create_transaction(
        user["user_id"],
        "withdraw",
        amount,
        status="processing",
        comment="CryptoPay withdraw",
        asset=asset,
        payload=f"spend_id={spend_id}&profit={profit_margin:.2f}&transfer_amount={transfer_amount_str}",
    )

    # Используем createCheck вместо transfer
    check_payload = {
        "asset": asset,
        "amount": transfer_amount_str,
        "pin_to_user_id": message.from_user.id,
    }

    # Отправляем эмодзи перед созданием чека
    loading_msg = bot.send_message(message.chat.id, "💸")
    
    try:
        check = crypto_pay_client.create_check(check_payload)
        # Удаляем эмодзи после создания чека (через 1 сек)
        time.sleep(1)
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except ApiException:
            pass
    except CryptoPayError as exc:
        # Удаляем эмодзи в случае ошибки
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except ApiException:
            pass
        logger.warning("Failed to create Crypto Pay check: %s", exc)
        db.update_user_balance(
            user["user_id"],
            delta_balance=amount,
            delta_withdraw=-amount,
        )
        db.update_transaction(
            transaction_id,
            status="failed",
            comment=f"Ошибка создания чека: {exc}",
        )
        
        # Проверяем, не является ли ошибка связанной с недостатком средств
        error_msg = str(exc).lower()
        if "insufficient" in error_msg or "balance" in error_msg or "not enough" in error_msg or "недостаточно" in error_msg:
            bot.reply_to(
                message, 
                "❌ <b>Резерв для вывода пуст</b>\n\n"
                "Дождитесь пополнения резерва администратором.\n\n"
                "Ваш баланс был восстановлен.",
                parse_mode="HTML"
            )
        else:
            bot.reply_to(message, f"❌ Не удалось создать чек для вывода: {exc}")
        
        states.pop(message.from_user.id)
        panel_message_id = user_state.payload.get("message_id")
        if panel_message_id:
            render_personal_cabinet_inline(
                message.chat.id, int(panel_message_id), message.from_user
            )
        return

    check_id = check.get("check_id")
    check_url = check.get("bot_check_url")
    check_asset = check.get("asset", asset)
    db.update_transaction(
        transaction_id,
        status="completed",
        external_id=str(check_id) if check_id is not None else None,
        external_url=check_url,
        asset=check_asset,
        comment=f"CryptoPay check #{check_id}" if check_id is not None else "CryptoPay check",
    )
    
    # Отправляем пользователю ссылку на чек
    markup = types.InlineKeyboardMarkup()
    if check_url:
        markup.add(
            types.InlineKeyboardButton("💰 Получить чек", url=check_url)
        )
    
    send_withdraw_response_message(
        message,
        (
            f"✅ Вывод {amount_str} $ успешно обработан!\n\n"
            f"Сумма к получению: <b>{transfer_amount_str} {asset}</b>\n\n"
            "Нажмите кнопку ниже, чтобы активировать чек в @CryptoBot:"
        ),
        reply_markup=markup,
    )
    states.pop(message.from_user.id)
    panel_message_id = user_state.payload.get("message_id")
    if panel_message_id:
        render_personal_cabinet_inline(
            message.chat.id, int(panel_message_id), message.from_user
        )


def handle_invoice_callback(call: types.CallbackQuery) -> None:
    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Некорректный запрос")
        return
    _, action, raw_id = parts
    try:
        transaction_id = int(raw_id)
    except ValueError:
        bot.answer_callback_query(call.id, "Некорректный ID")
        return

    if action == "check":
        check_invoice_status(call, transaction_id)
    elif action == "check_reserve":
        check_reserve_invoice_status(call, transaction_id)
    elif action == "cancel":
        cancel_invoice(call, transaction_id)
    else:
        bot.answer_callback_query(call.id, "Команда не поддерживается")


def check_invoice_status(call: types.CallbackQuery, transaction_id: int) -> None:
    try:
        transaction = db.get_transaction(transaction_id)
    except ValueError:
        bot.answer_callback_query(call.id, "Счёт не найден")
        return

    if transaction["user_id"] != call.from_user.id:
        bot.answer_callback_query(call.id, "Нет доступа")
        return
    if transaction["direction"] != "deposit":
        bot.answer_callback_query(call.id, "Неверный тип операции")
        return
    if transaction["status"] == "completed":
        bot.answer_callback_query(call.id, "Оплата уже зачислена")
        return
    if not crypto_pay_client.is_configured:
        bot.answer_callback_query(call.id, "Crypto Pay не настроен")
        return

    external_id = transaction["external_id"]
    if not external_id:
        bot.answer_callback_query(call.id, "Счёт не привязан к Crypto Pay")
        return

    try:
        invoice = crypto_pay_client.get_invoice(int(external_id))
    except CryptoPayError as exc:
        logger.warning("Failed to fetch invoice %s: %s", external_id, exc)
        bot.answer_callback_query(call.id, f"Ошибка проверки: {exc}", show_alert=True)
        return

    if not invoice:
        bot.answer_callback_query(call.id, "Счёт не найден", show_alert=True)
        return
    logger.debug(
        "Invoice status payload for %s: %s",
        external_id,
        crypto_pay_client._sanitize_mapping(invoice),
    )
    if invoice.get("status") != "paid":
        bot.answer_callback_query(call.id, "Оплата ещё не поступила", show_alert=True)
        return

    amount = Decimal(str(transaction["amount"]))
    db.update_user_balance(
        transaction["user_id"],
        delta_balance=amount,
        delta_deposit=amount,
    )
    invoice_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
    )
    db.update_transaction(
        transaction_id,
        status="completed",
        comment="CryptoPay invoice paid",
        asset=invoice.get("paid_asset") or invoice.get("asset"),
        external_url=invoice_url,
    )
    logger.info(
        "Invoice %s paid for user %s (transaction %s)",
        external_id,
        transaction["user_id"],
        transaction_id,
    )

    success_text = (
        "<b>💳 Пополнение оплачено</b>\n"
        f"Сумма: <b>{format_money(amount)} $</b>\n"
        "Средства зачислены на ваш баланс."
    )
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=success_text,
        )
    except ApiException:
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id, reply_markup=None
            )
        except ApiException:
            pass

    bot.answer_callback_query(call.id, "Оплата зачислена ✅")
    bot.send_message(
        call.message.chat.id,
        f"💳 Оплата {format_money(amount)} $ получена! Баланс обновлён.",
    )


def check_reserve_invoice_status(call: types.CallbackQuery, transaction_id: int) -> None:
    """Проверка оплаты пополнения резерва админом."""
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Доступ запрещён")
        return
    
    try:
        transaction = db.get_transaction(transaction_id)
    except ValueError:
        bot.answer_callback_query(call.id, "Счёт не найден")
        return
    
    settings = db.get_settings()
    
    if transaction["direction"] != "reserve_deposit":
        bot.answer_callback_query(call.id, "Неверный тип операции")
        return
    
    if transaction["status"] == "completed":
        bot.answer_callback_query(call.id, "Оплата уже зачислена")
        return
    
    if not crypto_pay_client.is_configured:
        bot.answer_callback_query(call.id, "Crypto Pay не настроен")
        return
    
    # Извлекаем invoice_id из payload
    payload = transaction["payload"] if "payload" in transaction.keys() else ""
    invoice_id = None
    if payload:
        for part in payload.split("&"):
            if part.startswith("invoice_id="):
                invoice_id = part.split("=", 1)[1]
                break
    
    if not invoice_id:
        bot.answer_callback_query(call.id, "Счёт не привязан к Crypto Pay")
        return
    
    try:
        invoice = crypto_pay_client.get_invoice(int(invoice_id))
    except (CryptoPayError, ValueError) as exc:
        logger.warning("Failed to fetch reserve invoice %s: %s", invoice_id, exc)
        bot.answer_callback_query(call.id, f"Ошибка проверки: {exc}", show_alert=True)
        return
    
    if not invoice:
        bot.answer_callback_query(call.id, "Счёт не найден", show_alert=True)
        return
    
    logger.debug(
        "Reserve invoice status payload for %s: %s",
        invoice_id,
        crypto_pay_client._sanitize_mapping(invoice),
    )
    
    if invoice.get("status") != "paid":
        bot.answer_callback_query(call.id, "Оплата ещё не поступила", show_alert=True)
        return
    
    # Обновляем транзакцию
    amount = Decimal(str(transaction["amount"]))
    invoice_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
    )
    db.update_transaction(
        transaction_id,
        status="completed",
        comment="Reserve deposit completed via CryptoPay",
        asset=invoice.get("paid_asset") or invoice.get("asset"),
        external_url=invoice_url,
    )
    
    logger.info(
        "Reserve invoice %s paid by admin %s (transaction %s, amount=%s)",
        invoice_id,
        call.from_user.id,
        transaction_id,
        amount,
    )
    
    summary = get_reserve_balance_summary(settings)
    success_lines = [
        "<b>💎 Резерв пополнен</b>",
        f"Сумма: <b>{format_money(amount)} $</b>",
    ]
    if summary["error"]:
        success_lines.append(summary["error"])
    else:
        asset_code = summary["asset"] or resolve_reserve_asset(settings)
        total_text = summary.get("total")
        available_text = summary.get("available")
        onhold_text = summary.get("onhold")
        if total_text:
            success_lines.append(f"Текущий резерв (Crypto Pay): <b>{total_text}</b> {asset_code}")
        if onhold_text:
            if available_text:
                success_lines.append(f"Доступно: <b>{available_text}</b> {asset_code}")
            success_lines.append(f"На удержании: <b>{onhold_text}</b> {asset_code}")
        elif not total_text and available_text:
            success_lines.append(f"Доступно: <b>{available_text}</b> {asset_code}")
    success_lines.append("Средства добавлены в резерв приложения.")
    success_text = "\n".join(success_lines)
    
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=success_text,
        )
    except ApiException:
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id, reply_markup=None
            )
        except ApiException:
            pass
    
    bot.answer_callback_query(call.id, "Резерв пополнен ✅")
    
    notification_lines = [
        f"💎 Резерв успешно пополнен на {format_money(amount)} $!",
    ]
    if summary["error"]:
        notification_lines.append(summary["error"])
    else:
        asset_code = summary["asset"] or resolve_reserve_asset(settings)
        total_text = summary.get("total")
        available_text = summary.get("available")
        onhold_text = summary.get("onhold")
        if total_text:
            notification_lines.append(f"Текущий резерв: {total_text} {asset_code}")
        if onhold_text:
            if available_text:
                notification_lines.append(f"Доступно: {available_text} {asset_code}")
            notification_lines.append(f"На удержании: {onhold_text} {asset_code}")
        elif not total_text and available_text:
            notification_lines.append(f"Доступно: {available_text} {asset_code}")
    bot.send_message(call.message.chat.id, "\n".join(notification_lines))


def cancel_invoice(call: types.CallbackQuery, transaction_id: int) -> None:
    try:
        transaction = db.get_transaction(transaction_id)
    except ValueError:
        bot.answer_callback_query(call.id, "Счёт не найден")
        return

    if transaction["user_id"] != call.from_user.id:
        bot.answer_callback_query(call.id, "Нет доступа")
        return
    if transaction["direction"] != "deposit":
        bot.answer_callback_query(call.id, "Неверный тип операции")
        return
    if transaction["status"] == "completed":
        bot.answer_callback_query(call.id, "Счёт уже оплачен", show_alert=True)
        return
    if transaction["status"] == "cancelled":
        bot.answer_callback_query(call.id, "Счёт уже отменён")
        return

    external_id = transaction["external_id"]
    if crypto_pay_client.is_configured and external_id:
        try:
            invoice = crypto_pay_client.get_invoice(int(external_id))
            if invoice and invoice.get("status") == "paid":
                bot.answer_callback_query(call.id, "Счёт уже оплачен", show_alert=True)
                return
            crypto_pay_client.delete_invoice(int(external_id))
        except CryptoPayError as exc:
            logger.warning("Failed to cancel invoice %s: %s", external_id, exc)
            bot.answer_callback_query(call.id, f"Не удалось отменить: {exc}", show_alert=True)
            return

    db.update_transaction(
        transaction_id,
        status="cancelled",
        comment="CryptoPay invoice cancelled",
    )
    logger.info(
        "Invoice %s cancelled by user %s (transaction %s)",
        external_id,
        call.from_user.id,
        transaction_id,
    )
    cancel_text = (
        "<b>💳 Пополнение отменено</b>\n"
        "Счёт аннулирован."
    )
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=cancel_text,
        )
    except ApiException:
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id, reply_markup=None
            )
        except ApiException:
            pass

    bot.answer_callback_query(call.id, "Счёт отменён")
    bot.send_message(
        call.message.chat.id,
        "Счёт отменён. Вы можете создать новый запрос на пополнение.",
    )


def handle_cancel_callback(call: types.CallbackQuery) -> None:
    parts = call.data.split(":")
    if len(parts) < 2:
        bot.answer_callback_query(call.id, "Некорректная команда")
        return

    action = parts[1]
    response_text = None

    if action == "bet":
        states.pop(call.from_user.id)
        response_text = "Ставка отменена. Выберите новую опцию."
        bot.answer_callback_query(call.id, "Ставка отменена")
    elif action == "deposit":
        states.pop(call.from_user.id)
        response_text = "Пополнение отменено."
        bot.answer_callback_query(call.id, "Пополнение отменено")
        send_personal_cabinet(call.message)
    elif action == "withdraw":
        states.pop(call.from_user.id)
        response_text = "Вывод отменён."
        bot.answer_callback_query(call.id, "Вывод отменён")
        send_personal_cabinet(call.message)
    else:
        bot.answer_callback_query(call.id, "Команда не поддерживается")
        return

    if response_text:
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=response_text,
            )
        except ApiException:
            try:
                bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id, reply_markup=None
                )
            except ApiException:
                pass


def process_dice_test_chat_id(message: types.Message, user_state: PendingState) -> None:
    """Обработка ссылки на канал для теста кубика."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    text = (message.text or "").strip()
    
    # Проверка на отмену
    if text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "❌ Тест кубика отменен.")
        return
    
    # Парсим ссылку на канал
    channel_username = None
    
    # Убираем пробелы и возможные лишние символы
    text = text.strip()
    
    # Варианты форматов:
    # @channel_name
    # https://t.me/channel_name
    # t.me/channel_name
    # channel_name
    
    if text.startswith("@"):
        channel_username = text  # Уже в формате @channel
    elif "t.me/" in text:
        # Извлекаем имя канала из ссылки
        parts = text.split("t.me/")
        if len(parts) > 1:
            channel_username = "@" + parts[1].strip("/")
    else:
        # Просто имя канала без @
        channel_username = "@" + text
    
    if not channel_username or len(channel_username) < 2:
        bot.reply_to(
            message, 
            "❌ Некорректная ссылка на канал!\n\n"
            "Примеры правильных форматов:\n"
            "• <code>https://t.me/your_channel</code>\n"
            "• <code>@your_channel</code>\n"
            "• <code>t.me/your_channel</code>"
        )
        return
    
    # Проверяем, что бот может писать в этот канал
    try:
        chat = bot.get_chat(channel_username)
        chat_id = chat.id
        
        # Пробуем отправить действие (проверка прав)
        bot.send_chat_action(chat_id=chat_id, action="typing")
        
    except ApiException as e:
        bot.reply_to(
            message, 
            f"❌ Не могу получить доступ к каналу {channel_username}!\n\n"
            f"Убедитесь, что:\n"
            f"• Бот добавлен в канал как администратор\n"
            f"• У бота есть права на отправку сообщений\n"
            f"• Ссылка указана правильно\n\n"
            f"Ошибка: {str(e)}"
        )
        return
    
    target_chat_id = chat_id
    target_chat_name = chat.title if hasattr(chat, "title") and chat.title else channel_username or str(chat_id)
    
    payload = dict(user_state.payload)
    payload.update(
        target_chat_id=target_chat_id,
        target_chat_name=target_chat_name,
        target_chat_username=channel_username,
    )
    states.set(message.from_user.id, "awaiting_dice_test_number", **payload)
    
    confirmation_lines = [
        f"✅ Канал подтвержден: <b>{target_chat_name}</b>",
        f"ID: <code>{target_chat_id}</code>",
    ]
    if channel_username:
        confirmation_lines.append(f"Username: <code>{channel_username}</code>")
    confirmation_lines.extend(
        [
            "",
            "Теперь введите число от 1 до 6, которое должно выпасть на кубике.",
            "Бот будет отправлять dice в канал до тех пор, пока не выпадет нужное число.",
            "",
            "⚠️ <i>Внимание: все неудачные броски будут удаляться!</i>",
        ]
    )
    bot.reply_to(
        message,
        "\n".join(confirmation_lines),
        parse_mode="HTML",
    )


def process_dice_test(message: types.Message, user_state: PendingState) -> None:
    """Обработка теста кубика - отправляет dice пока не выпадет нужное число."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    text = (message.text or "").strip()
    
    # Проверка на отмену
    if text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "❌ Тест кубика отменен.")
        return
    
    # Проверка валидности числа
    try:
        target_number = int(text)
        if target_number < 1 or target_number > 6:
            bot.reply_to(message, "❌ Введите число от 1 до 6!")
            return
    except ValueError:
        bot.reply_to(message, "❌ Введите корректное число от 1 до 6!")
        return
    
    # Получаем target_chat_id из данных состояния
    payload = user_state.payload or {}
    target_chat_id = payload.get("target_chat_id")
    target_chat_name = payload.get("target_chat_name") or payload.get("target_chat_username") or target_chat_id
    
    if not target_chat_id:
        bot.reply_to(message, "❌ Ошибка: не найден канал. Начните заново.")
        states.pop(message.from_user.id)
        return
    
    states.pop(message.from_user.id)
    
    # Начинаем тест (сообщение отправляется в бот)
    status_msg = bot.send_message(
        chat_id=message.chat.id,
        text=(
            f"🎲 Начинаю тест кубика...\n"
            f"Цель: выбросить <b>{target_number}</b>\n"
            f"Канал: <b>{target_chat_name}</b>\n\n"
            f"Попытка 1..."
        )
    )
    
    attempts = 0
    max_attempts = 1000  # Ограничение для безопасности
    start_time = time.time()
    
    while attempts < max_attempts:
        attempts += 1
        
        # Отправляем dice в целевой канал (не в бот!)
        try:
            dice_msg = bot.send_dice(chat_id=target_chat_id, emoji="🎲")
            result = dice_msg.dice.value
        except ApiException as e:
            bot.send_message(
                chat_id=message.chat.id,
                text=f"❌ Ошибка при отправке dice в канал {target_chat_name}:\n{str(e)}"
            )
            return
        
        # Сразу удаляем dice сообщение из целевого чата, если не то число
        if result != target_number:
            try:
                bot.delete_message(chat_id=target_chat_id, message_id=dice_msg.message_id)
            except ApiException:
                pass
        
        # Обновляем статус в боте каждые 5 попыток или при успехе
        if result == target_number or attempts % 5 == 0:
            try:
                elapsed = time.time() - start_time
                bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    text=(
                        f"🎲 Тест кубика\n"
                        f"Цель: <b>{target_number}</b>\n"
                        f"Канал: <b>{target_chat_name}</b>\n\n"
                        f"Попыток: <b>{attempts}</b>\n"
                        f"Последний результат: <b>{result}</b>\n"
                        f"Время: <b>{elapsed:.1f}s</b>"
                    )
                )
            except ApiException:
                pass
        
        # Если выпало нужное число - показываем результат в боте и завершаем
        if result == target_number:
            elapsed = time.time() - start_time
            
            # Отправляем сообщение об успехе в бот
            bot.send_message(
                chat_id=message.chat.id,
                text=(
                    f"✅ <b>УСПЕХ!</b>\n\n"
                    f"🎯 Выпало число: <b>{target_number}</b>\n"
                    f"📊 Попыток потребовалось: <b>{attempts}</b>\n"
                    f"⏱ Время: <b>{elapsed:.2f} секунд</b>\n"
                    f"⚡️ Скорость: <b>{attempts/elapsed:.1f} попыток/сек</b>\n\n"
                    f"📈 Вероятность выпадения: <b>~16.67%</b> (1 из 6)\n\n"
                    f"🎲 Кубик с результатом <b>{target_number}</b> остался в канале <b>{target_chat_name}</b>"
                )
            )
            return
        
        # Небольшая задержка чтобы не словить rate limit
        time.sleep(0.05)
    
    # Если достигли лимита попыток (сообщение в бот)
    elapsed = time.time() - start_time
    bot.send_message(
        chat_id=message.chat.id,
        text=(
            f"⚠️ <b>Достигнут лимит попыток!</b>\n\n"
            f"🎯 Искомое число: <b>{target_number}</b>\n"
            f"📊 Попыток сделано: <b>{attempts}</b>\n"
            f"⏱ Время: <b>{elapsed:.2f} секунд</b>\n\n"
            f"💡 Число так и не выпало за {max_attempts} попыток."
        )
    )


def process_quick_game_send(call: types.CallbackQuery, game_key: str, outcome: str) -> None:
    """Быстрая отправка игры с контролируемым результатом в предустановленный канал."""
    if not db.is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Доступ запрещён")
        return
    
    # Получаем канал из настроек
    settings = db.get_settings()
    games_channel = settings.get("games_channel", "").strip()
    
    if not games_channel:
        bot.answer_callback_query(call.id, "❌ Канал не настроен!")
        bot.send_message(
            call.message.chat.id,
            "⚠️ Сначала настройте канал для игр в разделе:\n/admin → Ссылки и чаты → Канал для контроля игр"
        )
        return
    
    # Парсим канал
    channel_username = games_channel
    if not channel_username.startswith("@"):
        if "t.me/" in channel_username:
            parts = channel_username.split("t.me/")
            if len(parts) > 1:
                channel_username = "@" + parts[1].strip("/")
        else:
            channel_username = "@" + channel_username
    
    # Проверяем доступ к каналу
    try:
        chat = bot.get_chat(channel_username)
        target_chat_id = chat.id
        target_chat_name = chat.title if hasattr(chat, "title") and chat.title else channel_username
    except ApiException as e:
        bot.answer_callback_query(call.id, "❌ Нет доступа к каналу!")
        bot.send_message(
            call.message.chat.id,
            f"❌ Не могу получить доступ к каналу <code>{games_channel}</code>!\n\n"
            f"Убедитесь, что:\n"
            f"• Бот добавлен в канал как администратор\n"
            f"• У бота есть права на отправку сообщений\n\n"
            f"Ошибка: {str(e)}",
            parse_mode="HTML",
        )
        return
    
    if game_key not in GAME_RULES:
        bot.answer_callback_query(call.id, "❌ Неизвестная игра")
        return
    
    bot.answer_callback_query(call.id, "🎮 Начинаю отправку...")
    
    rules = GAME_RULES[game_key]
    emoji = rules.get("emoji", "🎮")
    label = rules.get("label", game_key)
    outcome_label = "ВЫИГРЫШ ✅" if outcome == "win" else "ПРОИГРЫШ ❌"
    
    # Начинаем отправку (сообщение отправляется в бот)
    status_msg = bot.send_message(
        chat_id=call.message.chat.id,
        text=(
            f"🎮 Начинаю отправку игры...\n"
            f"Игра: {emoji} <b>{label}</b>\n"
            f"Нужный результат: <b>{outcome_label}</b>\n"
            f"Канал: <b>{target_chat_name}</b>\n\n"
            f"Попытка 1..."
        ),
        parse_mode="HTML",
    )
    
    attempts = 0
    max_attempts = 1000  # Ограничение для безопасности
    start_time = time.time()
    
    # Определяем, какой результат нам нужен
    # Для простоты: если нужен выигрыш - ищем выигрышное значение, если проигрыш - любое другое
    # В данном случае мы просто отправляем игры, а контроль будет как в dice test
    
    # Используем emoji напрямую из rules, т.к. они соответствуют Telegram API
    telegram_emoji = emoji
    
    while attempts < max_attempts:
        attempts += 1
        
        # Отправляем игру в целевой канал
        try:
            game_msg = bot.send_dice(chat_id=target_chat_id, emoji=telegram_emoji)
            result = game_msg.dice.value
        except ApiException as e:
            bot.send_message(
                chat_id=call.message.chat.id,
                text=f"❌ Ошибка при отправке игры в канал {target_chat_name}:\n{str(e)}",
                parse_mode="HTML",
            )
            return
        
        # Определяем, является ли результат выигрышным
        # Для кубика: 6 = выигрыш, остальное = проигрыш
        # Для футбола: 3,4,5 = гол (выигрыш), остальное = промах (проигрыш)
        # Для дартса: 2-6 = попадание (выигрыш), 6 = буллсай (максимум), 1 = промах (проигрыш)
        # Для баскетбола: 4 или 5 = попадание (выигрыш), 3 = застрял (промах), 1-2 = далеко от центра (промах)
        
        is_winning_result = False
        if game_key == "dice":
            is_winning_result = (result == 6)
        elif game_key == "ball":
            is_winning_result = (result in {3, 4, 5})
        elif game_key == "darts":
            is_winning_result = (result >= 2)  # 2-6 попадание (1 = промах)
        elif game_key == "basket":
            is_winning_result = (result in {4, 5})  # 5 = свиш, 4 = попадание от щита
        
        # Проверяем, соответствует ли результат нужному исходу
        result_matches = (is_winning_result and outcome == "win") or (not is_winning_result and outcome == "lose")
        
        # Если результат не соответствует - удаляем сообщение
        if not result_matches:
            try:
                bot.delete_message(chat_id=target_chat_id, message_id=game_msg.message_id)
            except ApiException:
                pass
        
        # Обновляем статус в боте каждые 5 попыток или при успехе
        if result_matches or attempts % 5 == 0:
            try:
                elapsed = time.time() - start_time
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=status_msg.message_id,
                    text=(
                        f"🎮 Отправка игры...\n"
                        f"Игра: {emoji} <b>{label}</b>\n"
                        f"Нужный результат: <b>{outcome_label}</b>\n"
                        f"Канал: <b>{target_chat_name}</b>\n\n"
                        f"📊 Попытка {attempts}\n"
                        f"⏱ Время: {elapsed:.2f} сек\n"
                        f"🎲 Последний результат: {result}"
                    ),
                    parse_mode="HTML",
                )
            except ApiException:
                pass
        
        # Если результат подходит - завершаем
        if result_matches:
            # СОХРАНЯЕМ результат игры для использования в ставках!
            save_channel_game(game_key, target_chat_id, game_msg.message_id, result)
            
            elapsed = time.time() - start_time
            bot.send_message(
                chat_id=call.message.chat.id,
                text=(
                    f"✅ <b>Успех!</b>\n\n"
                    f"Игра: {emoji} <b>{label}</b>\n"
                    f"Результат: <b>{outcome_label}</b> (значение: {result})\n"
                    f"Канал: <b>{target_chat_name}</b>\n"
                    f"📊 Попыток: <b>{attempts}</b>\n"
                    f"⏱ Время: <b>{elapsed:.2f} секунд</b>\n\n"
                    f"💡 <i>Этот результат будет использован для ставок игроков!</i>"
                ),
                parse_mode="HTML",
            )
            return
        
        # Небольшая задержка между попытками
        if attempts < max_attempts:
            time.sleep(0.5)
    
    # Если достигли лимита попыток
    elapsed = time.time() - start_time
    bot.send_message(
        chat_id=call.message.chat.id,
        text=(
            f"⚠️ <b>Достигнут лимит попыток!</b>\n\n"
            f"Игра: {emoji} <b>{label}</b>\n"
            f"Нужный результат: <b>{outcome_label}</b>\n"
            f"📊 Попыток сделано: <b>{attempts}</b>\n"
            f"⏱ Время: <b>{elapsed:.2f} секунд</b>\n\n"
            f"💡 Нужный результат так и не выпал за {max_attempts} попыток."
        ),
        parse_mode="HTML",
    )


def handle_admin_callback(call: types.CallbackQuery) -> None:
    if not db.is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Доступ запрещён")
        return

    _, action = call.data.split(":", 1)
    settings = db.get_settings()

    if action == "financial":
        financial_keys = [
            "min_deposit",
            "min_withdraw",
            "min_bet",
            "min_reserve_topup",
            "max_daily_auto_withdrawals",
            "max_auto_withdraw_amount",
            "withdraw_profit_margin",
            "owner_profit_margin",
            "profit_target",
            "referral_percentage",
        ]
        text_lines = [
            "<b>💰 Финансовые настройки</b>",
            "Управление лимитами, минимальными суммами и профитом.",
            "",
        ]
        for key in financial_keys:
            display_name = setting_display_name(key)
            display_value = format_setting_display(key, settings.get(key))
            text_lines.append(f"• {display_name}: <code>{display_value}</code>")
        
        text_lines.append("")
        auto_withdraw_enabled = settings.get("auto_withdraw_enabled", "true").lower() in {"true", "1", "yes"}
        auto_status = "✅ Включен" if auto_withdraw_enabled else "❌ Выключен"
        text_lines.append(f"• Автовывод: <code>{auto_status}</code>")
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        for setting_key in financial_keys:
            markup.add(
                types.InlineKeyboardButton(
                    admin_setting_button_label(setting_key),
                    callback_data=f"admin:set:{setting_key}",
                )
            )
        
        # Кнопка для переключения автовывода
        toggle_text = "🔴 Выключить автовывод" if auto_withdraw_enabled else "🟢 Включить автовывод"
        markup.add(
            types.InlineKeyboardButton(
                toggle_text,
                callback_data="admin:toggle_auto_withdraw"
            )
        )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return

    if action == "multipliers":
        text_lines = [
            "<b>🎮 Игровые множители</b>",
            "Настройте коэффициенты выигрыша для каждой игры.",
            "",
        ]
        
        # Показываем текущие множители
        for game_key, rules in GAME_RULES.items():
            bet_types = get_bet_types(game_key)
            for bet_type, bet_config in bet_types.items():
                targets = bet_config.get("targets") or []
                for option in targets:
                    target_key = option.get("key")
                    if not target_key:
                        continue
                    setting_key = option.get("multiplier_key") or multiplier_setting_key(
                        game_key, bet_type, target_key
                    )
                    option_label = option.get("label") or target_key
                    multiplier_value = settings.get(setting_key, "N/A")
                    text_lines.append(f"{rules['emoji']} {option_label}: <code>{multiplier_value}</code>")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for game_key, rules in GAME_RULES.items():
            bet_types = get_bet_types(game_key)
            for bet_type, bet_config in bet_types.items():
                targets = bet_config.get("targets") or []
                added_target_buttons = False
                for option in targets:
                    target_key = option.get("key")
                    if not target_key:
                        continue
                    setting_key = option.get("multiplier_key") or multiplier_setting_key(
                        game_key, bet_type, target_key
                    )
                    option_label = option.get("label") or target_key
                    button_text = f"{rules['emoji']} {rules['label']} • {option_label}"
                    markup.add(
                        types.InlineKeyboardButton(
                            button_text, callback_data=f"admin:set:{setting_key}"
                        )
                    )
                    added_target_buttons = True
                if not added_target_buttons:
                    setting_key = multiplier_setting_key(game_key, bet_type)
                    button_text = (
                        f"{rules['emoji']} {rules['label']} • {bet_config.get('title', bet_type)}"
                    )
                    markup.add(
                        types.InlineKeyboardButton(
                            button_text, callback_data=f"admin:set:{setting_key}"
                        )
                    )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return

    if action == "mines_chance":
        if not VIP_FEATURES_ENABLED:
            bot.answer_callback_query(call.id, "Раздел доступен после покупки VIP.", show_alert=True)
            return
        current_value = settings.get("mines_safe_chance", "0").strip() or "0"
        display_value = format_setting_display("mines_safe_chance", current_value)
        text_lines = [
            "<b>🎯 Шанс безопасной клетки</b>",
            "",
            "Укажите вероятность (в процентах) того, что игрок откроет безопасную клетку в игре «Мины».",
            "Значение 0 выключает принудительный шанс и оставляет стандартный рандом.",
            "",
            f"Текущее значение: <code>{display_value}</code>",
        ]
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "✏️ Изменить шанс", callback_data="admin:set:mines_safe_chance"
            )
        )
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        bot.edit_message_text(
            text="\n".join(text_lines),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return

    if action == "links":
        link_keys = [
            "chat_link",
            "channel_link",
            "big_win_link",
            "reviews_link",
            "games_channel",
            "wins_channel",
            "crypto_bot_username",
            "crypto_pay_api_token",
        ]
        markup = types.InlineKeyboardMarkup(row_width=1)
        text_lines = [
            "<b>🔗 Ссылки и реквизиты</b>",
            "Нажмите кнопку, чтобы изменить параметр. Во время ввода можно отменить действие.",
            "",
        ]
        for setting_key in link_keys:
            markup.add(
                types.InlineKeyboardButton(
                    admin_setting_button_label(setting_key),
                    callback_data=f"admin:set:{setting_key}",
                )
            )
            display_name = setting_display_name(setting_key)
            display_value = format_setting_display(setting_key, settings.get(setting_key))
            text_lines.append(f"{display_name}: <code>{display_value}</code>")
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return

    if action == "reviews":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "Разместите отзывы в канале, ссылка на который указана в настройках."
        )
        return

    if action == "test_dice":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "<b>🎲 Тест кубика</b>\n\n"
            "Сначала добавьте бота в канал, куда нужно делать броски.\n\n"
            "Затем отправьте мне ссылку на этот канал.\n\n"
            "💡 Пример ссылок:\n"
            "• <code>https://t.me/your_channel</code>\n"
            "• <code>@your_channel</code>\n"
            "• <code>t.me/your_channel</code>"
        )
        states.set(call.from_user.id, "awaiting_dice_test_chat_id")
        return

    if action == "send_games":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "<b>📤 Отправка игр в канал</b>\n\n"
            "Сначала добавьте бота в канал, куда нужно отправлять игры.\n\n"
            "Затем отправьте мне ссылку на этот канал.\n\n"
            "💡 Пример ссылок:\n"
            "• <code>https://t.me/your_channel</code>\n"
            "• <code>@your_channel</code>\n"
            "• <code>t.me/your_channel</code>\n\n"
            "⚠️ <i>Бот будет отправлять игры в канал, пока не получит нужный результат (выигрыш или проигрыш), удаляя неподходящие попытки.</i>",
            parse_mode="HTML",
        )
        states.set(call.from_user.id, "awaiting_game_send_chat_id")
        return

    if action == "stats":
        stats = db.get_bot_stats()
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            (
                "<b>Статистика</b>\n"
                f"Новых за сегодня: {stats['new_today']}\n"
                f"Всего пользователей: {stats['total_users']}\n"
                f"Пополнений всего: {stats['total_deposits']:.2f} $\n"
                f"Выводов всего: {stats['total_withdrawals']:.2f} $"
            ),
        )
        return
    
    if action == "top_balance":
        """Handle top 20 users by balance."""
        top_users = db.get_top_users_by_balance(20)
        bot.answer_callback_query(call.id)
        
        if not top_users:
            text = "<b>🏆 Топ 20 по балансу</b>\n\nПока нет пользователей."
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton(
                    "◀️ Назад", callback_data="admin:back_to_menu"
                )
            )
            bot.send_message(
                call.message.chat.id,
                text,
                reply_markup=markup,
                parse_mode="HTML"
            )
            return
        
        lines = ["<b>🏆 Топ 20 пользователей по балансу</b>\n"]
        medals = ["🥇", "🥈", "🥉"]
        
        for idx, user in enumerate(top_users):
            if idx < 3:
                medal = medals[idx]
            else:
                medal = f"{idx + 1}."
            
            user_id = user["user_id"] if "user_id" in user.keys() else "?"
            username = user["username"] if "username" in user.keys() else None
            first_name = user["first_name"] if "first_name" in user.keys() else "Игрок"
            balance = row_decimal(user, "balance")
            
            # Формируем имя пользователя
            if username:
                user_name = f"@{username}"
            else:
                user_name = first_name or f"ID: {user_id}"
            
            lines.append(
                f"{medal} <b>{user_name}</b> — {format_money(balance)} $"
            )
        
        text = "\n".join(lines)
        
        # Добавляем кнопки управления
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "🗑 Очистить весь топ", callback_data="admin:confirm_reset_stats"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=markup,
            parse_mode="HTML"
        )
        return
    
    if action == "confirm_reset_stats":
        """Show confirmation dialog before resetting stats."""
        bot.answer_callback_query(call.id)
        
        # Get current stats count
        cursor = db._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE balance != 0 OR bets_total != 0")
        active_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM bets")
        total_bets = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transactions")
        total_transactions = cursor.fetchone()[0]
        
        text = (
            "⚠️ <b>ВНИМАНИЕ! ОЧИСТКА ТОПА</b> ⚠️\n\n"
            "Вы собираетесь очистить всю статистику бота:\n\n"
            f"• Обнулить балансы <b>{active_users}</b> пользователей\n"
            f"• Удалить <b>{total_bets}</b> записей ставок\n"
            f"• Удалить <b>{total_transactions}</b> транзакций\n"
            f"• Обнулить все выигрыши и депозиты\n\n"
            "⚠️ <b>ЭТО ДЕЙСТВИЕ НЕОБРАТИМО!</b> ⚠️\n\n"
            "Пользователи останутся в базе, но вся их игровая статистика будет удалена.\n\n"
            "Вы уверены?"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(
                "✅ Да, очистить всё", callback_data="admin:execute_reset_stats"
            ),
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:top_balance"
            )
        )
        
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=markup,
            parse_mode="HTML"
        )
        return
    
    if action == "execute_reset_stats":
        """Execute the stats reset after confirmation."""
        bot.answer_callback_query(call.id, "Очистка топа... Пожалуйста, подождите.")
        
        try:
            # Reset all stats
            users_reset, bets_deleted, transactions_deleted = db.reset_all_stats()
            
            text = (
                "✅ <b>Топ успешно очищен!</b>\n\n"
                f"📊 Результаты:\n"
                f"• Обнулено пользователей: <b>{users_reset}</b>\n"
                f"• Удалено ставок: <b>{bets_deleted}</b>\n"
                f"• Удалено транзакций: <b>{transactions_deleted}</b>\n\n"
                "Все пользователи могут начать игру заново!"
            )
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton(
                    "◀️ Назад в админ-меню", callback_data="admin:back_to_menu"
                )
            )
            
            bot.send_message(
                call.message.chat.id,
                text,
                reply_markup=markup,
                parse_mode="HTML"
            )
            
            logger.info(f"Admin {call.from_user.id} reset all stats: {users_reset} users, {bets_deleted} bets, {transactions_deleted} transactions")
            
        except Exception as e:
            logger.error(f"Error resetting stats: {e}")
            bot.send_message(
                call.message.chat.id,
                f"❌ Ошибка при очистке топа: {e}\n\nПопробуйте снова или обратитесь к разработчику.",
                parse_mode="HTML"
            )
        
        return
    
    if action == "broadcast":
        """Handle broadcast menu."""
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "<b>📢 Рассылка</b>\n\n"
            "Отправьте сообщение, которое нужно разослать всем пользователям.\n\n"
            "Вы можете отправить текст, фото с подписью, или любое другое сообщение.\n\n"
            "❌ Для отмены используйте команду /admin"
        )
        states.set(call.from_user.id, "awaiting_broadcast_message")
        return
    
    if action == "crypto_checks":
        """Handle Crypto Pay checks menu."""
        if not crypto_pay_client.is_configured:
            bot.answer_callback_query(call.id, "Crypto Pay не настроен", show_alert=True)
            return
        
        try:
            settings = db.get_settings()
            asset_filter = settings.get(
                "crypto_pay_asset",
                DEFAULT_SETTINGS["crypto_pay_asset"],
            )
            params: Dict[str, Any] = {"offset": 0, "count": 100}
            if asset_filter:
                params["asset"] = asset_filter
            
            # Get checks from Crypto Pay API
            response = crypto_pay_client.get_checks(params)
            checks = response.get("items", [])
            
            # Separate active and inactive checks
            active_checks = [
                c for c in checks if c.get("status") in CRYPTO_CHECK_ACTIVE_STATUSES
            ]
            inactive_checks = [
                c for c in checks if c.get("status") not in CRYPTO_CHECK_ACTIVE_STATUSES
            ]
            
            text_lines = [
                "<b>🧾 Управление чеками Crypto Pay</b>",
                f"Активный ассет: <b>{asset_filter or 'все'}</b>",
                f"Всего чеков: {len(checks)}",
                f"• Активных: {len(active_checks)}",
                f"• Неактивных: {len(inactive_checks)}",
                "",
                "Выберите список, чтобы просмотреть чеки.",
                "Удалять можно только активные / ожидающие чеки.",
            ]
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton(
                    f"✅ Активные чеки ({len(active_checks)})", callback_data="admin:checks_list:active"
                ),
                types.InlineKeyboardButton(
                    f"❌ Неактивные чеки ({len(inactive_checks)})", callback_data="admin:checks_list:inactive"
                ),
                types.InlineKeyboardButton(
                    "🔄 Обновить", callback_data="admin:crypto_checks"
                ),
                types.InlineKeyboardButton(
                    "◀️ Назад", callback_data="admin:back_to_menu"
                )
            )
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
            bot.answer_callback_query(call.id)
        except CryptoPayError as exc:
            logger.error("Failed to get checks: %s", exc)
            bot.answer_callback_query(call.id, f"Ошибка: {exc}", show_alert=True)
        return
    
    if action.startswith("checks_list:"):
        """Handle checks list display."""
        _, status_filter = action.split(":", 1)
        
        if not crypto_pay_client.is_configured:
            bot.answer_callback_query(call.id, "Crypto Pay не настроен", show_alert=True)
            return
        
        try:
            settings = db.get_settings()
            asset_filter = settings.get(
                "crypto_pay_asset",
                DEFAULT_SETTINGS["crypto_pay_asset"],
            )
            params: Dict[str, Any] = {"offset": 0, "count": 100}
            if asset_filter:
                params["asset"] = asset_filter
            
            # Get checks from Crypto Pay API
            response = crypto_pay_client.get_checks(params)
            checks = response.get("items", [])
            
            # Filter checks
            if status_filter == "active":
                filtered_checks = [
                    c for c in checks if c.get("status") in CRYPTO_CHECK_ACTIVE_STATUSES
                ]
                title = "✅ Активные чеки"
            else:
                filtered_checks = [
                    c for c in checks if c.get("status") not in CRYPTO_CHECK_ACTIVE_STATUSES
                ]
                title = "❌ Неактивные чеки"
            
            if not filtered_checks:
                text = f"<b>{title}</b>\n\nЧеков не найдено."
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin:crypto_checks"))
                
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=text,
                    reply_markup=markup,
                )
                bot.answer_callback_query(call.id)
                return
            
            # Show checks with pagination (first 10)
            text_lines = [
                f"<b>{title}</b> ({len(filtered_checks)})",
                "",
            ]
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            for check in filtered_checks[:10]:
                check_id = check.get("check_id")
                amount = check.get("amount", "?")
                asset = check.get("asset") or asset_filter or "?"
                status = (check.get("status") or "unknown").lower()
                status_label = CRYPTO_CHECK_STATUS_LABELS.get(status, status)
                is_active = status in CRYPTO_CHECK_ACTIVE_STATUSES
                prefix = "✅" if is_active else "⚪️"
                
                if check_id is None:
                    continue
                
                line = f"{prefix} <b>#{check_id}</b> — {amount} {asset} · {status_label}"
                text_lines.append(line)
                
                check_url = check.get("bot_check_url") or check.get("short_url") or check.get("pay_url")
                if check_url:
                    text_lines.append(f'   <a href="{escape(str(check_url))}">Открыть чек</a>')
                
                if status in CRYPTO_CHECK_DELETABLE_STATUSES:
                    markup.add(
                        types.InlineKeyboardButton(
                            f"🗑 Удалить #{check_id}",
                            callback_data=f"admin:delete_check:{status_filter}:{check_id}",
                        )
                    )
            
            if len(filtered_checks) > 10:
                text_lines.append(f"\n<i>Показано 10 из {len(filtered_checks)}</i>")
            
            markup.add(
                types.InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data=f"admin:checks_list:{status_filter}",
                )
            )
            markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin:crypto_checks"))
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
            bot.answer_callback_query(call.id)
        except CryptoPayError as exc:
            logger.error("Failed to get checks: %s", exc)
            bot.answer_callback_query(call.id, f"Ошибка: {exc}", show_alert=True)
        return
    
    if action.startswith("delete_check:"):
        """Handle check deletion."""
        parts = action.split(":")
        status_filter: Optional[str] = None
        if len(parts) == 3:
            _, status_filter, check_id_str = parts
        elif len(parts) == 2:
            _, check_id_str = parts
        else:
            bot.answer_callback_query(call.id, "Некорректные данные запроса", show_alert=True)
            return
        
        if not crypto_pay_client.is_configured:
            bot.answer_callback_query(call.id, "Crypto Pay не настроен", show_alert=True)
            return
        
        try:
            check_id = int(check_id_str)
            success = crypto_pay_client.delete_check(check_id)
            
            if success:
                bot.answer_callback_query(call.id, f"✅ Чек #{check_id} удален", show_alert=True)
                # Refresh the checks list
                if status_filter:
                    call.data = f"admin:checks_list:{status_filter}"
                else:
                    call.data = "admin:crypto_checks"
                handle_admin_callback(call)
            else:
                bot.answer_callback_query(call.id, f"❌ Не удалось удалить чек", show_alert=True)
        except (ValueError, CryptoPayError) as exc:
            logger.error("Failed to delete check: %s", exc)
            bot.answer_callback_query(call.id, f"Ошибка: {exc}", show_alert=True)
        return
    
    if action == "promo_codes":
        """Handle promo codes menu."""
        promo_codes = db.get_all_promo_codes()
        
        text_lines = [
            "<b>🎁 Управление промокодами</b>",
            f"Всего промокодов: {len(promo_codes)}",
            "",
        ]
        
        if promo_codes:
            for promo in promo_codes[:10]:  # Show first 10
                expires = promo["expires_at"] or "без срока"
                text_lines.append(
                    f"• <code>{promo['code']}</code> — {promo['amount']} $ "
                    f"({promo['used_count']}/{promo['max_uses']} исп.)"
                )
        else:
            text_lines.append("Промокодов пока нет.")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Создать промокод", callback_data="admin:create_promo"
            ),
            types.InlineKeyboardButton(
                "🗑 Удалить промокод", callback_data="admin:delete_promo"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action == "create_promo":
        """Start promo code creation."""
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "<b>🎁 Создание промокода</b>\n\n"
            "Введите код промокода (латинские буквы и цифры):\n\n"
            "Пример: <code>WELCOME2024</code>\n\n"
            "❌ Для отмены используйте команду /admin"
        )
        states.set(call.from_user.id, "awaiting_promo_code")
        return
    
    if action == "delete_promo":
        """Handle promo code deletion."""
        promo_codes = db.get_all_promo_codes()
        
        if not promo_codes:
            bot.answer_callback_query(call.id, "Нет промокодов для удаления")
            return
        
        text_lines = [
            "<b>🗑 Удаление промокода</b>",
            "",
            "Выберите промокод для удаления:",
            "",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for promo in promo_codes[:10]:
            markup.add(
                types.InlineKeyboardButton(
                    f"🗑 {promo['code']} ({promo['amount']} $)",
                    callback_data=f"admin:confirm_delete_promo:{promo['code']}"
                )
            )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:promo_codes"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action.startswith("confirm_delete_promo:"):
        """Confirm and delete promo code."""
        _, promo_code = action.split(":", 1)
        success = db.delete_promo_code(promo_code)
        
        if success:
            bot.answer_callback_query(call.id, f"Промокод {promo_code} удалён")
        else:
            bot.answer_callback_query(call.id, "Ошибка при удалении")
        
        # Refresh promo codes menu
        promo_codes = db.get_all_promo_codes()
        
        text_lines = [
            "<b>🎁 Управление промокодами</b>",
            f"Всего промокодов: {len(promo_codes)}",
            "",
        ]
        
        if promo_codes:
            for promo in promo_codes[:10]:
                text_lines.append(
                    f"• <code>{promo['code']}</code> — {promo['amount']} $ "
                    f"({promo['used_count']}/{promo['max_uses']} исп.)"
                )
        else:
            text_lines.append("Промокодов пока нет.")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Создать промокод", callback_data="admin:create_promo"
            ),
            types.InlineKeyboardButton(
                "🗑 Удалить промокод", callback_data="admin:delete_promo"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        return
    
    if action == "required_channels":
        """Handle required channels menu."""
        channels = db.get_all_required_channels()
        
        text_lines = [
            "<b>📢 Управление ОП каналами</b>",
            "",
            "Пользователи должны подписаться на эти каналы перед использованием бота.",
            f"Всего каналов: {len(channels)}",
            "",
        ]
        
        if channels:
            for channel in channels:
                text_lines.append(
                    f"• <b>{channel['channel_name']}</b>\n"
                    f"  ID: <code>{channel['channel_id']}</code>\n"
                    f"  Ссылка: {channel['channel_link']}"
                )
        else:
            text_lines.append("Обязательных каналов пока нет.")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Добавить канал", callback_data="admin:add_required_channel"
            ),
            types.InlineKeyboardButton(
                "🗑 Удалить канал", callback_data="admin:delete_required_channel"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action == "add_required_channel":
        """Start required channel addition."""
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "<b>📢 Добавление обязательного канала</b>\n\n"
            "Отправьте информацию о канале в следующем формате:\n\n"
            "<code>ID канала | Название | Ссылка</code>\n\n"
            "Пример:\n"
            "<code>@my_channel | Мой канал | https://t.me/my_channel</code>\n\n"
            "или\n\n"
            "<code>-1001234567890 | Мой приватный канал | https://t.me/+AbCdEfGhIjK</code>\n\n"
            "⚠️ Важно: бот должен быть администратором канала!\n\n"
            "❌ Для отмены используйте команду /admin"
        )
        states.set(call.from_user.id, "awaiting_required_channel_info")
        return
    
    if action == "delete_required_channel":
        """Handle required channel deletion."""
        channels = db.get_all_required_channels()
        
        if not channels:
            bot.answer_callback_query(call.id, "Нет каналов для удаления")
            return
        
        text_lines = [
            "<b>🗑 Удаление обязательного канала</b>",
            "",
            "Выберите канал для удаления:",
            "",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for channel in channels:
            markup.add(
                types.InlineKeyboardButton(
                    f"🗑 {channel['channel_name']}",
                    callback_data=f"admin:confirm_delete_channel:{channel['channel_id']}"
                )
            )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:required_channels"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action.startswith("confirm_delete_channel:"):
        """Confirm and delete required channel."""
        channel_id = action.split(":", 1)[1]
        success = db.remove_required_channel(channel_id)
        
        if success:
            bot.answer_callback_query(call.id, f"Канал удалён")
        else:
            bot.answer_callback_query(call.id, "Ошибка при удалении")
        
        # Refresh required channels menu
        channels = db.get_all_required_channels()
        
        text_lines = [
            "<b>📢 Управление ОП каналами</b>",
            "",
            "Пользователи должны подписаться на эти каналы перед использованием бота.",
            f"Всего каналов: {len(channels)}",
            "",
        ]
        
        if channels:
            for channel in channels:
                text_lines.append(
                    f"• <b>{channel['channel_name']}</b>\n"
                    f"  ID: <code>{channel['channel_id']}</code>\n"
                    f"  Ссылка: {channel['channel_link']}"
                )
        else:
            text_lines.append("Обязательных каналов пока нет.")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Добавить канал", callback_data="admin:add_required_channel"
            ),
            types.InlineKeyboardButton(
                "🗑 Удалить канал", callback_data="admin:delete_required_channel"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        return

    if action == "cancel_setting":
        states.pop(call.from_user.id)
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Изменение параметра отменено.",
                reply_markup=None,
            )
        except ApiException:
            try:
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                )
            except ApiException:
                pass
        bot.answer_callback_query(call.id, "Изменение отменено")
        return

    if action.startswith("set:"):
        _, setting_key = action.split(":", 1)
        setting_title = setting_display_name(setting_key)
        current_value = settings.get(setting_key, "")
        display_value = format_setting_display(setting_key, current_value)
        prompt_lines = [
            f"Изменение параметра <b>{setting_title}</b>",
            f"Ключ: <code>{setting_key}</code>",
            f"Текущее значение: <code>{display_value}</code>",
            "",
            "Отправьте новое значение одним сообщением.",
            "Введите «отмена», чтобы отменить без изменений, или воспользуйтесь кнопкой ниже.",
        ]
        if "token" in setting_key.lower():
            prompt_lines.append(
                "Для безопасности в истории отображается только часть токена."
            )
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:cancel_setting"
            )
        )
        bot.answer_callback_query(call.id)
        prompt_message = bot.send_message(
            call.message.chat.id,
            "\n".join(prompt_lines),
            reply_markup=markup,
        )
        states.set(
            call.from_user.id,
            "awaiting_admin_setting",
            setting_key=setting_key,
            setting_title=setting_title,
            current_value=current_value if current_value is not None else "",
            prompt_message_id=prompt_message.message_id,
            prompt_chat_id=prompt_message.chat.id,
        )
        return

    if action == "toggle_auto_withdraw":
        current_value = settings.get("auto_withdraw_enabled", "true").lower() in {"true", "1", "yes"}
        new_value = "false" if current_value else "true"
        db.set_setting("auto_withdraw_enabled", new_value)
        
        status_text = "выключен" if new_value == "false" else "включен"
        bot.answer_callback_query(call.id, f"Автовывод {status_text}")
        
        # Обновляем меню финансовых настроек
        handle_admin_callback(call)
        return

    if action == "reserve":
        summary = get_reserve_balance_summary(settings)
        min_reserve = Decimal(settings.get("min_reserve_topup", DEFAULT_SETTINGS["min_reserve_topup"]))
        
        text_lines = ["<b>💎 Резерв приложения</b>", ""]
        if summary["error"]:
            text_lines.append(summary["error"])
        else:
            asset_code = summary["asset"] or resolve_reserve_asset(settings)
            available_text = summary.get("available")
            onhold_text = summary.get("onhold")
            total_text = summary.get("total") or available_text
            if available_text and onhold_text:
                text_lines.append(f"💰 Доступно: <b>{available_text}</b> {asset_code}")
                text_lines.append(f"🔒 На удержании: <b>{onhold_text}</b> {asset_code}")
                if total_text:
                    text_lines.append(f"📊 Итого: <b>{total_text}</b> {asset_code}")
            elif total_text:
                text_lines.append(f"💰 Текущий резерв: <b>{total_text}</b> {asset_code}")
        
        text_lines.extend(
            [
                "",
                "🎯 Действия:",
                f"• Пополнить резерв (мин. {format_money(min_reserve)} $)",
                "• Управлять чеками Crypto Pay",
                "• Изменить минимальную сумму",
            ]
        )
    
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "💵 Пополнить резерв", callback_data="admin:topup_reserve"
            ),
            types.InlineKeyboardButton(
                "🧾 Управление чеками", callback_data="admin:manage_checks"
            ),
            types.InlineKeyboardButton(
                f"⚙️ Изменить мин. сумму ({format_money(min_reserve)} $)", callback_data="admin:set:min_reserve_topup"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action == "topup_reserve":
        """Start reserve top-up process."""
        min_reserve = Decimal(settings.get("min_reserve_topup", DEFAULT_SETTINGS["min_reserve_topup"]))
        
        bot.answer_callback_query(call.id)
        
        text_lines = [
            "<b>💵 Пополнение резерва</b>",
            "",
            f"Введите сумму пополнения в USD (минимум {format_money(min_reserve)} $):",
        ]
    
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:cancel_reserve"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:reserve"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        
        states.set(
            call.from_user.id,
            "awaiting_reserve_amount",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return
    
    if action == "manage_checks":
        """Show Crypto Pay checks management."""
        bot.answer_callback_query(call.id, "Загрузка чеков...")
        
        if not crypto_pay_client.is_configured:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="❌ Crypto Pay API не настроен. Настройте токен в разделе 'Ссылки и чаты'.",
            )
            return
        
        try:
            # Get checks from Crypto Pay
            checks_data = crypto_pay_client.get_checks({"count": 20})
            checks = checks_data.get("items", [])
            
            text_lines = ["<b>🧾 Управление чеками Crypto Pay</b>", ""]
            
            if not checks:
                text_lines.append("📭 Активных чеков нет.")
            else:
                text_lines.append(f"📋 Всего чеков: <b>{len(checks)}</b>")
                text_lines.append("")
                
                for idx, check in enumerate(checks[:10], 1):
                    check_id = check.get("check_id", "?")
                    asset = check.get("asset", "?")
                    amount = check.get("amount", "0")
                    status = check.get("status", "?")
                    
                    # Эмодзи для статуса
                    status_emoji = {
                        "active": "🟢",
                        "activated": "✅",
                    }.get(status, "⚪")
                    
                    text_lines.append(
                        f"{idx}. {status_emoji} ID: <code>{check_id}</code>\n"
                        f"   💰 {amount} {asset} • {status}"
                    )
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            
            if checks:
                markup.add(
                    types.InlineKeyboardButton(
                        "🗑 Удалить чек", callback_data="admin:delete_check_prompt"
                    )
                )
            
            markup.add(
                types.InlineKeyboardButton(
                    "🔄 Обновить", callback_data="admin:manage_checks"
                ),
                types.InlineKeyboardButton(
                    "◀️ Назад", callback_data="admin:reserve"
                )
            )
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
                parse_mode="HTML"
            )
            
        except CryptoPayError as e:
            logger.error(f"Error fetching checks: {e}")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ Ошибка при загрузке чеков: {e}",
                parse_mode="HTML"
            )
        return

    if action == "cancel_reserve":
        states.pop(call.from_user.id)
        bot.answer_callback_query(call.id, "Пополнение резерва отменено")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад к резерву", callback_data="admin:reserve"
            )
        )
        
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Пополнение резерва отменено.",
                reply_markup=markup,
            )
        except ApiException:
            pass
        return
    
    if action == "delete_check_prompt":
        """Prompt for check ID to delete."""
        bot.answer_callback_query(call.id)
        
        text = (
            "<b>🗑 Удаление чека</b>\n\n"
            "Введите ID чека, который хотите удалить.\n\n"
            "Пример: <code>12345</code>\n\n"
            "❌ Для отмены используйте /admin"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:manage_checks"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML"
        )
        
        states.set(call.from_user.id, "awaiting_check_id_to_delete")
        return

    if action == "design":
        """Handle design menu - manage section photos."""
        current_state = states.peek(call.from_user.id)
        if current_state and current_state.state == "awaiting_section_photo":
            states.pop(call.from_user.id)
        
        section_keys = ["start", "play", "cabinet", "about", "referral", "top", "dice", "ball", "darts", "basket", "mines", "withdraw", "wins"]
        section_names = {
            "start": "🚀 Приветствие",
            "play": "🎮 Играть",
            "cabinet": "👤 Личный кабинет",
            "about": "ℹ️ О боте",
            "referral": "💼 Партнерка",
            "top": "🏆 ТОП игроков",
            "dice": "🎲 Кубик",
            "ball": "⚽ Футбол",
            "darts": "🎯 Дартс",
            "basket": "🏀 Баскет",
            "mines": "💣 Мины",
            "withdraw": "💸 Вывод",
            "wins": "🏆 Победы",
        }
        
        text_lines = [
            "<b>🎨 Оформление разделов</b>",
            "Добавьте или удалите фото для различных разделов бота.",
            "",
        ]
        
        # Show current photos
        all_photos = db.get_all_section_photos()
        photo_dict = {p["section_key"]: p for p in all_photos}
        
        for key in section_keys:
            name = section_names.get(key, key)
            if key in photo_dict:
                text_lines.append(f"{name}: ✅ Установлено")
            else:
                text_lines.append(f"{name}: ❌ Не установлено")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        # Кнопка для изменения текста приветствия
        markup.add(
            types.InlineKeyboardButton(
                "📝 Изменить текст приветствия", callback_data="admin:set:welcome_text"
            )
        )
        
        for key in section_keys:
            name = section_names.get(key, key)
            has_photo = key in photo_dict
            btn_text = f"{'✏️' if has_photo else '➕'} {name}"
            markup.add(
                types.InlineKeyboardButton(
                    btn_text, callback_data=f"admin:design_section:{key}"
                )
            )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
        except ApiException as exc:
            logger.debug("Failed to edit design menu: %s", exc)
            bot.send_message(
                call.message.chat.id,
                "\n".join(text_lines),
                reply_markup=markup,
            )
        bot.answer_callback_query(call.id)
        return

    if action.startswith("design_section:"):
        """Handle editing a specific section's photo."""
        current_state = states.peek(call.from_user.id)
        if current_state and current_state.state == "awaiting_section_photo":
            states.pop(call.from_user.id)
        
        _, section_key = action.split(":", 1)
        section_names = {
            "start": "🚀 Приветствие",
            "play": "🎮 Играть",
            "cabinet": "👤 Личный кабинет",
            "about": "ℹ️ О боте",
            "referral": "💼 Партнерка",
            "top": "🏆 ТОП игроков",
            "dice": "🎲 Кубик",
            "ball": "⚽ Футбол",
            "darts": "🎯 Дартс",
            "basket": "🏀 Баскет",
            "mines": "💣 Мины",
            "withdraw": "💸 Вывод",
            "wins": "🏆 Победы",
        }
        section_name = section_names.get(section_key, section_key)
        
        photo = db.get_section_photo(section_key)
        
        text_lines = [
            f"<b>Настройка фото для: {section_name}</b>",
            "",
        ]
        
        if photo:
            text_lines.append("Текущее фото установлено.")
        else:
            text_lines.append("Фото не установлено.")
        
        text_lines.extend([
            "",
            "Используйте кнопки ниже для управления фото:",
        ])
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "📤 Загрузить/Заменить фото", callback_data=f"admin:design_upload:{section_key}"
            )
        )
        if photo:
            markup.add(
                types.InlineKeyboardButton(
                    "🗑️ Удалить фото", callback_data=f"admin:design_remove:{section_key}"
                )
            )
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:design"
            )
        )
        
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
        except ApiException as exc:
            logger.debug("Failed to edit design section menu: %s", exc)
            bot.send_message(
                call.message.chat.id,
                "\n".join(text_lines),
                reply_markup=markup,
            )
        bot.answer_callback_query(call.id)
        return

    if action.startswith("design_upload:"):
        """Prompt admin to upload a photo for section."""
        _, section_key = action.split(":", 1)
        section_names = {
            "start": "🚀 Приветствие",
            "play": "🎮 Играть",
            "cabinet": "👤 Личный кабинет",
            "about": "ℹ️ О боте",
            "referral": "💼 Партнерка",
            "top": "🏆 ТОП игроков",
            "dice": "🎲 Кубик",
            "ball": "⚽ Футбол",
            "darts": "🎯 Дартс",
            "basket": "🏀 Баскет",
            "mines": "💣 Мины",
            "withdraw": "💸 Вывод",
            "wins": "🏆 Победы",
        }
        section_name = section_names.get(section_key, section_key)
        
        text_lines = [
            f"<b>📤 Загрузка фото для раздела: {section_name}</b>",
            "",
            "Отправьте фото, которое хотите установить для этого раздела.",
            "",
            "📝 Советы:",
            "• Рекомендуемый размер: минимум 800x600 пикселей",
            "• Формат: JPG или PNG",
            "• Можно добавить подпись к фото",
            "",
            "Для отмены введите 'отмена' или нажмите кнопку ниже.",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data=f"admin:design_section:{section_key}"
            )
        )
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "\n".join(text_lines),
            reply_markup=markup,
        )
        
        states.set(
            call.from_user.id,
            "awaiting_section_photo",
            section_key=section_key,
        )
        return

    if action.startswith("design_remove:"):
        """Remove photo from section."""
        _, section_key = action.split(":", 1)
        if db.remove_section_photo(section_key):
            bot.answer_callback_query(call.id, "Фото удалено")
        else:
            bot.answer_callback_query(call.id, "Фото не найдено")
        
        # Return to design section menu
        call.data = f"admin:design_section:{section_key}"
        handle_admin_callback(call)
        return

    if action == "balance_management":
        """Handle balance management menu."""
        text_lines = [
            "<b>💳 Управление балансом пользователей</b>",
            "",
            "Выберите действие:",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Начислить баланс", callback_data="admin:add_balance"
            ),
            types.InlineKeyboardButton(
                "➖ Списать баланс", callback_data="admin:subtract_balance"
            ),
            types.InlineKeyboardButton(
                "🔒 Заблокировать пользователя", callback_data="admin:block_user"
            ),
            types.InlineKeyboardButton(
                "🔓 Разблокировать пользователя", callback_data="admin:unblock_user"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return

    if action == "manage_admins":
        """Handle admin management menu."""
        admins = db.get_all_admins()
        
        text_lines = [
            "<b>👥 Управление администраторами</b>",
            f"Всего администраторов: {len(admins)}",
            "",
        ]
        
        for admin in admins:
            username = admin["username"] or "нет"
            perms = db.get_admin_permissions(admin["user_id"])
            text_lines.append(f"• ID: {admin['user_id']} (@{username}) — {len(perms)} разделов")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Добавить админа", callback_data="admin:add_admin"
            ),
            types.InlineKeyboardButton(
                "➖ Удалить админа", callback_data="admin:remove_admin"
            ),
        )
        
        # Добавляем кнопки настройки разрешений для каждого админа
        for admin in admins:
            markup.add(
                types.InlineKeyboardButton(
                    f"⚙️ Разрешения для {admin['user_id']}",
                    callback_data=f"admin:edit_permissions:{admin['user_id']}"
                )
            )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action.startswith("edit_permissions:"):
        """Handle editing admin permissions."""
        _, admin_id_str = action.split(":", 1)
        try:
            admin_id = int(admin_id_str)
        except ValueError:
            bot.answer_callback_query(call.id, "Некорректный ID")
            return
        
        permissions = db.get_admin_permissions(admin_id)
        
        all_sections = [
            ("financial", "💰 Финансовые настройки"),
            ("multipliers", "🎮 Игровые множители"),
            ("links", "🔗 Ссылки и чаты"),
            ("design", "🎨 Оформление"),
            ("manage_admins", "👥 Управление админами"),
            ("user_management", "👤 Управление пользователями"),
            ("balance_management", "💳 Управление балансом"),
            ("reserve", "💎 Резерв приложения"),
            ("stats", "📊 Статистика"),
            ("reviews", "📝 Отзывы"),
            ("test_dice", "🎲 Тест кубика"),
            ("broadcast", "📢 Рассылка"),
            ("promo_codes", "🎁 Промокоды"),
        ]
        
        text_lines = [
            f"<b>⚙️ Настройка разрешений для админа {admin_id}</b>",
            "",
            "Выберите разделы, к которым у админа будет доступ:",
            "",
        ]
        
        for section_id, section_name in all_sections:
            status = "✅" if section_id in permissions else "❌"
            text_lines.append(f"{status} {section_name}")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for section_id, section_name in all_sections:
            status_emoji = "✅" if section_id in permissions else "❌"
            markup.add(
                types.InlineKeyboardButton(
                    f"{status_emoji} {section_name}",
                    callback_data=f"admin:toggle_permission:{admin_id}:{section_id}"
                )
            )
        
        markup.add(
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:manage_admins"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        return
    
    if action.startswith("toggle_permission:"):
        """Toggle admin permission for a section."""
        parts = action.split(":", 2)  # Split into max 3 parts
        if len(parts) < 3:
            bot.answer_callback_query(call.id, "Некорректный запрос")
            return
        
        _, admin_id_str, section_id = parts
        try:
            admin_id = int(admin_id_str)
        except ValueError:
            bot.answer_callback_query(call.id, "Некорректный ID")
            return
        
        permissions = db.get_admin_permissions(admin_id)
        
        if section_id in permissions:
            permissions.remove(section_id)
            status_msg = "отключен"
        else:
            permissions.append(section_id)
            status_msg = "включен"
        
        # Обновляем разрешения в базе
        db.update_admin_permissions(admin_id, ",".join(permissions))
        
        bot.answer_callback_query(call.id, f"Доступ {status_msg}")
        
        # Обновляем меню - вместо рекурсивного вызова используем прямое обновление
        try:
            all_sections = [
                ("financial", "💰 Финансовые настройки"),
                ("multipliers", "🎮 Игровые множители"),
                ("links", "🔗 Ссылки и чаты"),
                ("design", "🎨 Оформление"),
                ("manage_admins", "👥 Управление админами"),
                ("balance_management", "💳 Управление балансом"),
                ("reserve", "💎 Резерв приложения"),
                ("stats", "📊 Статистика"),
                ("reviews", "📝 Отзывы"),
                ("test_dice", "🎲 Тест кубика"),
            ]
            
            # Получаем обновленные разрешения
            updated_permissions = db.get_admin_permissions(admin_id)
            
            text_lines = [
                f"<b>⚙️ Настройка разрешений для админа {admin_id}</b>",
                "",
                "Выберите разделы, к которым у админа будет доступ:",
                "",
            ]
            
            for sec_id, sec_name in all_sections:
                status = "✅" if sec_id in updated_permissions else "❌"
                text_lines.append(f"{status} {sec_name}")
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            for sec_id, sec_name in all_sections:
                status_emoji = "✅" if sec_id in updated_permissions else "❌"
                markup.add(
                    types.InlineKeyboardButton(
                        f"{status_emoji} {sec_name}",
                        callback_data=f"admin:toggle_permission:{admin_id}:{sec_id}"
                    )
                )
            
            markup.add(
                types.InlineKeyboardButton(
                    "◀️ Назад", callback_data="admin:manage_admins"
                )
            )
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
        except Exception as e:
            logger.error(f"Error updating permissions menu: {e}")
            bot.answer_callback_query(call.id, "Ошибка обновления меню")
        return

    if action == "add_admin":
        """Prompt for new admin ID."""
        text_lines = [
            "<b>➕ Добавление администратора</b>",
            "",
            "Отправьте ID пользователя Telegram, которого хотите сделать администратором.",
            "Вы можете узнать ID через @userinfobot",
            "",
            "Введите 'отмена' для отмены.",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:manage_admins"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        
        states.set(
            call.from_user.id,
            "awaiting_add_admin_id",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return

    if action == "remove_admin":
        """Prompt for admin ID to remove."""
        admins = db.get_all_admins()
        
        text_lines = [
            "<b>➖ Удаление администратора</b>",
            "",
            "Текущие администраторы:",
        ]
        
        for admin in admins:
            username = admin["username"] or "нет"
            text_lines.append(f"• ID: {admin['user_id']} (@{username})")
        
        text_lines.extend([
            "",
            "Отправьте ID администратора, которого хотите удалить.",
            "Введите 'отмена' для отмены.",
        ])
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:manage_admins"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        
        states.set(
            call.from_user.id,
            "awaiting_remove_admin_id",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return

    if action == "add_balance":
        """Prompt for user ID to add balance."""
        text_lines = [
            "<b>➕ Начисление баланса</b>",
            "",
            "Отправьте ID пользователя Telegram, которому хотите начислить баланс.",
            "Вы можете узнать ID через @userinfobot",
            "",
            "Введите 'отмена' для отмены.",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:balance_management"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        
        states.set(
            call.from_user.id,
            "awaiting_add_balance_user_id",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return

    if action == "subtract_balance":
        """Prompt for user ID to subtract balance."""
        text_lines = [
            "<b>➖ Списание баланса</b>",
            "",
            "Отправьте ID пользователя Telegram, у которого хотите списать баланс.",
            "Вы можете узнать ID через @userinfobot",
            "",
            "Введите 'отмена' для отмены.",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:balance_management"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        
        states.set(
            call.from_user.id,
            "awaiting_subtract_balance_user_id",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return

    if action == "block_user":
        """Prompt for user ID to block."""
        text_lines = [
            "<b>🔒 Блокировка пользователя</b>",
            "",
            "Отправьте ID пользователя Telegram, которого хотите заблокировать.",
            "Заблокированный пользователь не сможет использовать бота.",
            "",
            "Введите 'отмена' для отмены.",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:balance_management"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        
        states.set(
            call.from_user.id,
            "awaiting_block_user_id",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return

    if action == "unblock_user":
        """Prompt for user ID to unblock."""
        text_lines = [
            "<b>🔓 Разблокировка пользователя</b>",
            "",
            "Отправьте ID пользователя Telegram, которого хотите разблокировать.",
            "",
            "Введите 'отмена' для отмены.",
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "❌ Отмена", callback_data="admin:balance_management"
            )
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(text_lines),
            reply_markup=markup,
        )
        bot.answer_callback_query(call.id)
        
        states.set(
            call.from_user.id,
            "awaiting_unblock_user_id",
            message_id=call.message.message_id,
            chat_id=call.message.chat.id,
        )
        return

    if action == "back_to_menu":
        states.pop(call.from_user.id)
        bot.answer_callback_query(call.id)
        
        markup = build_admin_menu_markup(call.from_user.id)
        
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="<b>🔧 Админ-панель</b>\n\nВыберите раздел для настройки:",
                reply_markup=markup,
            )
        except ApiException:
            bot.send_message(
                call.message.chat.id,
                "<b>🔧 Админ-панель</b>\n\nВыберите раздел для настройки:",
                reply_markup=markup,
            )
        return

    if action.startswith("process_withdraw:"):
        _, _, raw_transaction_id = action.split(":", 2)
        try:
            transaction_id = int(raw_transaction_id)
        except ValueError:
            bot.answer_callback_query(call.id, "Некорректный ID транзакции")
            return
        
        try:
            transaction = db.get_transaction(transaction_id)
        except ValueError:
            bot.answer_callback_query(call.id, "Транзакция не найдена")
            return
        
        if transaction["status"] != "pending":
            bot.answer_callback_query(call.id, "Заявка уже обработана")
            return
        
        # Извлекаем данные из payload
        payload = transaction["payload"] if "payload" in transaction.keys() else ""
        transfer_amount_str = None
        profit_margin = Decimal("0")
        user_id_from_payload = None
        
        for part in payload.split("&"):
            if part.startswith("transfer_amount="):
                transfer_amount_str = part.split("=", 1)[1]
            elif part.startswith("profit="):
                try:
                    profit_margin = Decimal(part.split("=", 1)[1])
                except (InvalidOperation, ValueError):
                    profit_margin = Decimal("0")
            elif part.startswith("user_id="):
                try:
                    user_id_from_payload = int(part.split("=", 1)[1])
                except ValueError:
                    pass
        
        if not transfer_amount_str:
            bot.answer_callback_query(call.id, "Ошибка данных транзакции")
            return
        
        amount = Decimal(str(transaction["amount"]))
        asset = transaction.get("asset") or settings.get("crypto_pay_asset", DEFAULT_SETTINGS["crypto_pay_asset"])
        try:
            user_row = db.get_user(transaction["user_id"])
            username = user_row["username"]
        except ValueError:
            user_row = None
            username = None
        user_label = (
            f"@{username}" if username else f"ID: {transaction['user_id']}"
        )
        prompt_lines = [
            "<b>🧾 Обработка заявки на вывод</b>",
            f"ID заявки: <code>{transaction_id}</code>",
            f"Пользователь: {user_label}",
            f"Сумма на вывод: <b>{format_money(amount)} $</b>",
            f"К выплате: <b>{transfer_amount_str} {asset}</b>",
            "",
            "Отправьте ссылку на чек/платёж, которую нужно переслать пользователю.",
            "Для отмены напишите «отмена».",
        ]
        prompt_message_id = call.message.message_id
        try:
            bot.edit_message_text(
                text="\n".join(prompt_lines),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("◀️ Назад", callback_data="admin:back_to_menu")
                ),
            )
        except ApiException:
            sent_prompt = bot.send_message(
                call.message.chat.id,
                "\n".join(prompt_lines),
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("◀️ Назад", callback_data="admin:back_to_menu")
                ),
            )
            prompt_message_id = sent_prompt.message_id
        states.set(
            call.from_user.id,
            "awaiting_manual_withdraw_link",
            transaction_id=transaction_id,
            user_id=transaction["user_id"],
            amount=str(transaction["amount"]),
            transfer_amount=transfer_amount_str,
            asset=asset,
            admin_prompt_chat_id=call.message.chat.id,
            admin_prompt_message_id=prompt_message_id,
        )
        bot.answer_callback_query(call.id, "Отправьте ссылку на чек.")
        return

    if action.startswith("reject_withdraw:"):
        _, _, raw_transaction_id = action.split(":", 2)
        try:
            transaction_id = int(raw_transaction_id)
        except ValueError:
            bot.answer_callback_query(call.id, "Некорректный ID транзакции")
            return
        
        try:
            transaction = db.get_transaction(transaction_id)
        except ValueError:
            bot.answer_callback_query(call.id, "Транзакция не найдена")
            return
        
        if transaction["status"] != "pending":
            bot.answer_callback_query(call.id, "Заявка уже обработана")
            return
        
        # Возвращаем средства пользователю
        amount = Decimal(str(transaction["amount"]))
        user_id = transaction["user_id"]
        
        db.update_user_balance(
            user_id,
            delta_balance=amount,
            delta_withdraw=-amount,
        )
        
        db.update_transaction(
            transaction_id,
            status="cancelled",
            comment="Rejected by admin",
        )
        
        # Уведомляем пользователя
        user_text = (
            f"❌ <b>Ваша заявка на вывод отклонена</b>\n\n"
            f"Сумма {format_money(amount)} $ возвращена на ваш баланс.\n"
            "Обратитесь к администратору для получения дополнительной информации."
        )
        
        try:
            bot.send_message(user_id, user_text)
        except ApiException as exc:
            logger.warning("Failed to notify user %s about rejection: %s", user_id, exc)
        
        # Обновляем сообщение админа
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"{call.message.text}\n\n❌ <b>Отклонено</b>\nСредства возвращены пользователю.",
                reply_markup=None,
            )
        except ApiException:
            pass
        
        bot.answer_callback_query(call.id, "Заявка отклонена, средства возвращены")
        logger.info(
            "Admin %s rejected withdraw %s for user %s",
            call.from_user.id,
            transaction_id,
            user_id,
        )
        return

    bot.answer_callback_query(call.id, "Раздел в разработке")


def process_admin_setting(message: types.Message, user_state: PendingState) -> None:
    global crypto_pay_token

    setting_key = user_state.payload.get("setting_key")
    if not setting_key:
        bot.reply_to(message, "Ошибка состояния")
        states.pop(message.from_user.id)
        return

    prompt_chat_id = user_state.payload.get("prompt_chat_id")
    prompt_message_id = user_state.payload.get("prompt_message_id")
    current_value = user_state.payload.get("current_value", "")
    setting_title = user_state.payload.get("setting_title") or setting_display_name(setting_key)

    incoming_text = (message.text or "").strip()
    if not incoming_text:
        bot.reply_to(message, "Значение не может быть пустым")
        states.pop(message.from_user.id)
        return

    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        if prompt_chat_id and prompt_message_id:
            try:
                bot.edit_message_text(
                    chat_id=prompt_chat_id,
                    message_id=prompt_message_id,
                    text="Изменение параметра отменено.",
                    reply_markup=None,
                )
            except ApiException:
                try:
                    bot.edit_message_reply_markup(
                        prompt_chat_id,
                        prompt_message_id,
                        reply_markup=None,
                    )
                except ApiException:
                    pass
        bot.reply_to(message, "Изменение отменено.")
        return

    value = incoming_text
    if "multiplier" in setting_key:
        try:
            numeric = Decimal(value.replace(",", "."))
        except InvalidOperation:
            bot.reply_to(message, "Введите число")
            states.pop(message.from_user.id)
            return
        value = f"{numeric:.2f}"
    elif setting_key == "mines_safe_chance":
        if not VIP_FEATURES_ENABLED:
            bot.reply_to(message, "Настройка доступна только после покупки VIP-статуса.")
            states.pop(message.from_user.id)
            return
        try:
            numeric = Decimal(value.replace(",", "."))
        except InvalidOperation:
            bot.reply_to(message, "Введите число от 0 до 100")
            states.pop(message.from_user.id)
            return
        if numeric < Decimal("0"):
            numeric = Decimal("0")
        if numeric > Decimal("100"):
            numeric = Decimal("100")
        numeric = numeric.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        value = f"{numeric:.2f}".rstrip("0").rstrip(".") or "0"

    db.set_setting(setting_key, value)
    if setting_key == "crypto_pay_api_token":
        crypto_pay_client.set_token(value)
        crypto_pay_token = value

    states.pop(message.from_user.id)

    confirmation_text = (
        f"✅ Параметр <b>{setting_title}</b> обновлён.\n"
        f"Было: <code>{format_setting_display(setting_key, current_value)}</code>\n"
        f"Стало: <code>{format_setting_display(setting_key, value)}</code>"
    )

    if prompt_chat_id and prompt_message_id:
        try:
            bot.edit_message_text(
                chat_id=prompt_chat_id,
                message_id=prompt_message_id,
                text=confirmation_text,
                reply_markup=None,
            )
        except ApiException:
            try:
                bot.edit_message_reply_markup(
                    prompt_chat_id,
                    prompt_message_id,
                    reply_markup=None,
                )
            except ApiException:
                pass

    bot.reply_to(message, confirmation_text)


def process_manual_withdraw_link(message: types.Message, user_state: PendingState) -> None:
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён.")
        states.pop(message.from_user.id)
        return
    incoming_text = (message.text or "").strip()
    if not incoming_text:
        bot.reply_to(message, "Ссылка не может быть пустой.")
        return
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        admin_chat_id = user_state.payload.get("admin_prompt_chat_id")
        admin_message_id = user_state.payload.get("admin_prompt_message_id")
        if admin_chat_id and admin_message_id:
            try:
                bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=int(admin_message_id),
                    text="Обработка заявки отменена.",
                    reply_markup=None,
                )
            except ApiException:
                pass
        bot.reply_to(message, "Обработка заявки отменена.")
        return
    if not incoming_text.lower().startswith(("http://", "https://")):
        bot.reply_to(message, "Пришлите ссылку, начинающуюся с http:// или https://")
        return
    transaction_id = user_state.payload.get("transaction_id")
    if not transaction_id:
        bot.reply_to(message, "Не удалось найти заявку. Попробуйте снова из админ-панели.")
        states.pop(message.from_user.id)
        return
    try:
        transaction = db.get_transaction(int(transaction_id))
    except (ValueError, sqlite3.Error):
        bot.reply_to(message, "Заявка не найдена или уже обработана.")
        states.pop(message.from_user.id)
        return
    if transaction["status"] != "pending":
        bot.reply_to(message, "Заявка уже обработана или отменена.")
        states.pop(message.from_user.id)
        return
    settings = db.get_settings()
    transfer_amount_str = user_state.payload.get("transfer_amount") or str(transaction["amount"])
    asset = user_state.payload.get("asset") or transaction.get("asset") or settings.get("crypto_pay_asset", DEFAULT_SETTINGS["crypto_pay_asset"])
    amount = Decimal(str(transaction["amount"]))
    try:
        transfer_amount = Decimal(str(transfer_amount_str))
    except InvalidOperation:
        transfer_amount = amount
    db.update_transaction(
        int(transaction_id),
        status="completed",
        external_url=incoming_text,
        comment="Manual withdraw link provided",
    )
    user_markup = types.InlineKeyboardMarkup(row_width=1)
    user_markup.add(types.InlineKeyboardButton("💳 Получить чек", url=incoming_text))
    user_text = (
        f"✅ <b>Ваша заявка на вывод одобрена!</b>\n\n"
        f"Сумма: <b>{format_money(amount)} $</b>\n"
        f"К получению: <b>{format_money(transfer_amount)} {asset}</b>\n\n"
        "Нажмите кнопку ниже, чтобы получить чек."
    )
    user_id = transaction["user_id"]
    try:
        bot.send_message(user_id, user_text, reply_markup=user_markup)
    except ApiException as exc:
        logger.warning("Failed to send manual withdraw check to user %s: %s", user_id, exc)
        bot.reply_to(
            message,
            "Чек сохранён, но не удалось отправить сообщение пользователю. Проверьте блокировку у пользователя.",
        )
    admin_chat_id = user_state.payload.get("admin_prompt_chat_id")
    admin_message_id = user_state.payload.get("admin_prompt_message_id")
    if admin_chat_id and admin_message_id:
        try:
            bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=int(admin_message_id),
                text="✅ Заявка обработана. Ссылка отправлена пользователю.",
                reply_markup=None,
            )
        except ApiException:
            pass
    bot.reply_to(message, "Ссылка отправлена пользователю.")
    logger.info(
        "Admin %s approved withdraw %s manually and sent link to user %s",
        message.from_user.id,
        transaction_id,
        user_id,
    )
    states.pop(message.from_user.id)

def process_reserve_amount(message: types.Message, user_state: PendingState) -> None:
    """Обработка суммы пополнения резерва админом."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Пополнение резерва отменено.")
        return
    
    settings = db.get_settings()
    min_reserve = Decimal(settings.get("min_reserve_topup", DEFAULT_SETTINGS["min_reserve_topup"]))
    
    try:
        amount = decimal_from_text(incoming_text)
        if amount < min_reserve:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        bot.reply_to(message, f"Введите корректную сумму (минимум {format_money(min_reserve)} USD)")
        states.pop(message.from_user.id)
        return
    
    # Проверяем настройки Crypto Pay
    if not crypto_pay_client.is_configured:
        bot.reply_to(
            message,
            "❌ Crypto Pay не настроен. Укажите API токен в настройках.",
        )
        states.pop(message.from_user.id)
        return
    
    # Создаем инвойс для пополнения резерва
    asset_setting = settings.get("crypto_pay_asset", DEFAULT_SETTINGS["crypto_pay_asset"])
    asset = (asset_setting or DEFAULT_SETTINGS["crypto_pay_asset"]).strip().upper()
    if not asset:
        asset = DEFAULT_SETTINGS["crypto_pay_asset"]
    
    currency_type_setting = settings.get(
        "crypto_pay_currency_type", DEFAULT_SETTINGS["crypto_pay_currency_type"]
    )
    currency_type = (currency_type_setting or "crypto").strip().lower()
    if currency_type not in {"crypto", "fiat"}:
        currency_type = "crypto"
    
    description = "Пополнение резерва приложения"
    
    try:
        invoice_ttl = int(settings.get("crypto_pay_invoice_ttl", "900") or 0)
    except ValueError:
        invoice_ttl = 900
    if invoice_ttl < 60:
        invoice_ttl = 900
    
    payload_dict = {
        "amount": decimal_to_str(amount),
        "description": description,
        "currency_type": currency_type,
    }
    
    if currency_type == "fiat":
        fiat_setting = settings.get("crypto_pay_fiat", DEFAULT_SETTINGS["crypto_pay_fiat"])
        fiat_value = (fiat_setting or DEFAULT_SETTINGS["crypto_pay_fiat"]).strip().upper()
        payload_dict["fiat"] = fiat_value
        accepted_assets_raw = settings.get("crypto_pay_accepted_assets", "")
        if accepted_assets_raw and accepted_assets_raw.strip():
            payload_dict["accepted_assets"] = accepted_assets_raw.strip()
    else:
        # asset parameter is only used when currency_type is "crypto"
        payload_dict["asset"] = asset
    
    payload_dict["expires_in"] = invoice_ttl
    
    logger.info("Creating reserve invoice with payload: %s", payload_dict)
    
    invoice_data = crypto_pay_client.create_invoice(payload_dict)
    if not invoice_data:
        bot.reply_to(
            message,
            "❌ Ошибка создания счёта. Проверьте настройки Crypto Pay.",
        )
        states.pop(message.from_user.id)
        return
    
    invoice_id = invoice_data.get("invoice_id")
    bot_invoice_url = invoice_data.get("bot_invoice_url")
    mini_app_invoice_url = invoice_data.get("mini_app_invoice_url")
    web_app_invoice_url = invoice_data.get("web_app_invoice_url")
    pay_url = invoice_data.get("pay_url")
    
    # Создаем транзакцию для резерва
    transaction_id = db.create_transaction(
        message.from_user.id,
        "reserve_deposit",
        amount,
        status="pending",
        comment="Reserve fund deposit",
        asset=asset,
        payload=f"invoice_id={invoice_id}",
    )
    
    states.pop(message.from_user.id)
    
    # Формируем сообщение с кнопками оплаты
    text_lines = [
        "<b>💎 Пополнение резерва</b>",
        f"Сумма: <b>{amount:.2f} USD</b>",
        f"Актив: <code>{asset}</code>",
        "",
        "Нажмите на кнопку ниже для оплаты через Crypto Pay:",
    ]
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    crypto_bot_username = (
        settings.get("crypto_bot_username", DEFAULT_SETTINGS["crypto_bot_username"])
        or DEFAULT_SETTINGS["crypto_bot_username"]
    )
    
    if bot_invoice_url:
        markup.add(
            types.InlineKeyboardButton(
                f"💳 Оплатить через @{crypto_bot_username}",
                url=bot_invoice_url,
            )
        )
    elif pay_url:
        markup.add(
            types.InlineKeyboardButton(
                "💳 Оплатить",
                url=pay_url,
            )
        )
    
    if mini_app_invoice_url:
        markup.add(
            types.InlineKeyboardButton(
                "📱 Оплатить в Mini App",
                url=mini_app_invoice_url,
            )
        )
    
    if web_app_invoice_url:
        markup.add(
            types.InlineKeyboardButton(
                "🌐 Оплатить в Web App",
                url=web_app_invoice_url,
            )
        )
    
    markup.add(
        types.InlineKeyboardButton(
            "✅ Проверить оплату",
            callback_data=f"invoice:check_reserve:{transaction_id}",
        )
    )
    
    bot.send_message(
        message.chat.id,
        "\n".join(text_lines),
        reply_markup=markup,
        parse_mode='HTML',
    )
    
    logger.info(
        "Reserve invoice created for admin %s: amount=%s, invoice_id=%s, transaction_id=%s",
        message.from_user.id,
        amount,
        invoice_id,
        transaction_id,
    )


def process_add_admin(message: types.Message, user_state: PendingState) -> None:
    """Обработка добавления нового администратора."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Добавление админа отменено.")
        return
    
    try:
        new_admin_id = int(incoming_text)
    except ValueError:
        bot.reply_to(message, "Введите корректный ID пользователя (только цифры)")
        states.pop(message.from_user.id)
        return
    
    # Проверяем, не является ли пользователь уже админом
    if db.is_admin(new_admin_id):
        bot.reply_to(message, f"Пользователь с ID {new_admin_id} уже является администратором")
        states.pop(message.from_user.id)
        return
    
    # Добавляем нового админа со всеми разрешениями по умолчанию
    all_permissions = "financial,multipliers,links,design,manage_admins,balance_management,reserve,crypto_checks,stats,top_balance,reviews,test_dice,broadcast,promo_codes,required_channels"
    db.add_admin(new_admin_id, added_by=message.from_user.id, permissions=all_permissions)
    
    # Пытаемся отправить уведомление новому админу
    try:
        bot.send_message(
            new_admin_id,
            "🎉 Вы были назначены администратором бота!\n\n"
            "Используйте команду /admin для доступа к панели управления."
        )
    except ApiException as exc:
        logger.warning("Could not notify new admin %s: %s", new_admin_id, exc)
    
    # Отправляем меню выбора разрешений
    text_lines = [
        f"✅ Пользователь с ID {new_admin_id} успешно добавлен в администраторы!",
        "",
        "Теперь выберите разделы, к которым у него будет доступ:",
    ]
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            f"⚙️ Настроить разделы для админа {new_admin_id}",
            callback_data=f"admin:edit_permissions:{new_admin_id}"
        ),
        types.InlineKeyboardButton(
            "✅ Оставить все разделы (по умолчанию)",
            callback_data="admin:manage_admins"
        )
    )
    
    bot.send_message(message.chat.id, "\n".join(text_lines), reply_markup=markup)
    
    states.pop(message.from_user.id)
    
    # Обновляем сообщение в чате администратора
    message_id = user_state.payload.get("message_id")
    chat_id = user_state.payload.get("chat_id")
    if message_id and chat_id:
        admins = db.get_all_admins()
        text_lines = [
            "<b>👥 Управление администраторами</b>",
            f"Всего администраторов: {len(admins)}",
            "",
        ]
        for admin in admins:
            username = admin["username"] or "нет"
            text_lines.append(f"• ID: {admin['user_id']} (@{username})")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Добавить админа", callback_data="admin:add_admin"
            ),
            types.InlineKeyboardButton(
                "➖ Удалить админа", callback_data="admin:remove_admin"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
        except ApiException:
            pass


def process_remove_admin(message: types.Message, user_state: PendingState) -> None:
    """Обработка удаления администратора."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Удаление админа отменено.")
        return
    
    try:
        admin_id_to_remove = int(incoming_text)
    except ValueError:
        bot.reply_to(message, "Введите корректный ID пользователя (только цифры)")
        states.pop(message.from_user.id)
        return
    
    # Проверяем, что пользователь не пытается удалить сам себя
    if admin_id_to_remove == message.from_user.id:
        bot.reply_to(message, "❌ Вы не можете удалить себя из администраторов")
        states.pop(message.from_user.id)
        return
    
    # Проверяем, что это администратор
    if not db.is_admin(admin_id_to_remove):
        bot.reply_to(message, f"Пользователь с ID {admin_id_to_remove} не является администратором")
        states.pop(message.from_user.id)
        return
    
    # Удаляем админа
    if db.remove_admin(admin_id_to_remove):
        bot.reply_to(
            message,
            f"✅ Администратор с ID {admin_id_to_remove} успешно удален"
        )
        
        # Пытаемся отправить уведомление удаленному админу
        try:
            bot.send_message(
                admin_id_to_remove,
                "ℹ️ Вы были удалены из администраторов бота."
            )
        except ApiException as exc:
            logger.warning("Could not notify removed admin %s: %s", admin_id_to_remove, exc)
    else:
        bot.reply_to(message, f"❌ Не удалось удалить администратора")
    
    states.pop(message.from_user.id)
    
    # Обновляем сообщение в чате администратора
    message_id = user_state.payload.get("message_id")
    chat_id = user_state.payload.get("chat_id")
    if message_id and chat_id:
        admins = db.get_all_admins()
        text_lines = [
            "<b>👥 Управление администраторами</b>",
            f"Всего администраторов: {len(admins)}",
            "",
        ]
        for admin in admins:
            username = admin["username"] or "нет"
            text_lines.append(f"• ID: {admin['user_id']} (@{username})")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "➕ Добавить админа", callback_data="admin:add_admin"
            ),
            types.InlineKeyboardButton(
                "➖ Удалить админа", callback_data="admin:remove_admin"
            ),
            types.InlineKeyboardButton(
                "◀️ Назад", callback_data="admin:back_to_menu"
            )
        )
        
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(text_lines),
                reply_markup=markup,
            )
        except ApiException:
            pass


def process_add_balance_user_id(message: types.Message, user_state: PendingState) -> None:
    """Обработка ввода ID пользователя для начисления баланса."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Начисление баланса отменено.")
        return
    
    try:
        target_user_id = int(incoming_text)
    except ValueError:
        bot.reply_to(message, "Введите корректный ID пользователя (только цифры)")
        return
    
    # Проверяем, существует ли пользователь
    try:
        user = db.get_user(target_user_id)
    except ValueError:
        bot.reply_to(message, f"Пользователь с ID {target_user_id} не найден в базе данных")
        states.pop(message.from_user.id)
        return
    
    # Переходим к запросу суммы
    text_lines = [
        "<b>➕ Начисление баланса</b>",
        f"Пользователь: ID {target_user_id}",
        f"Текущий баланс: {user['balance']:.2f} $",
        "",
        "Введите сумму для начисления (в USD):",
        "Введите 'отмена' для отмены.",
    ]
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "❌ Отмена", callback_data="admin:balance_management"
        )
    )
    
    bot.send_message(
        message.chat.id,
        "\n".join(text_lines),
        reply_markup=markup,
    )
    
    states.set(
        message.from_user.id,
        "awaiting_add_balance_amount",
        target_user_id=target_user_id,
    )


def process_add_balance_amount(message: types.Message, user_state: PendingState) -> None:
    """Обработка ввода суммы для начисления баланса."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Начисление баланса отменено.")
        return
    
    try:
        amount = decimal_from_text(incoming_text)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        bot.reply_to(message, "Введите корректную сумму (положительное число)")
        return
    
    target_user_id = user_state.payload.get("target_user_id")
    if not target_user_id:
        bot.reply_to(message, "Ошибка: не указан ID пользователя")
        states.pop(message.from_user.id)
        return
    
    # Начисляем баланс
    db.update_user_balance(target_user_id, delta_balance=amount)
    
    # Получаем обновленный баланс
    user = db.get_user(target_user_id)
    
    bot.reply_to(
        message,
        f"✅ Баланс успешно начислен!\n\n"
        f"Пользователь: ID {target_user_id}\n"
        f"Начислено: +{amount:.2f} $\n"
        f"Новый баланс: {user['balance']:.2f} $"
    )
    
    # Пытаемся отправить уведомление пользователю
    try:
        bot.send_message(
            target_user_id,
            f"💰 Ваш баланс пополнен администратором\n\n"
            f"Начислено: +{amount:.2f} $\n"
            f"Текущий баланс: {user['balance']:.2f} $"
        )
    except ApiException as exc:
        logger.warning("Could not notify user %s about balance addition: %s", target_user_id, exc)
    
    states.pop(message.from_user.id)


def process_subtract_balance_user_id(message: types.Message, user_state: PendingState) -> None:
    """Обработка ввода ID пользователя для списания баланса."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Списание баланса отменено.")
        return
    
    try:
        target_user_id = int(incoming_text)
    except ValueError:
        bot.reply_to(message, "Введите корректный ID пользователя (только цифры)")
        return
    
    # Проверяем, существует ли пользователь
    try:
        user = db.get_user(target_user_id)
    except ValueError:
        bot.reply_to(message, f"Пользователь с ID {target_user_id} не найден в базе данных")
        states.pop(message.from_user.id)
        return
    
    # Переходим к запросу суммы
    text_lines = [
        "<b>➖ Списание баланса</b>",
        f"Пользователь: ID {target_user_id}",
        f"Текущий баланс: {user['balance']:.2f} $",
        "",
        "Введите сумму для списания (в USD):",
        "Введите 'отмена' для отмены.",
    ]
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "❌ Отмена", callback_data="admin:balance_management"
        )
    )
    
    bot.send_message(
        message.chat.id,
        "\n".join(text_lines),
        reply_markup=markup,
    )
    
    states.set(
        message.from_user.id,
        "awaiting_subtract_balance_amount",
        target_user_id=target_user_id,
    )


def process_subtract_balance_amount(message: types.Message, user_state: PendingState) -> None:
    """Обработка ввода суммы для списания баланса."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Списание баланса отменено.")
        return
    
    try:
        amount = decimal_from_text(incoming_text)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        bot.reply_to(message, "Введите корректную сумму (положительное число)")
        return
    
    target_user_id = user_state.payload.get("target_user_id")
    if not target_user_id:
        bot.reply_to(message, "Ошибка: не указан ID пользователя")
        states.pop(message.from_user.id)
        return
    
    # Получаем текущий баланс
    user = db.get_user(target_user_id)
    current_balance = Decimal(str(user['balance']))
    
    # Проверяем, достаточно ли средств
    if current_balance < amount:
        bot.reply_to(
            message,
            f"⚠️ Недостаточно средств для списания!\n\n"
            f"Текущий баланс: {current_balance:.2f} $\n"
            f"Запрошено к списанию: {amount:.2f} $\n"
            f"Не хватает: {(amount - current_balance):.2f} $"
        )
        states.pop(message.from_user.id)
        return
    
    # Списываем баланс
    db.update_user_balance(target_user_id, delta_balance=-amount)
    
    # Получаем обновленный баланс
    user = db.get_user(target_user_id)
    
    bot.reply_to(
        message,
        f"✅ Баланс успешно списан!\n\n"
        f"Пользователь: ID {target_user_id}\n"
        f"Списано: -{amount:.2f} $\n"
        f"Новый баланс: {user['balance']:.2f} $"
    )
    
    # Пытаемся отправить уведомление пользователю
    try:
        bot.send_message(
            target_user_id,
            f"💳 С вашего баланса списаны средства администратором\n\n"
            f"Списано: -{amount:.2f} $\n"
            f"Текущий баланс: {user['balance']:.2f} $"
        )
    except ApiException as exc:
        logger.warning("Could not notify user %s about balance subtraction: %s", target_user_id, exc)
    
    states.pop(message.from_user.id)


def process_block_user(message: types.Message, user_state: PendingState) -> None:
    """Обработка блокировки пользователя."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Блокировка отменена.")
        return
    
    try:
        target_user_id = int(incoming_text)
    except ValueError:
        bot.reply_to(message, "Введите корректный ID пользователя (только цифры)")
        return
    
    # Проверяем, существует ли пользователь
    try:
        user = db.get_user(target_user_id)
    except ValueError:
        bot.reply_to(message, f"Пользователь с ID {target_user_id} не найден в базе данных")
        states.pop(message.from_user.id)
        return
    
    # Блокируем пользователя
    db.block_user(target_user_id)
    
    username = (user["username"] if "username" in user.keys() else None) or (user["first_name"] if "first_name" in user.keys() else "Пользователь")
    
    bot.reply_to(
        message,
        f"✅ Пользователь заблокирован!\n\n"
        f"ID: {target_user_id}\n"
        f"Имя: {username}\n\n"
        f"Пользователь больше не сможет использовать бота."
    )
    
    states.pop(message.from_user.id)


def process_unblock_user(message: types.Message, user_state: PendingState) -> None:
    """Обработка разблокировки пользователя."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Разблокировка отменена.")
        return
    
    try:
        target_user_id = int(incoming_text)
    except ValueError:
        bot.reply_to(message, "Введите корректный ID пользователя (только цифры)")
        return
    
    # Проверяем, существует ли пользователь
    try:
        user = db.get_user(target_user_id)
    except ValueError:
        bot.reply_to(message, f"Пользователь с ID {target_user_id} не найден в базе данных")
        states.pop(message.from_user.id)
        return
    
    # Разблокируем пользователя
    db.unblock_user(target_user_id)
    
    username = (user["username"] if "username" in user.keys() else None) or (user["first_name"] if "first_name" in user.keys() else "Пользователь")
    
    bot.reply_to(
        message,
        f"✅ Пользователь разблокирован!\n\n"
        f"ID: {target_user_id}\n"
        f"Имя: {username}\n\n"
        f"Пользователь снова может использовать бота."
    )
    
    # Пытаемся отправить уведомление пользователю
    try:
        bot.send_message(
            target_user_id,
            f"✅ Ваш доступ к боту восстановлен!\n\n"
            f"Теперь вы снова можете использовать все функции бота."
        )
    except ApiException as exc:
        logger.warning("Could not notify user %s about unblocking: %s", target_user_id, exc)
    
    states.pop(message.from_user.id)


def process_broadcast(message: types.Message, user_state: PendingState) -> None:
    """Process broadcast message and send to all users."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    # Get all user IDs
    user_ids = db.get_all_user_ids()
    
    # Send confirmation
    bot.reply_to(
        message,
        f"📢 Начинаю рассылку сообщения для {len(user_ids)} пользователей...\n\n"
        "Это может занять некоторое время."
    )
    
    # Track stats
    success_count = 0
    failed_count = 0
    
    # Send message to all users
    for user_id in user_ids:
        try:
            # Forward the message to each user
            if message.text:
                bot.send_message(user_id, message.text, parse_mode="HTML")
            elif message.photo:
                bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption, parse_mode="HTML")
            elif message.video:
                bot.send_video(user_id, message.video.file_id, caption=message.caption, parse_mode="HTML")
            elif message.document:
                bot.send_document(user_id, message.document.file_id, caption=message.caption, parse_mode="HTML")
            else:
                bot.copy_message(user_id, message.chat.id, message.message_id)
            
            success_count += 1
            time.sleep(0.05)  # Small delay to avoid rate limits
        except ApiException as e:
            failed_count += 1
            logger.warning(f"Failed to send broadcast to user {user_id}: {e}")
    
    # Send completion message
    bot.send_message(
        message.chat.id,
        f"✅ Рассылка завершена!\n\n"
        f"Успешно отправлено: {success_count}\n"
        f"Ошибок: {failed_count}"
    )
    
    states.pop(message.from_user.id)


def process_promo_code(message: types.Message, user_state: PendingState) -> None:
    """Process promo code input."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip().upper()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Создание промокода отменено.")
        return
    
    # Validate promo code format (only letters and numbers)
    if not incoming_text.replace("_", "").replace("-", "").isalnum():
        bot.reply_to(message, "Промокод должен содержать только латинские буквы, цифры, дефисы и подчёркивания.")
        return
    
    # Check if promo code already exists
    existing = db.get_promo_code(incoming_text)
    if existing:
        bot.reply_to(message, f"Промокод <code>{incoming_text}</code> уже существует. Введите другой код.")
        return
    
    # Ask for amount
    bot.reply_to(
        message,
        f"Промокод: <code>{incoming_text}</code>\n\n"
        "Теперь введите сумму бонуса в долларах:\n\n"
        "Пример: <code>10</code> или <code>5.50</code>"
    )
    
    states.set(
        message.from_user.id,
        "awaiting_promo_amount",
        promo_code=incoming_text
    )


def process_promo_amount(message: types.Message, user_state: PendingState) -> None:
    """Process promo code amount input."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Создание промокода отменено.")
        return
    
    try:
        amount = decimal_from_text(incoming_text)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        bot.reply_to(message, "Введите корректную сумму (положительное число)")
        return
    
    promo_code = user_state.payload.get("promo_code")
    
    # Ask for max uses
    bot.reply_to(
        message,
        f"Промокод: <code>{promo_code}</code>\n"
        f"Сумма: <b>{amount} $</b>\n\n"
        "Теперь введите максимальное количество использований:\n\n"
        "Пример: <code>1</code> (одноразовый) или <code>100</code> (многоразовый)"
    )
    
    states.set(
        message.from_user.id,
        "awaiting_promo_max_uses",
        promo_code=promo_code,
        promo_amount=amount
    )


def process_promo_max_uses(message: types.Message, user_state: PendingState) -> None:
    """Process promo code max uses input and create promo code."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Создание промокода отменено.")
        return
    
    try:
        max_uses = int(incoming_text)
        if max_uses <= 0:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "Введите корректное количество использований (положительное целое число)")
        return
    
    promo_code = user_state.payload.get("promo_code")
    promo_amount = user_state.payload.get("promo_amount")
    
    # Create promo code
    try:
        db.create_promo_code(
            code=promo_code,
            amount=promo_amount,
            max_uses=max_uses,
            created_by=message.from_user.id
        )
        
        bot.reply_to(
            message,
            f"✅ Промокод создан!\n\n"
            f"Код: <code>{promo_code}</code>\n"
            f"Сумма: <b>{promo_amount} $</b>\n"
            f"Макс. использований: <b>{max_uses}</b>\n\n"
            f"Для активации пользователь должен использовать:\n"
            f"<code>/promo {promo_code}</code>"
        )
    except Exception as e:
        logger.error(f"Error creating promo code: {e}")
        bot.reply_to(message, f"Ошибка при создании промокода: {e}")
    
    states.pop(message.from_user.id)


def process_required_channel_info(message: types.Message, user_state: PendingState) -> None:
    """Process required channel information input and add channel."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Добавление канала отменено.")
        return
    
    # Parse input: channel_id | channel_name | channel_link
    parts = [p.strip() for p in incoming_text.split("|")]
    
    if len(parts) != 3:
        bot.reply_to(
            message,
            "❌ Неверный формат. Используйте:\n\n"
            "<code>ID канала | Название | Ссылка</code>\n\n"
            "Пример:\n"
            "<code>@my_channel | Мой канал | https://t.me/my_channel</code>"
        )
        return
    
    channel_id, channel_name, channel_link = parts
    
    if not channel_id or not channel_name or not channel_link:
        bot.reply_to(
            message,
            "❌ Все поля обязательны для заполнения."
        )
        return
    
    # Validate channel link
    if not channel_link.startswith(("https://t.me/", "http://t.me/", "t.me/")):
        bot.reply_to(
            message,
            "❌ Ссылка на канал должна начинаться с https://t.me/ или t.me/"
        )
        return
    
    # Try to check if bot is admin in the channel
    try:
        bot_member = bot.get_chat_member(channel_id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator']:
            bot.reply_to(
                message,
                f"⚠️ Бот не является администратором канала {channel_name}!\n\n"
                f"Пожалуйста, добавьте бота администратором канала и повторите попытку."
            )
            return
    except ApiException as e:
        logger.warning(f"Cannot check bot admin status for channel {channel_id}: {e}")
        bot.reply_to(
            message,
            f"⚠️ Не удалось проверить права бота в канале.\n\n"
            f"Убедитесь, что:\n"
            f"1. ID канала указан правильно\n"
            f"2. Бот добавлен в канал как администратор\n\n"
            f"Ошибка: {e}"
        )
        return
    
    # Add channel to database
    success, msg = db.add_required_channel(
        channel_id=channel_id,
        channel_name=channel_name,
        channel_link=channel_link,
        added_by=message.from_user.id
    )
    
    if success:
        bot.reply_to(
            message,
            f"{msg}\n\n"
            f"ID: <code>{channel_id}</code>\n"
            f"Название: <b>{channel_name}</b>\n"
            f"Ссылка: {channel_link}"
        )
    else:
        bot.reply_to(message, msg)
    
    states.pop(message.from_user.id)


def process_delete_check(message: types.Message, user_state: PendingState) -> None:
    """Process check deletion by ID."""
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "Доступ запрещён")
        states.pop(message.from_user.id)
        return
    
    incoming_text = (message.text or "").strip()
    
    if incoming_text.lower() in CANCEL_KEYWORDS:
        states.pop(message.from_user.id)
        bot.reply_to(message, "Удаление чека отменено.")
        return
    
    # Validate check ID
    try:
        check_id = int(incoming_text)
    except ValueError:
        bot.reply_to(
            message,
            "❌ Неверный формат ID чека. Введите числовой ID.\n\n"
            "Пример: <code>12345</code>",
            parse_mode="HTML"
        )
        return
    
    # Try to delete the check
    try:
        success = crypto_pay_client.delete_check(check_id)
        
        if success:
            bot.reply_to(
                message,
                f"✅ Чек <code>{check_id}</code> успешно удален!",
                parse_mode="HTML"
            )
            logger.info(f"Admin {message.from_user.id} deleted check {check_id}")
        else:
            bot.reply_to(
                message,
                f"❌ Не удалось удалить чек <code>{check_id}</code>",
                parse_mode="HTML"
            )
    except CryptoPayError as e:
        logger.error(f"Error deleting check {check_id}: {e}")
        bot.reply_to(
            message,
            f"❌ Ошибка при удалении чека:\n{e}",
            parse_mode="HTML"
        )
    
    states.pop(message.from_user.id)


def main() -> None:
    logger.info("Starting Telegram bot")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
