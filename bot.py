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
    return "RFD Early Pump Spot Bot Running"

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

MIN_VOLUME_USDT = 200000
MAX_SIGNALS_PER_RUN = 5
MIN_CONFIDENCE = 75

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
# OKX SPOT PAIRS
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
# ANALYZE EARLY PUMP
# =========================

def analyze_symbol(inst_id):
    df = get_okx_candles(inst_id)

    if df is None or len(df) < 100:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # EMA
    df["ema20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df["ema100"] = ta.trend.EMAIndicator(close, window=100).ema_indicator()

    # RSI
    df["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    # STOCH RSI
    stoch = ta.momentum.StochRSIIndicator(
        close=close,
        window=14,
        smooth1=3,
        smooth2=3
    )

    df["stoch_k"] = stoch.stochrsi_k() * 100
    df["stoch_d"] = stoch.stochrsi_d() * 100

    # MACD
    macd = ta.trend.MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ATR
    atr = ta.volatility.AverageTrueRange(
        high=high,
        low=low,
        close=close,
        window=14
    )

    df["atr"] = atr.average_true_range()

    # VOLUME
    df["volume_ma"] = volume.rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    price = last["close"]

    # =========================
    # EARLY PUMP CONDITIONS
    # =========================

    stoch_oversold_reversal = (
        prev["stoch_k"] < 25
        and last["stoch_k"] > prev["stoch_k"]
        and last["stoch_k"] > last["stoch_d"]
    )

    macd_early_reversal = (
        last["macd_hist"] > prev["macd_hist"]
        and prev["macd_hist"] > prev2["macd_hist"]
    )

    rsi_recovery = (
        last["rsi"] > prev["rsi"]
        and 35 <= last["rsi"] <= 62
    )

    volume_building = (
        last["volume"] >= last["volume_ma"] * 0.8
    )

    green_candle = (
        last["close"] > last["open"]
    )

    near_bottom = (
        price <= df["low"].tail(40).min() * 1.12
    )

    trend_not_dead = (
        price > last["ema100"] * 0.92
    )

    liquidity_good = (
        last["volume_quote"] >= MIN_VOLUME_USDT
    )

    candle_strength = (
        (last["close"] - last["open"]) > 0
        and (last["close"] - last["open"]) >= (last["high"] - last["low"]) * 0.35
    )

    # =========================
    # CONFIDENCE SCORE
    # =========================

    confidence = 0

    if stoch_oversold_reversal:
        confidence += 25

    if macd_early_reversal:
        confidence += 20

    if rsi_recovery:
        confidence += 15

    if volume_building:
        confidence += 15

    if green_candle:
        confidence += 10

    if near_bottom:
        confidence += 10

    if trend_not_dead:
        confidence += 5

    if liquidity_good:
        confidence += 10

    if candle_strength:
        confidence += 10

    early_pump_signal = (
        stoch_oversold_reversal
        and macd_early_reversal
        and rsi_recovery
        and volume_building
        and green_candle
        and near_bottom
        and trend_not_dead
        and liquidity_good
        and confidence >= MIN_CONFIDENCE
    )

    if early_pump_signal:
        atr_value = last["atr"]

        entry_low = price * 0.997
        entry_high = price * 1.006

        stop_loss = price - (atr_value * 1.6)

        tp1 = price + (atr_value * 1.2)
        tp2 = price + (atr_value * 2.0)
        tp3 = price + (atr_value * 2.8)
        tp4 = price + (atr_value * 3.6)
        tp5 = price + (atr_value * 4.5)

        return {
            "symbol": inst_id,
            "price": price,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "tps": [tp1, tp2, tp3, tp4, tp5],
            "confidence": min(confidence, 100),
            "rsi": last["rsi"],
            "stoch_k": last["stoch_k"],
            "macd_hist": last["macd_hist"],
            "volume_quote": last["volume_quote"]
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

🚀 *EARLY SPOT LONG*

⏰ {TIMEFRAME} Timeframe

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

📈 *Signal Data*
RSI: {signal["rsi"]:.2f}
Stoch RSI: {signal["stoch_k"]:.2f}
Volume USDT: {signal["volume_quote"]:.2f}
"""

    return message.strip()

# =========================
# RUN BOT
# =========================

def run_bot():
    print("RFD Early Pump Spot Bot Started")

    send_telegram("🔥 RFD Early Pump Spot Bot Started")

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
