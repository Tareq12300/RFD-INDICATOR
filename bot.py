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
    return "RFD Multi Exchange 4H Spot Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web).start()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CMC_API_KEY = os.getenv("CMC_API_KEY")

TIMEFRAME = "4H"
SCAN_INTERVAL = 1800
LIMIT = 200
TOP_CMC_LIMIT = 1000

MIN_VOLUME_USDT = 200000
MAX_SIGNALS_PER_RUN = 5
MIN_CONFIDENCE = 75

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
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print("Telegram Error:", r.text)
    except Exception as e:
        print("Telegram Exception:", e)


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
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()

        data = r.json().get("data", [])
        symbols = []

        for coin in data:
            s = coin.get("symbol")
            if s:
                symbols.append(s.upper())

        print(f"CMC symbols loaded: {len(symbols)}")
        return symbols

    except Exception as e:
        print("CMC Error:", e)
        return []


def get_okx_spot_pairs():
    url = "https://www.okx.com/api/v5/public/instruments"

    try:
        r = requests.get(url, params={"instType": "SPOT"}, timeout=20)
        r.raise_for_status()

        data = r.json().get("data", [])
        pairs = {}

        for item in data:
            inst_id = item.get("instId")
            base = item.get("baseCcy")
            quote = item.get("quoteCcy")
            state = item.get("state")

            if inst_id and base and quote == "USDT" and state == "live":
                pairs[base.upper()] = inst_id

        print(f"OKX Spot pairs loaded: {len(pairs)}")
        return pairs

    except Exception as e:
        print("OKX Error:", e)
        return {}


def get_bybit_spot_pairs():
    url = "https://api.bybit.com/v5/market/instruments-info"

    try:
        r = requests.get(url, params={"category": "spot"}, timeout=20)
        r.raise_for_status()

        data = r.json().get("result", {}).get("list", [])
        pairs = {}

        for item in data:
            symbol = item.get("symbol")
            status = item.get("status", "")

            if symbol and symbol.endswith("USDT") and status == "Trading":
                base = symbol.replace("USDT", "")
                pairs[base.upper()] = symbol

        print(f"Bybit Spot pairs loaded: {len(pairs)}")
        return pairs

    except Exception as e:
        print("Bybit Error:", e)
        return {}


def get_bitget_spot_pairs():
    url = "https://api.bitget.com/api/v2/spot/public/symbols"

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()

        data = r.json().get("data", [])
        pairs = {}

        for item in data:
            symbol = item.get("symbol")
            base = item.get("baseCoin")
            quote = item.get("quoteCoin")
            status = item.get("status")

            if symbol and base and quote == "USDT" and status == "online":
                pairs[base.upper()] = symbol

        print(f"Bitget Spot pairs loaded: {len(pairs)}")
        return pairs

    except Exception as e:
        print("Bitget Error:", e)
        return {}


def get_gate_spot_pairs():
    url = "https://api.gateio.ws/api/v4/spot/currency_pairs"

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()

        data = r.json()
        pairs = {}

        for item in data:
            pair_id = item.get("id")
            base = item.get("base")
            quote = item.get("quote")
            trade_status = item.get("trade_status")

            if pair_id and base and quote == "USDT" and trade_status == "tradable":
                pairs[base.upper()] = pair_id

        print(f"Gate Spot pairs loaded: {len(pairs)}")
        return pairs

    except Exception as e:
        print("Gate Error:", e)
        return {}


def build_watchlist():
    cmc_symbols = get_cmc_top_symbols()

    okx = get_okx_spot_pairs()
    bybit = get_bybit_spot_pairs()
    bitget = get_bitget_spot_pairs()
    gate = get_gate_spot_pairs()

    watchlist = []
    used = set()

    for s in cmc_symbols:
        if s in used:
            continue

        if s in okx:
            watchlist.append({"exchange": "OKX", "symbol": okx[s]})
            used.add(s)

        elif s in bybit:
            watchlist.append({"exchange": "BYBIT", "symbol": bybit[s]})
            used.add(s)

        elif s in bitget:
            watchlist.append({"exchange": "BITGET", "symbol": bitget[s]})
            used.add(s)

        elif s in gate:
            watchlist.append({"exchange": "GATE", "symbol": gate[s]})
            used.add(s)

    print(f"Final multi-exchange watchlist: {len(watchlist)}")
    return watchlist


def get_okx_candles(symbol):
    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": symbol,
        "bar": "4H",
        "limit": str(LIMIT)
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()

        data = r.json().get("data", [])

        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            "ts", "open", "high", "low", "close",
            "volume", "volume_ccy", "volume_quote", "confirm"
        ])

        df = df.iloc[::-1].reset_index(drop=True)

        for col in ["open", "high", "low", "close", "volume", "volume_quote"]:
            df[col] = df[col].astype(float)

        return df

    except Exception as e:
        print(f"OKX Candles Error {symbol}: {e}")
        return None


