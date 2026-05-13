import os
import time
import requests
import pandas as pd
import ta

from datetime import datetime
from flask import Flask
from threading import Thread

# =========================
# RAILWAY WEB SERVER
# =========================

app = Flask(__name__)

@app.route("/")
def home():
    return "RFD Spot Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web).start()

# =========================
# ENV VARIABLES
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CMC_API_KEY = os.getenv("CMC_API_KEY")

# =========================
# BOT SETTINGS
# =========================

TIMEFRAME = "15m"
OKX_BAR = "15m"

LIMIT = 200
SCAN_INTERVAL = 300

TOP_CMC_LIMIT = 1000

MIN_VOLUME_USDT = 500000
MAX_SIGNALS_PER_RUN = 5

sent_signals = set()

# =========================
# TELEGRAM
# =========================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables missing")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)

        if response.status_code != 200:
            print("Telegram Error:", response.text)

    except Exception as e:
        print("Telegram Exception:", e)

# =========================
# COINMARKETCAP TOP 1000
# =========================

def get_cmc_top_symbols():
    if not CMC_API_KEY:
        print("CMC_API_KEY missing")
        return []

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    params = {
        "start": "1",
        "limit": str(TOP_CMC_LIMIT),
        "convert": "USD",
        "sort": "market_cap"
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()

        data = response.json().get("data", [])

        symbols = []

        for coin in data:
            symbol = coin.get("symbol")

            if symbol:
                symbols.append(symbol.upper())

        print(f"CMC symbols loaded: {len(symbols)}")

        return symbols

    except Exception as e:
        print("CMC Error:", e)
        return []

# =========================
# OKX SPOT INSTRUMENTS
# =========================

def get_okx_spot_pairs():
    url = "https://www.okx.com/api/v5/public/instruments"

    params = {
        "instType": "SPOT"
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()

        data = response.json().get("data", [])

        pairs = {}

        for item in data:
            inst_id = item.get("instId")
            base_ccy = item.get("baseCcy")
            quote_ccy = item.get("quoteCcy")
            state = item.get("state")

            if (
                inst_id
                and base_ccy
                and quote_ccy == "USDT"
                and state == "live"
            ):
                pairs[base_ccy.upper()] = inst_id

        print(f"OKX Spot USDT pairs loaded: {len(pairs)}")

        return pairs

    except Exception as e:
        print("OKX Instruments Error:", e)
        return {}

# =========================
# BUILD WATCHLIST
# =========================

def build_watchlist():
    cmc_symbols = get_cmc_top_symbols()
    okx_pairs = get_okx_spot_pairs()

    watchlist = []

    for symbol in cmc_symbols:
        if symbol in okx_pairs:
            watchlist.append(okx_pairs[symbol])

    print(f"Final OKX watchlist: {len(watchlist)}")

    return watchlist

# =========================
# OKX CANDLES
# =========================

def get_okx_candles(inst_id):
    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": inst_id,
        "bar": OKX_BAR,
        "limit": str(LIMIT)
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()

        data = response.json().get("data", [])

        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            "ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volume_ccy",
            "volume_quote",
            "confirm"
        ])

        df = df.iloc[::-1].reset_index(drop=True)

        numeric_cols = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volume_quote"
        ]

        for col in numeric_cols:
            df[col] = df[col].astype(float)

        return df

    except Exception as e:
        print(f"OKX Candles Error {inst_id}: {e}")
        return None

# =========================
# ANALYZE SYMBOL
# =========================

def analyze_symbol(inst_id):
    df = get_okx_candles(inst_id)

    if df is None or len(df) < 100:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["ema20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()

    df["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd = ta.trend.MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    atr = ta.volatility.AverageTrueRange(
        high=high,
        low=low,
        close=close,
        window=14
    )

    df["atr"] = atr.average_true_range()
    df["volume_ma"] = volume.rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    price = last["close"]

    trend_up = last["ema20"] > last["ema50"]

    rsi_good = 45 <= last["rsi"] <= 68

    macd_cross = (
        last["macd"] > last["macd_signal"]
        and prev["macd"] <= prev["macd_signal"]
    )

    volume_good = last["volume"] > last["volume_ma"]

    liquidity_good = last["volume_quote"] >= MIN_VOLUME_USDT

    if trend_up and rsi_good and macd_cross and volume_good and liquidity_good:
        atr_value = last["atr"]

        entry_low = price * 0.995
        entry_high = price * 1.005

        stop_loss = price - (atr_value * 1.8)

        tp1 = price + (atr_value * 1.2)
        tp2 = price + (atr_value * 2.0)
        tp3 = price + (atr_value * 2.8)
        tp4 = price + (atr_value * 3.6)
        tp5 = price + (atr_value * 4.5)

        confidence = 0

        if trend_up:
            confidence += 25

        if rsi_good:
            confidence += 20

        if macd_cross:
            confidence += 25

        if volume_good:
            confidence += 20

        if liquidity_good:
            confidence += 10

        return {
            "symbol": inst_id,
            "price": price,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "tps": [tp1, tp2, tp3, tp4, tp5],
            "confidence": confidence
        }

    return None

# =========================
# FORMAT SIGNAL
# =========================

def format_signal(signal):
    symbol = signal["symbol"].replace("-", "/")

    tps_text = "\n".join([
        f"{tp:.6f}" for tp in signal["tps"]
    ])

    message = f"""
🔥 *RFD Indicator*

⏰ {TIMEFRAME} Timeframe

✅ *SPOT LONG*

#{symbol}

📍 *Entry Zone*
{signal["entry_low"]:.6f}
to
{signal["entry_high"]:.6f}

🎯 *Take Profits*
{tps_text}

🛑 *Stop Loss*
{signal["stop_loss"]:.6f}

📊 *Confidence*
{signal["confidence"]}%
"""

    return message.strip()

# =========================
# RUN BOT
# =========================

def run_bot():
    print("RFD Spot Bot Started")

    # الرسالة الترحيبية بعد حذف الجزء المطلوب
    send_telegram("🔥 RFD Spot Bot Started")

    while True:
        try:
            watchlist = build_watchlist()

            if not watchlist:
                print("Watchlist empty, retrying later...")
                time.sleep(SCAN_INTERVAL)
                continue

            signals_count = 0

            for inst_id in watchlist:
                try:
                    signal = analyze_symbol(inst_id)

                    if signal:
                        signal_id = (
                            f"{inst_id}_"
                            f"{TIMEFRAME}_"
                            f"{datetime.utcnow().strftime('%Y%m%d%H%M')}"
                        )

                        if signal_id not in sent_signals:
                            message = format_signal(signal)

                            send_telegram(message)

                            sent_signals.add(signal_id)

                            print(f"Signal sent: {inst_id}")

                            signals_count += 1

                            if signals_count >= MAX_SIGNALS_PER_RUN:
                                break

                    time.sleep(0.2)

                except Exception as e:
                    print(f"Analyze Error {inst_id}: {e}")

            print("Scan completed")
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("Main Loop Error:", e)
            time.sleep(60)

# =========================
# START
# =========================

if __name__ == "__main__":
    run_bot()
