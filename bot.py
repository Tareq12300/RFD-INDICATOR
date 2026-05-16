import os
import time
import threading
import requests
import pandas as pd
from flask import Flask

# =========================================================
# Telegram Settings
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "90"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))  # 5 minutes
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Exchanges only: MEXC, Gate, Bitget, OKX, Bybit
ENABLED_EXCHANGES = ["MEXC", "GATE", "BITGET", "OKX", "BYBIT"]

# =========================================================
# Flask Keep Alive
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "3-Timeframe Multi-Exchange Crypto Signal Bot is Running ✅"

# =========================================================
# Exclusions
# =========================================================

EXCLUDED_BASES = {
    # Major / user excluded
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "JUP", "SUI",

    # Stablecoins
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD", "USDD",
    "USD1", "PYUSD", "GUSD", "LUSD", "EUSD", "EURT", "EURC",

    # Exchange / wallet tokens
    "TWT", "KCS", "LEO", "OKB", "CRO", "GT", "BGB", "MX",

    # Tokenized gold / ETFs / xStock-like
    "GLDX", "PAXG", "XAUT"
}

EXCLUDED_KEYWORDS = [
    "USD", "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD",
    "PAXG", "XAUT", "ETF", "XSTOCK", "STOCK"
]

sent_signals = {}
active_trades = {}

# =========================================================
# Helpers
# =========================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoSignalBot/1.0"
})

def safe_get(url, params=None):
    r = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def is_excluded_base(base):
    if not base:
        return True

    b = base.upper().replace("-", "").replace("_", "")

    if b in EXCLUDED_BASES:
        return True

    # Avoid synthetic / stable-like / wrapped fiat tokens
    if b.endswith("USD") or b.endswith("USDT") or b.endswith("USDC"):
        return True

    for word in EXCLUDED_KEYWORDS:
        if word in b and b not in {"MUSDT"}:
            return True

    return False

def to_float(x):
    try:
        return float(x)
    except Exception:
        return None

def normalize_ohlcv(rows):
    """
    rows format expected:
    [timestamp, open, high, low, close, volume]
    """
    if not rows or len(rows) < 50:
        return None

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df = df.sort_values("time").reset_index(drop=True)

    if len(df) < 50:
        return None

    return df

# =========================================================
# Telegram
# =========================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables missing")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:
        session.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

# =========================================================
# Exchange Symbols
# =========================================================