def get_bybit_candles(symbol):
    url = "https://api.bybit.com/v5/market/kline"

    params = {
        "category": "spot",
        "symbol": symbol,
        "interval": "240",
        "limit": str(LIMIT)
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()

        data = r.json().get("result", {}).get("list", [])

        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            "ts", "open", "high", "low", "close",
            "volume", "turnover"
        ])

        df = df.iloc[::-1].reset_index(drop=True)

        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["volume_quote"] = df["turnover"].astype(float)

        return df

    except Exception as e:
        print(f"Bybit Candles Error {symbol}: {e}")
        return None


def get_bitget_candles(symbol):
    url = "https://api.bitget.com/api/v2/spot/market/candles"

    params = {
        "symbol": symbol,
        "granularity": "4h",
        "limit": str(LIMIT)
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()

        data = r.json().get("data", [])

        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            "ts", "open", "high", "low", "close",
            "volume", "volume_quote", "volume_usdt"
        ])

        df = df.iloc[::-1].reset_index(drop=True)

        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["volume_quote"] = df["volume_quote"].astype(float)

        return df

    except Exception as e:
        print(f"Bitget Candles Error {symbol}: {e}")
        return None


def get_gate_candles(symbol):
    url = "https://api.gateio.ws/api/v4/spot/candlesticks"

    params = {
        "currency_pair": symbol,
        "interval": "4h",
        "limit": str(LIMIT)
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()

        data = r.json()

        if not data:
            return None

        rows = []

        for item in data:
            rows.append({
                "ts": item[0],
                "volume_quote": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "open": float(item[5]),
                "volume": float(item[6])
            })

        df = pd.DataFrame(rows)
        df = df.sort_values("ts").reset_index(drop=True)

        return df

    except Exception as e:
        print(f"Gate Candles Error {symbol}: {e}")
        return None


def get_candles(exchange, symbol):
    if exchange == "OKX":
        return get_okx_candles(symbol)

    if exchange == "BYBIT":
        return get_bybit_candles(symbol)

    if exchange == "BITGET":
        return get_bitget_candles(symbol)

    if exchange == "GATE":
        return get_gate_candles(symbol)

    return None


def analyze_symbol(exchange, symbol):
    df = get_candles(exchange, symbol)

    if df is None or len(df) < 100:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["ema20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df["ema100"] = ta.trend.EMAIndicator(close, window=100).ema_indicator()

    df["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    stoch = ta.momentum.StochRSIIndicator(
        close=close,
        window=14,
        smooth1=3,
        smooth2=3
    )

    df["stoch_k"] = stoch.stochrsi_k() * 100
    df["stoch_d"] = stoch.stochrsi_d() * 100

    macd = ta.trend.MACD(close)

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

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
    prev2 = df.iloc[-3]

    price = last["close"]

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
        and (last["close"] - last["open"]) >=
        (last["high"] - last["low"]) * 0.35
    )

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

    early_signal = (
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

    if not early_signal:
        return None

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
        "exchange": exchange,
        "symbol": symbol,
        "price": price,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "tps": [tp1, tp2, tp3, tp4, tp5],
        "confidence": min(confidence, 100),
        "rsi": last["rsi"],
        "stoch_k": last["stoch_k"],
        "volume_quote": last["volume_quote"]
    }


def format_symbol(exchange, symbol):
    if exchange == "OKX":
        return symbol.replace("-", "/")

    if exchange == "BYBIT":
        return symbol.replace("USDT", "/USDT")

    if exchange == "BITGET":
        return symbol.replace("USDT", "/USDT")

    if exchange == "GATE":
        return symbol.replace("_", "/")

    return symbol


def format_signal(signal):
    symbol = format_symbol(signal["exchange"], signal["symbol"])

    tps_text = "\n".join([f"{tp:.6f}" for tp in signal["tps"]])

    message = f"""
🔥 *RFD Indicator*

🚀 *EARLY SPOT LONG*

🏦 Exchange: {signal["exchange"]}

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


def run_bot():
    print("RFD Multi Exchange 4H Spot Bot Started")
    send_telegram("🔥 RFD Multi Exchange 4H Spot Bot Started")

    while True:
        try:
            watchlist = build_watchlist()

            if not watchlist:
                print("Watchlist empty")
                time.sleep(SCAN_INTERVAL)
                continue

            signals_count = 0

            for item in watchlist:
                exchange = item["exchange"]
                symbol = item["symbol"]

                try:
                    signal = analyze_symbol(exchange, symbol)

                    if signal:
                        signal_id = (
                            f"{exchange}_{symbol}_{TIMEFRAME}_"
                            f"{datetime.utcnow().strftime('%Y%m%d%H')}"
                        )

                        if signal_id not in sent_signals:
                            send_telegram(format_signal(signal))
                            sent_signals.add(signal_id)

                            print(f"Signal sent: {exchange} {symbol}")

                            signals_count += 1

                            if signals_count >= MAX_SIGNALS_PER_RUN:
                                break

                    time.sleep(0.25)

                except Exception as e:
                    print(f"Analyze Error {exchange} {symbol}: {e}")

            print("Scan completed")
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("Main Loop Error:", e)
            time.sleep(60)


if __name__ == "__main__":
    run_bot()
