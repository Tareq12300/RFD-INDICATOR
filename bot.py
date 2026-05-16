import os
import time
import threading
import json
import requests
import pandas as pd
from flask import Flask

# =========================================================
# Telegram Settings
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# مرن جدًا حتى يعطي تنبيهات كثيرة
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "50"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_SYMBOLS_PER_EXCHANGE = int(os.getenv("MAX_SYMBOLS_PER_EXCHANGE", "500"))
MIN_VOLUME_USD = float(os.getenv("MIN_VOLUME_USD", "500000"))

TARGET1_PERCENT = float(os.getenv("TARGET1_PERCENT", "1.5"))
TARGET2_PERCENT = float(os.getenv("TARGET2_PERCENT", "3"))
TARGET3_PERCENT = float(os.getenv("TARGET3_PERCENT", "5"))
TARGET4_PERCENT = float(os.getenv("TARGET4_PERCENT", "8"))
TARGET5_PERCENT = float(os.getenv("TARGET5_PERCENT", "12"))

STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "1.5"))

EMA_DISTANCE_LIMIT = float(os.getenv("EMA_DISTANCE_LIMIT", "0.08"))

VOLUME_15M_RATIO = float(os.getenv("VOLUME_15M_RATIO", "0.80"))
VOLUME_5M_RATIO = float(os.getenv("VOLUME_5M_RATIO", "0.80"))

STOCH_MAX = float(os.getenv("STOCH_MAX", "90"))

DUPLICATE_COOLDOWN_HOURS = int(os.getenv("DUPLICATE_COOLDOWN_HOURS", "2"))

# Learning / performance tracking
LEARNING_FILE = os.getenv("LEARNING_FILE", "signals_learning.json")
LEARNING_REPORT_EVERY = int(os.getenv("LEARNING_REPORT_EVERY", "6"))  # كل 6 جولات تقريباً
MIN_SAMPLE_FOR_ADVICE = int(os.getenv("MIN_SAMPLE_FOR_ADVICE", "10"))

ENABLED_EXCHANGES = ["GATE", "BITGET", "OKX"]

# =========================================================
# Flask Keep Alive
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Flexible 3-Timeframe Crypto Bot is Running ✅"

# =========================================================
# Exclusions
# =========================================================

EXCLUDED_BASES = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "JUP", "SUI",
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD", "USDD",
    "USD1", "PYUSD", "GUSD", "LUSD", "EUSD", "EURT", "EURC",
    "TWT", "KCS", "LEO", "OKB", "CRO", "GT", "BGB", "MX",
    "GLDX", "PAXG", "XAUT"
}

EXCLUDED_KEYWORDS = [
    "USD", "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD",
    "PAXG", "XAUT", "ETF", "XSTOCK", "STOCK", "ON"
]

LEVERAGED_SUFFIXES = (
    "2L", "2S", "3L", "3S", "4L", "4S", "5L", "5S",
    "BULL", "BEAR", "UP", "DOWN"
)

active_trades = {}
active_bases = {}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoSignalBot/1.0",
    "Accept": "application/json"
})

# =========================================================
# Helpers
# =========================================================

def safe_get(url, params=None):
    r = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def get_base_from_display(display):
    return display.replace("/USDT", "").strip().upper()

def is_excluded_base(base):
    if not base:
        return True

    b = base.upper().replace("-", "").replace("_", "")

    if b in EXCLUDED_BASES:
        return True

    if b.endswith("USD") or b.endswith("USDT") or b.endswith("USDC"):
        return True

    if b.endswith(LEVERAGED_SUFFIXES):
        return True

    for word in EXCLUDED_KEYWORDS:
        if word in b:
            return True

    return False

def normalize_ohlcv(rows):
    if not rows or len(rows) < 50:
        return None

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"])
    df = df.sort_values("time").reset_index(drop=True)

    if len(df) < 50:
        return None

    return df


# =========================================================
# Learning System
# =========================================================

scan_rounds = 0