def get_mexc_symbols():
    data = safe_get("https://api.mexc.com/api/v3/exchangeInfo")
    out = []

    for item in data.get("symbols", []):
        base = item.get("baseAsset", "")
        quote = item.get("quoteAsset", "")
        status = item.get("status", "")

        if quote == "USDT" and status in ("ENABLED", "1", "TRADING"):
            if not is_excluded_base(base):
                out.append({
                    "exchange": "MEXC",
                    "symbol": item.get("symbol"),
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out

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
                    "symbol": pair,          # e.g. BTC_USDT
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out

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
                    "symbol": symbol,        # e.g. BTCUSDT
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out

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
                    "symbol": inst_id,       # e.g. BTC-USDT
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out

def get_bybit_symbols():
    data = safe_get("https://api.bybit.com/v5/market/instruments-info", {"category": "spot"})
    out = []

    for item in data.get("result", {}).get("list", []):
        base = item.get("baseCoin", "")
        quote = item.get("quoteCoin", "")
        status = item.get("status", "")
        symbol = item.get("symbol", "")

        if quote == "USDT" and status == "Trading":
            if not is_excluded_base(base):
                out.append({
                    "exchange": "BYBIT",
                    "symbol": symbol,        # e.g. BTCUSDT
                    "base": base,
                    "display": f"{base}/USDT"
                })

    return out

def get_all_symbols():
    funcs = {
        "MEXC": get_mexc_symbols,
        "GATE": get_gate_symbols,
        "BITGET": get_bitget_symbols,
        "OKX": get_okx_symbols,
        "BYBIT": get_bybit_symbols,
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
# Exchange Candles
# =========================================================

def get_mexc_klines(symbol, interval, limit=220):
    data = safe_get("https://api.mexc.com/api/v3/klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })

    rows = []
    for x in data:
        rows.append([x[0], x[1], x[2], x[3], x[4], x[5]])

    return normalize_ohlcv(rows)

def get_gate_klines(symbol, interval, limit=220):
    # Gate intervals: 5m, 15m, 1h
    data = safe_get("https://api.gateio.ws/api/v4/spot/candlesticks", {
        "currency_pair": symbol,
        "interval": interval,
        "limit": limit
    })

    rows = []
    # Gate returns: [timestamp, volume, close, high, low, open]
    for x in data:
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
        # [timestamp, open, high, low, close, baseVol, quoteVol, usdtVol]
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
    # OKX returns newest first:
    # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    for x in data.get("data", []):
        rows.append([x[0], x[1], x[2], x[3], x[4], x[5]])

    return normalize_ohlcv(rows)

def get_bybit_klines(symbol, interval, limit=220):
    interval_map = {
        "5m": "5",
        "15m": "15",
        "1h": "60"
    }

    data = safe_get("https://api.bybit.com/v5/market/kline", {
        "category": "spot",
        "symbol": symbol,
        "interval": interval_map[interval],
        "limit": str(limit)
    })

    rows = []
    # Bybit returns newest first:
    # [startTime, open, high, low, close, volume, turnover]
    for x in data.get("result", {}).get("list", []):
        rows.append([x[0], x[1], x[2], x[3], x[4], x[5]])

    return normalize_ohlcv(rows)

def get_klines(exchange, symbol, interval, limit=220):
    if exchange == "MEXC":
        return get_mexc_klines(symbol, interval, limit)
    if exchange == "GATE":
        return get_gate_klines(symbol, interval, limit)
    if exchange == "BITGET":
        return get_bitget_klines(symbol, interval, limit)
    if exchange == "OKX":
        return get_okx_klines(symbol, interval, limit)
    if exchange == "BYBIT":
        return get_bybit_klines(symbol, interval, limit)

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
# Analysis
# =========================================================

def analyze_1h(df):
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema200"] = ema(df["close"], 200)

    last = df.iloc[-1]
    return bool(last["close"] > last["ema200"] and last["ema20"] > last["ema200"])

def analyze_15m(df):
    _, _, hist = macd(df)
    k, d = stoch_rsi(df)

    if pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]):
        return False

    volume_now = df["volume"].iloc[-1]
    volume_avg = df["volume"].rolling(20).mean().iloc[-1]

    condition_stoch = k.iloc[-2] < 30 and k.iloc[-1] > d.iloc[-1]
    condition_macd = hist.iloc[-1] > hist.iloc[-2]
    condition_volume = volume_now > volume_avg

    return bool(condition_stoch and condition_macd and condition_volume)

def analyze_5m(df):
    df = df.copy()
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    _, _, hist = macd(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    volume_now = df["volume"].iloc[-1]
    volume_avg = df["volume"].rolling(20).mean().iloc[-1]

    condition_ema = last["ema9"] > last["ema21"]
    condition_breakout = last["close"] > prev["high"]
    condition_macd = hist.iloc[-1] > 0
    condition_volume = volume_now > volume_avg

    return bool(condition_ema and condition_breakout and condition_macd and condition_volume)

def calculate_confidence(df_1h, df_15m, df_5m):
    score = 0

    if analyze_1h(df_1h):
        score += 35

    if analyze_15m(df_15m):
        score += 30

    if analyze_5m(df_5m):
        score += 25

    vol_now = df_5m["volume"].iloc[-1]
    vol_avg = df_5m["volume"].rolling(20).mean().iloc[-1]

    if vol_now > vol_avg * 1.5:
        score += 10

    return min(score, 100)

# =========================================================
# Targets
# =========================================================

def build_targets(price):
    return [
        price * 1.015,
        price * 1.03,
        price * 1.05,
        price * 1.08,
        price * 1.12
    ]

def build_stop_loss(price):
    return price * 0.985

# =========================================================
# Alert
# =========================================================

def format_message(exchange, display, price, confidence, targets, stop_loss):
    return f"""
🚀 *إشارة دخول قوية - Long*

المنصة: `{exchange}`
العملة: `{display}`
السعر الحالي: `{price:.8f}`
الثقة: `{confidence}%`

✅ *توافق 3 فريمات:*
1H الاتجاه العام: صاعد ✅
15M التأكيد: إيجابي ✅
5M الدخول: Long ✅

🎯 *الأهداف:*
1) `{targets[0]:.8f}`
2) `{targets[1]:.8f}`
3) `{targets[2]:.8f}`
4) `{targets[3]:.8f}`
5) `{targets[4]:.8f}`

🛑 وقف الخسارة المقترح:
`{stop_loss:.8f}`

⚠️ ليست توصية مالية. استخدم إدارة مخاطر صارمة.
""".strip()

def can_send(exchange, symbol):
    key = f"{exchange}:{symbol}"
    now = time.time()
    cooldown = 60 * 60 * 4

    if key not in sent_signals:
        sent_signals[key] = now
        return True

    if now - sent_signals[key] > cooldown:
        sent_signals[key] = now
        return True

    return False

def register_active_trade(exchange, symbol, display, entry_price, targets, stop_loss):
    """
    يحفظ الصفقة بعد إرسال إشارة الدخول حتى يراقب الأهداف.
    """
    key = f"{exchange}:{symbol}"

    active_trades[key] = {
        "exchange": exchange,
        "symbol": symbol,
        "display": display,
        "entry": entry_price,
        "targets": targets,
        "stop_loss": stop_loss,
        "hit_targets": [False, False, False, False, False],
        "stopped": False,
        "created_at": time.time()
    }

def send_target_alert(trade, target_index, current_price):
    target_price = trade["targets"][target_index]
    target_no = target_index + 1
    profit_pct = ((target_price - trade["entry"]) / trade["entry"]) * 100

    msg = f"""
🎯 *تم تحقيق الهدف {target_no}*

المنصة: `{trade['exchange']}`
العملة: `{trade['display']}`

سعر الدخول: `{trade['entry']:.8f}`
سعر الهدف: `{target_price:.8f}`
السعر الحالي: `{current_price:.8f}`

📈 الربح التقريبي من الدخول:
`+{profit_pct:.2f}%`

✅ الهدف {target_no} تحقق بنجاح.
""".strip()

    send_telegram(msg)

def send_stop_loss_alert(trade, current_price):
    loss_pct = ((current_price - trade["entry"]) / trade["entry"]) * 100

    msg = f"""
🛑 *تم كسر وقف الخسارة*

المنصة: `{trade['exchange']}`
العملة: `{trade['display']}`

سعر الدخول: `{trade['entry']:.8f}`
وقف الخسارة: `{trade['stop_loss']:.8f}`
السعر الحالي: `{current_price:.8f}`

📉 النتيجة التقريبية:
`{loss_pct:.2f}%`

⚠️ يفضل إغلاق الصفقة أو الالتزام بخطة إدارة المخاطر.
""".strip()

    send_telegram(msg)

def monitor_active_targets():
    """
    يراقب الصفقات التي تم إرسالها، ويرسل تنبيه عند تحقق كل هدف مرة واحدة فقط.
    """
    if not active_trades:
        return

    expired_keys = []
    max_trade_age = 60 * 60 * 24  # يحذف الصفقة بعد 24 ساعة من المراقبة

    for key, trade in list(active_trades.items()):
        try:
            if time.time() - trade["created_at"] > max_trade_age:
                expired_keys.append(key)
                continue

            if trade.get("stopped"):
                expired_keys.append(key)
                continue

            df = get_klines(trade["exchange"], trade["symbol"], "5m", 50)
            if df is None or len(df) < 2:
                continue

            current_price = float(df["close"].iloc[-1])

            # وقف الخسارة
            if current_price <= trade["stop_loss"]:
                trade["stopped"] = True
                send_stop_loss_alert(trade, current_price)
                expired_keys.append(key)
                continue

            # الأهداف: يرسل لكل هدف مرة واحدة فقط
            for idx, target in enumerate(trade["targets"]):
                if not trade["hit_targets"][idx] and current_price >= target:
                    trade["hit_targets"][idx] = True
                    send_target_alert(trade, idx, current_price)
                    time.sleep(0.3)

            # إذا تحققت كل الأهداف، احذف الصفقة من المراقبة
            if all(trade["hit_targets"]):
                expired_keys.append(key)

        except Exception as e:
            print(f"Target monitor error for {key}: {e}")
            continue

    for key in expired_keys:
        active_trades.pop(key, None)

# =========================================================
# Scanner
# =========================================================

def scan_market():
    symbols = get_all_symbols()
    print(f"Total exchange symbols: {len(symbols)}")

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

            trend_1h = analyze_1h(df_1h)
            confirm_15m = analyze_15m(df_15m)
            entry_5m = analyze_5m(df_5m)

            if not trend_1h:
                continue

            if not confirm_15m:
                continue

            if not entry_5m:
                continue

            confidence = calculate_confidence(df_1h, df_15m, df_5m)

            if confidence < MIN_CONFIDENCE:
                continue

            if not can_send(exchange, symbol):
                continue

            price = df_5m["close"].iloc[-1]
            targets = build_targets(price)
            stop_loss = build_stop_loss(price)

            msg = format_message(exchange, display, price, confidence, targets, stop_loss)
            send_telegram(msg)
            register_active_trade(exchange, symbol, display, price, targets, stop_loss)

            print(f"Signal sent: {exchange} {display} | Confidence: {confidence}%")

            time.sleep(0.5)

        except Exception as e:
            print(f"Error analyzing {exchange} {display}: {e}")
            continue

def run_bot():
    send_telegram("🤖 بوت توافق 3 فريمات بدأ العمل ✅\nالمنصات: MEXC, Gate, Bitget, OKX, Bybit")

    while True:
        try:
            monitor_active_targets()
            scan_market()
            monitor_active_targets()
        except Exception as e:
            print("Scanner error:", e)

        print(f"Sleeping {SCAN_INTERVAL} seconds...")
        time.sleep(SCAN_INTERVAL)

# =========================================================
# Start
# =========================================================

if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
