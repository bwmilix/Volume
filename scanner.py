"""
Single-run Binance volume spike scanner, designed to be triggered
repeatedly by GitHub Actions (or any external scheduler) instead of
looping forever. Each run scans once and exits.

Configuration is read from environment variables so secrets (like the
Telegram token) never need to be written into the code:

    TELEGRAM_TOKEN     - required, your bot token from BotFather
    TELEGRAM_CHAT_ID   - required, your Telegram chat id
    MARKET             - optional, "spot" or "futures" (default: spot)
    INTERVAL           - optional, candle timeframe (default: 5m)
    RVOL_THRESHOLD     - optional, minimum relative volume multiplier (default: 3)
    PCT_THRESHOLD      - optional, minimum absolute price change % (default: 1.0)
    LOOKBACK           - optional, candles used for average volume baseline (default: 20)
    QUOTE              - optional, quote asset filter (default: USDT)
"""

import os
import time
from datetime import datetime, timezone

import requests

SPOT_BASE = "https://data-api.binance.vision"
FUTURES_BASE = "https://fapi.binance.com"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MARKET = os.environ.get("MARKET", "spot")
INTERVAL = os.environ.get("INTERVAL", "5m")
RVOL_THRESHOLD = env_float("RVOL_THRESHOLD", 3.0)
PCT_THRESHOLD = env_float("PCT_THRESHOLD", 1.0)
LOOKBACK = env_int("LOOKBACK", 20)
QUOTE = os.environ.get("QUOTE", "USDT")


def send_telegram_message(text: str):
    url = TELEGRAM_API.format(token=TOKEN)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[Telegram error] {resp.status_code}: {resp.text}")
    except requests.RequestException as e:
        print(f"[Telegram error] {e}")


def get_symbols(market: str, quote: str):
    if market == "spot":
        url = f"{SPOT_BASE}/api/v3/exchangeInfo"
    else:
        url = f"{FUTURES_BASE}/fapi/v1/exchangeInfo"

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    symbols = []
    for s in data["symbols"]:
        if market == "spot":
            if s.get("status") == "TRADING" and s.get("quoteAsset") == quote and s.get("isSpotTradingAllowed", True):
                symbols.append(s["symbol"])
        else:
            if s.get("status") == "TRADING" and s.get("quoteAsset") == quote and s.get("contractType") == "PERPETUAL":
                symbols.append(s["symbol"])
    return symbols


def get_klines(market: str, symbol: str, interval: str, limit: int):
    if market == "spot":
        url = f"{SPOT_BASE}/api/v3/klines"
    else:
        url = f"{FUTURES_BASE}/fapi/v1/klines"

    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code != 200:
        return None
    return resp.json()


def analyze_symbol(klines, rvol_threshold, pct_threshold):
    if not klines or len(klines) < 6:
        return None

    last = klines[-1]
    previous = klines[:-1]

    last_open = float(last[1])
    last_close = float(last[4])
    last_volume = float(last[5])

    avg_volume = sum(float(k[5]) for k in previous) / len(previous)
    if avg_volume == 0:
        return None

    rvol = last_volume / avg_volume
    pct_change = (last_close - last_open) / last_open * 100

    if rvol >= rvol_threshold and abs(pct_change) >= pct_threshold:
        return {
            "rvol": round(rvol, 2),
            "pct_change": round(pct_change, 2),
            "close": last_close,
            "candle_time": datetime.fromtimestamp(last[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    return None


def main():
    symbols = get_symbols(MARKET, QUOTE)
    print(f"Scanning {len(symbols)} symbols on {MARKET} / {INTERVAL} "
          f"(RVOL>={RVOL_THRESHOLD}x, |change|>={PCT_THRESHOLD}%)")

    hits = []
    for i, symbol in enumerate(symbols):
        klines = get_klines(MARKET, symbol, INTERVAL, LOOKBACK + 1)
        result = analyze_symbol(klines, RVOL_THRESHOLD, PCT_THRESHOLD)
        if result:
            result["symbol"] = symbol
            hits.append(result)
        if i % 20 == 0:
            time.sleep(0.2)

    hits.sort(key=lambda x: x["rvol"], reverse=True)

    if not hits:
        print("No matches this run.")
        return

    for h in hits:
        direction = "🟢 UP" if h["pct_change"] > 0 else "🔴 DOWN"
        msg = (
            f"⚡ <b>Volume Spike</b> - {h['symbol']}\n"
            f"{direction} {h['pct_change']}%\n"
            f"RVOL: {h['rvol']}x\n"
            f"Close: {h['close']}\n"
            f"Candle: {h['candle_time']}"
        )
        print(msg.replace("\n", " | "))
        send_telegram_message(msg)


if __name__ == "__main__":
    main()