def load_learning_data():
    try:
        if os.path.exists(LEARNING_FILE):
            with open(LEARNING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("Learning load error:", e)

    return {
        "signals": [],
        "summary": {
            "total_signals": 0,
            "target1_hits": 0,
            "target2_hits": 0,
            "target3_hits": 0,
            "target4_hits": 0,
            "target5_hits": 0,
            "stop_hits": 0
        }
    }

def save_learning_data(data):
    try:
        with open(LEARNING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Learning save error:", e)

def learning_register_signal(exchange, display, entry, confidence, volume, volume_usd=0):
    data = load_learning_data()
    data["summary"]["total_signals"] += 1

    base = get_base_from_display(display)

    data["signals"].append({
        "base": base,
        "exchange": exchange,
        "display": display,
        "entry": entry,
        "confidence": confidence,
        "volume": volume,
        "volume_usd": volume_usd,
        "created_at": int(time.time()),
        "target1": False,
        "target2": False,
        "target3": False,
        "target4": False,
        "target5": False,
        "stop_loss": False
    })

    # احتفظ بآخر 500 إشارة فقط حتى لا يكبر الملف
    data["signals"] = data["signals"][-500:]
    save_learning_data(data)

def learning_mark_target(display, target_no):
    data = load_learning_data()
    base = get_base_from_display(display)
    key = f"target{target_no}"

    data["summary"][f"target{target_no}_hits"] += 1

    for sig in reversed(data["signals"]):
        if sig.get("base") == base and not sig.get(key):
            sig[key] = True
            break

    save_learning_data(data)

def learning_mark_stop(display):
    data = load_learning_data()
    base = get_base_from_display(display)

    data["summary"]["stop_hits"] += 1

    for sig in reversed(data["signals"]):
        if sig.get("base") == base and not sig.get("stop_loss"):
            sig["stop_loss"] = True
            break

    save_learning_data(data)

def build_learning_advice():
    data = load_learning_data()
    summary = data.get("summary", {})

    total = summary.get("total_signals", 0)
    t1 = summary.get("target1_hits", 0)
    t2 = summary.get("target2_hits", 0)
    stops = summary.get("stop_hits", 0)

    if total < MIN_SAMPLE_FOR_ADVICE:
        return f"""
🧠 تقرير التعلم الذاتي

عدد الإشارات المسجلة: {total}

لا توجد بيانات كافية للحكم الآن.
نحتاج على الأقل {MIN_SAMPLE_FOR_ADVICE} إشارات حتى نعطي توصية دقيقة.
""".strip()

    t1_rate = (t1 / total) * 100 if total else 0
    t2_rate = (t2 / total) * 100 if total else 0
    stop_rate = (stops / total) * 100 if total else 0

    advice = []

    if stop_rate > 35:
        advice.append("⚠️ نسبة وقف الخسارة مرتفعة: شدد شروط الدخول.")
        advice.append("اقتراح: ارفع MIN_CONFIDENCE إلى 60 أو 70.")
        advice.append("اقتراح: اجعل Volume على 5M أعلى من المتوسط بدل 0.80.")

    if t1_rate < 35:
        advice.append("⚠️ نسبة تحقيق الهدف الأول ضعيفة.")
        advice.append("اقتراح: لا تدخل إلا إذا كان 15M أقوى.")
        advice.append("اقتراح: اجعل Stoch RSI على 15M أقل من 80 بدل 90.")

    if t1_rate >= 50 and stop_rate <= 25:
        advice.append("✅ الأداء جيد مبدئياً.")
        advice.append("اقتراح: ارفع الهدف الأول تدريجياً أو ارفع MIN_CONFIDENCE إلى 60.")

    if t2_rate < 20 and t1_rate >= 45:
        advice.append("ℹ️ الهدف الأول جيد لكن الهدف الثاني ضعيف.")
        advice.append("اقتراح: خذ جزء من الربح عند الهدف الأول ولا تنتظر الهدف الثاني دائماً.")

    if not advice:
        advice.append("الأداء متوسط. استمر بجمع البيانات قبل تعديل الشروط.")

    return f"""
🧠 *تقرير التعلم الذاتي للبوت*

📊 إجمالي الإشارات: `{total}`
🎯 تحقق الهدف 1: `{t1}` | `{t1_rate:.1f}%`
🎯 تحقق الهدف 2: `{t2}` | `{t2_rate:.1f}%`
🛑 ضرب وقف الخسارة: `{stops}` | `{stop_rate:.1f}%`

🔧 *توصيات التحسين:*
{chr(10).join(advice)}
""".strip()


# =========================================================
# Telegram
# =========================================================

def send_telegram(message, use_markdown=True):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    if use_markdown:
        payload["parse_mode"] = "Markdown"

    try:
        response = session.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            return True

        print("Telegram send failed:", response.text)

        # fallback بدون Markdown
        if use_markdown:
            payload.pop("parse_mode", None)
            response = session.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            print("Telegram fallback failed:", response.text)

        return False

    except Exception as e:
        print("Telegram error:", e)
        return False

# =========================================================
# Symbols
# =========================================================

def get_gate_symbols():
    data = safe_get("https://api.gateio.ws/api/v4/spot/currency_pairs")
    out = []

    for item in data:
        pair = item.get("id", "")
        base = item.get("base", "")
        quote = item.get("quote", "")
        trade_status = item.get("trade_status", "")

        if quote == "USDT" and trade_status == "tradable":
            if not is_excluded_base(base):
                out.append({
                    "exchange": "GATE",
                    "symbol": pair,
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out[:MAX_SYMBOLS_PER_EXCHANGE]

def get_bitget_symbols():
    data = safe_get("https://api.bitget.com/api/v2/spot/public/symbols")
    out = []

    for item in data.get("data", []):
        base = item.get("baseCoin", "")
        quote = item.get("quoteCoin", "")
        status = item.get("status", "")
        symbol = item.get("symbol", "")

        if quote == "USDT" and status == "online":
            if not is_excluded_base(base):
                out.append({
                    "exchange": "BITGET",
                    "symbol": symbol,
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out[:MAX_SYMBOLS_PER_EXCHANGE]

def get_okx_symbols():
    data = safe_get("https://www.okx.com/api/v5/public/instruments", {"instType": "SPOT"})
    out = []

    for item in data.get("data", []):
        base = item.get("baseCcy", "")
        quote = item.get("quoteCcy", "")
        state = item.get("state", "")
        inst_id = item.get("instId", "")

        if quote == "USDT" and state == "live":
            if not is_excluded_base(base):
                out.append({
                    "exchange": "OKX",
                    "symbol": inst_id,
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out[:MAX_SYMBOLS_PER_EXCHANGE]

def get_all_symbols():
    funcs = {
        "GATE": get_gate_symbols,
        "BITGET": get_bitget_symbols,
        "OKX": get_okx_symbols,
    }

    all_items = []

    for ex in ENABLED_EXCHANGES:
        try:
            items = funcs[ex]()
            print(f"{ex} symbols loaded: {len(items)}")
            all_items.extend(items)
        except Exception as e:
            print(f"{ex} symbols load failed: {e}")

    return all_items

# =========================================================
# Candles
# =========================================================

def get_gate_klines(symbol, interval, limit=220):
    data = safe_get("https://api.gateio.ws/api/v4/spot/candlesticks", {
        "currency_pair": symbol,
        "interval": interval,
        "limit": limit
    })

    rows = []
    for x in data:
        # Gate: [timestamp, volume, close, high, low, open]
        rows.append([x[0], x[5], x[3], x[4], x[2], x[1]])

    return normalize_ohlcv(rows)

def get_bitget_klines(symbol, interval, limit=220):
    granularity_map = {
        "5m": "5min",
        "15m": "15min",
        "1h": "1h"
    }

    data = safe_get("https://api.bitget.com/api/v2/spot/market/candles", {
        "symbol": symbol,
        "granularity": granularity_map[interval],
        "limit": str(limit)
    })

    rows = []
    for x in data.get("data", []):
        rows.append([x[0], x[1], x[2], x[3], x[4], x[5]])

    return normalize_ohlcv(rows)

def get_okx_klines(symbol, interval, limit=220):
    bar_map = {
        "5m": "5m",
        "15m": "15m",
        "1h": "1H"
    }

    data = safe_get("https://www.okx.com/api/v5/market/candles", {
        "instId": symbol,
        "bar": bar_map[interval],
        "limit": str(limit)
    })

    rows = []
    for x in data.get("data", []):
        rows.append([x[0], x[1], x[2], x[3], x[4], x[5]])

    return normalize_ohlcv(rows)

def get_klines(exchange, symbol, interval, limit=220):
    if exchange == "GATE":
        return get_gate_klines(symbol, interval, limit)

    if exchange == "BITGET":
        return get_bitget_klines(symbol, interval, limit)

    if exchange == "OKX":
        return get_okx_klines(symbol, interval, limit)

    return None

# =========================================================
# Indicators
# =========================================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))

def macd(df):
    ema12 = ema(df["close"], 12)
    ema26 = ema(df["close"], 26)
    macd_line = ema12 - ema26
    signal_line = ema(macd_line, 9)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def stoch_rsi(df, period=14):
    r = rsi(df["close"], period)
    min_r = r.rolling(period).min()
    max_r = r.rolling(period).max()
    stoch = (r - min_r) / (max_r - min_r).replace(0, pd.NA) * 100
    k = stoch.rolling(3).mean()
    d = k.rolling(3).mean()
    return k, d

# =========================================================
# Analysis - مرن جدًا للتنبيهات الكثيرة
# =========================================================

def analyze_1h(df):
    """
    الاتجاه العام مرن:
    يكفي السعر فوق EMA200 أو EMA20 فوق EMA200.
    """
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema200"] = ema(df["close"], 200)

    last = df.iloc[-1]

    return bool(
        last["close"] > last["ema200"]
        or last["ema20"] > last["ema200"]
    )

def analyze_15m(df):
    """
    مرن جدًا:
    - Stoch RSI صاعد وليس فوق 90
    - MACD يتحسن
    - Volume يسمح حتى لو أقل من المتوسط
    """
    _, _, hist = macd(df)
    k, d = stoch_rsi(df)

    if pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]) or pd.isna(k.iloc[-2]) or pd.isna(d.iloc[-2]):
        return False

    volume_now = df["volume"].iloc[-1]
    volume_avg = df["volume"].rolling(20).mean().iloc[-1]

    condition_stoch = k.iloc[-1] > d.iloc[-1] and k.iloc[-1] < STOCH_MAX
    condition_macd = hist.iloc[-1] > hist.iloc[-2]
    condition_volume = volume_now > volume_avg * VOLUME_15M_RATIO

    return bool(condition_stoch and condition_macd and condition_volume)

def analyze_5m(df):
    """
    دخول سريع مرن جدًا:
    - EMA9 فوق EMA21
    - السعر أعلى من إغلاق الشمعة السابقة
    - MACD يتحسن
    - Volume يسمح حتى لو أقل من المتوسط
    - السعر لا يكون بعيد جدًا عن EMA9
    """
    df = df.copy()
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    _, _, hist = macd(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    volume_now = df["volume"].iloc[-1]
    volume_avg = df["volume"].rolling(20).mean().iloc[-1]

    distance_from_ema9 = (last["close"] - last["ema9"]) / last["ema9"]

    condition_ema = last["ema9"] > last["ema21"]
    condition_breakout = last["close"] > prev["close"]
    condition_macd = hist.iloc[-1] > hist.iloc[-2]
    condition_volume = volume_now > volume_avg * VOLUME_5M_RATIO
    condition_not_late = distance_from_ema9 <= EMA_DISTANCE_LIMIT

    return bool(
        condition_ema
        and condition_breakout
        and condition_macd
        and condition_volume
        and condition_not_late
    )

def calculate_confidence(df_1h, df_15m, df_5m):
    score = 0

    if analyze_1h(df_1h):
        score += 30

    if analyze_15m(df_15m):
        score += 30

    if analyze_5m(df_5m):
        score += 30

    vol_now = df_5m["volume"].iloc[-1]
    vol_avg = df_5m["volume"].rolling(20).mean().iloc[-1]

    if vol_now > vol_avg:
        score += 10

    return min(score, 100)

# =========================================================
# Targets
# =========================================================

def build_targets(price):
    return [
        price * (1 + TARGET1_PERCENT / 100),
        price * (1 + TARGET2_PERCENT / 100),
        price * (1 + TARGET3_PERCENT / 100),
        price * (1 + TARGET4_PERCENT / 100),
        price * (1 + TARGET5_PERCENT / 100),
    ]

def build_stop_loss(price):
    return price * (1 - STOP_LOSS_PERCENT / 100)

# =========================================================
# Messages
# =========================================================

def format_message(exchange, display, price, confidence, volume, volume_usd, targets, stop_loss):
    return f"""
🚀 *إشارة دخول - Long*

المنصة: `{exchange}`
العملة: `{display}`

💰 السعر الحالي:
`{price:.8f}`

📊 Volume الحالي بالدولار:
`${volume_usd:,.0f}`

📦 Volume العملة:
`{volume:,.2f}`

🔥 الثقة:
`{confidence}%`

✅ *شروط الإشارة المرنة:*
1H الاتجاه العام: إيجابي ✅
15M الزخم يتحسن ✅
5M دخول سريع ✅
Volume مناسب ✅

🎯 *الأهداف:*
1) `{targets[0]:.8f}`
2) `{targets[1]:.8f}`
3) `{targets[2]:.8f}`
4) `{targets[3]:.8f}`
5) `{targets[4]:.8f}`

🛑 *وقف الخسارة المقترح:*
`{stop_loss:.8f}`

⚠️ ليست توصية مالية. استخدم إدارة مخاطر صارمة.
""".strip()

def can_send(exchange, symbol, display):
    base = get_base_from_display(display)
    now = time.time()
    cooldown = 60 * 60 * DUPLICATE_COOLDOWN_HOURS  # منع التكرار ساعتين فقط

    if base not in active_bases:
        active_bases[base] = now
        return True

    if now - active_bases[base] > cooldown:
        active_bases[base] = now
        return True

    return False

def register_active_trade(exchange, symbol, display, entry_price, targets, stop_loss):
    base = get_base_from_display(display)

    active_trades[base] = {
        "exchange": exchange,
        "symbol": symbol,
        "display": display,
        "entry": entry_price,
        "targets": targets,
        "stop_loss": stop_loss,
        "next_target_index": 0,
        "stopped": False,
        "created_at": time.time()
    }

def send_target_alert(trade, target_index, current_price):
    target_price = trade["targets"][target_index]
    target_no = target_index + 1
    profit_pct = ((target_price - trade["entry"]) / trade["entry"]) * 100

    msg = f"""
🎯 *تم تحقيق الهدف {target_no}*

المنصة الأصلية: `{trade['exchange']}`
العملة: `{trade['display']}`

سعر الدخول: `{trade['entry']:.8f}`
سعر الهدف: `{target_price:.8f}`
السعر الحالي: `{current_price:.8f}`

📈 الربح التقريبي من الدخول:
`+{profit_pct:.2f}%`

✅ الهدف {target_no} تحقق بنجاح.
""".strip()

    sent_ok = send_telegram(msg)
    if sent_ok:
        learning_mark_target(trade['display'], target_no)
    return sent_ok

def send_stop_loss_alert(trade, current_price):
    loss_pct = ((current_price - trade["entry"]) / trade["entry"]) * 100

    msg = f"""
🛑 *تم كسر وقف الخسارة*

المنصة الأصلية: `{trade['exchange']}`
العملة: `{trade['display']}`

سعر الدخول: `{trade['entry']:.8f}`
وقف الخسارة: `{trade['stop_loss']:.8f}`
السعر الحالي: `{current_price:.8f}`

📉 النتيجة التقريبية:
`{loss_pct:.2f}%`

⚠️ يفضل الالتزام بخطة إدارة المخاطر.
""".strip()

    sent_ok = send_telegram(msg)
    if sent_ok:
        learning_mark_stop(trade['display'])
    return sent_ok

# =========================================================
# Target Monitoring
# =========================================================

def monitor_active_targets():
    if not active_trades:
        return

    expired_keys = []
    max_trade_age = 60 * 60 * 24

    for base, trade in list(active_trades.items()):
        try:
            if time.time() - trade["created_at"] > max_trade_age:
                expired_keys.append(base)
                continue

            if trade.get("stopped"):
                expired_keys.append(base)
                continue

            df = get_klines(trade["exchange"], trade["symbol"], "5m", 50)

            if df is None or len(df) < 2:
                continue

            current_price = float(df["close"].iloc[-1])

            if current_price <= trade["stop_loss"]:
                sent_ok = send_stop_loss_alert(trade, current_price)
                if sent_ok:
                    trade["stopped"] = True
                    expired_keys.append(base)
                continue

            # إرسال الأهداف بالترتيب
            while trade["next_target_index"] < len(trade["targets"]):
                idx = trade["next_target_index"]
                target_price = trade["targets"][idx]

                if current_price >= target_price:
                    sent_ok = send_target_alert(trade, idx, current_price)

                    if sent_ok:
                        trade["next_target_index"] += 1
                        time.sleep(0.5)
                        continue

                    print(f"Target {idx + 1} send failed for {base}. Will retry later.")
                    break

                break

            if trade["next_target_index"] >= len(trade["targets"]):
                expired_keys.append(base)

        except Exception as e:
            print(f"Target monitor error for {base}: {e}")
            continue

    for base in expired_keys:
        active_bases.pop(base, None)
        active_trades.pop(base, None)

# =========================================================
# Scanner
# =========================================================

def scan_market():
    symbols = get_all_symbols()
    print(f"Total symbols: {len(symbols)}")

    for i, item in enumerate(symbols, start=1):
        exchange = item["exchange"]
        symbol = item["symbol"]
        display = item["display"]

        try:
            print(f"[{i}/{len(symbols)}] Analyze {exchange} {display}")

            df_1h = get_klines(exchange, symbol, "1h", 220)
            df_15m = get_klines(exchange, symbol, "15m", 220)
            df_5m = get_klines(exchange, symbol, "5m", 220)

            if df_1h is None or df_15m is None or df_5m is None:
                continue

            if not analyze_1h(df_1h):
                continue

            if not analyze_15m(df_15m):
                continue

            if not analyze_5m(df_5m):
                continue

            confidence = calculate_confidence(df_1h, df_15m, df_5m)

            if confidence < MIN_CONFIDENCE:
                continue

            if not can_send(exchange, symbol, display):
                print(f"Skip duplicate coin across exchanges: {display}")
                continue

            price = float(df_5m["close"].iloc[-1])
            volume = float(df_5m["volume"].iloc[-1])
            volume_usd = volume * price

            # فلتر أقل Volume بالدولار
            if volume_usd < MIN_VOLUME_USD:
                continue

            targets = build_targets(price)
            stop_loss = build_stop_loss(price)

            msg = format_message(exchange, display, price, confidence, volume, volume_usd, targets, stop_loss)
            sent_ok = send_telegram(msg)

            if sent_ok:
                register_active_trade(exchange, symbol, display, price, targets, stop_loss)
                learning_register_signal(exchange, display, price, confidence, volume, volume_usd)
                print(f"Signal sent: {exchange} {display} | Confidence: {confidence}%")
            else:
                base = get_base_from_display(display)
                active_bases.pop(base, None)
                print(f"Signal failed and was not registered: {exchange} {display}")

            time.sleep(0.5)

        except Exception as e:
            print(f"Error analyzing {exchange} {display}: {e}")
            continue

# =========================================================
# Run
# =========================================================

def run_bot():
    welcome_message = """
🚀 بوت أبو علاوي المرن بدأ العمل

✅ هذا الإصدار مخصص لإرسال تنبيهات كثيرة:
• MIN_CONFIDENCE قابل للتعديل
• شروط Volume مرنة
• شروط 15M مرنة
• شروط 5M مرنة
• الأهداف بالترتيب
• منع تكرار نفس العملة لمدة ساعتين
• تعلم ذاتي وتوصيات تحسين
• أقل Volume بالدولار قابل للتعديل من MIN_VOLUME_USD

📊 المنصات:
Gate • Bitget • OKX

⚡ البوت يعمل الآن ويبحث عن الفرص...
"""

    sent_ok = send_telegram(welcome_message, use_markdown=False)

    if sent_ok:
        print("Welcome message sent successfully")
    else:
        print("Welcome message failed")

    time.sleep(3)

    while True:
        try:
            monitor_active_targets()
            scan_market()
            monitor_active_targets()

            global scan_rounds
            scan_rounds += 1
            if scan_rounds % LEARNING_REPORT_EVERY == 0:
                send_telegram(build_learning_advice())

        except Exception as e:
            print("Scanner error:", e)

        print(f"Sleeping {SCAN_INTERVAL} seconds...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
