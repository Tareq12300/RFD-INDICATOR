import os
import time
import requests
import pandas as pd
import ta

from datetime import datetime
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "RFD Spot Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web).start()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = "15m"
LIMIT = 200
SCAN_INTERVAL = 60

MIN_VOLUME_USDT = 500000
MAX_SIGNALS_PER_RUN = 5

SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT",
    "MATICUSDT","LTCUSDT","TRXUSDT","NEARUSDT","ARBUSDT",
]

sent_signals = set()

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

def get_klines(symbol, interval="15m", limit=200):

    url = "https://api.binance.com/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        df = pd.DataFrame(data, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_buy_base","taker_buy_quote","ignore"
        ])

        numeric_cols = [
            "open","high","low","close","volume","quote_volume"
        ]

        for col in numeric_cols:
            df[col] = df[col].astype(float)

        return df

    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

def analyze_symbol(symbol):

    df = get_klines(symbol, TIMEFRAME, LIMIT)

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
    liquidity_good = last["quote_volume"] >= MIN_VOLUME_USDT

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

        confidence = 100

        return {
            "symbol": symbol,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "tps": [tp1, tp2, tp3, tp4, tp5],
            "confidence": confidence
        }

    return None

def format_signal(signal):

    symbol = signal["symbol"].replace("USDT", "/USDT")

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

⚠️ Spot Only
❌ No Leverage
"""

    return message.strip()

def run_bot():

    print("RFD Spot Bot Started")

    send_telegram(
        "🔥 RFD Spot Bot Started\\n\\n✅ Spot Only\\n❌ No Leverage"
    )

    while True:

        try:

            signals_count = 0

            for symbol in SYMBOLS:

                try:

                    signal = analyze_symbol(symbol)

                    if signal:

                        signal_id = (
                            f"{symbol}_"
                            f"{TIMEFRAME}_"
                            f"{datetime.utcnow().strftime('%Y%m%d%H%M')}"
                        )

                        if signal_id not in sent_signals:

                            message = format_signal(signal)

                            send_telegram(message)

                            sent_signals.add(signal_id)

                            print(f"Signal sent: {symbol}")

                            signals_count += 1

                            if signals_count >= MAX_SIGNALS_PER_RUN:
                                break

                    time.sleep(1)

                except Exception as e:
                    print(f"Analyze Error {symbol}: {e}")

            time.sleep(SCAN_INTERVAL)

        except Exception as e:

            print("Main Loop Error:", e)
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
