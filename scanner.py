"""
Single-run Binance 5m top-movers scanner for GitHub Actions.
Reports the coins with the largest absolute price change in the last
5-minute candle, ranked against all other coins (not a fixed threshold).
"""

import os
import time
from datetime import datetime, timezone

import requests

SPOT_BASE = "https://data-api.binance.vision"
FUTURES_BASE = "https://fapi.binance.com"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MARKET = os.environ.get("MARKET", "spot")
INTERVAL = os.environ.get("INTERVAL", "5m")
QUOTE = os.environ.get("QUOTE", "USDT")
TOP_N = int(os.environ.get("TOP_N", "10"))
MIN_VOLUME_USDT = float(os.environ.get("MIN_VOLUME_USDT", "50000"))


def send_telegram_message(text: str):
    url = TELEGRAM_API.format(token=TOKEN)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[Telegram error] {resp.status_code}: {resp.text}")
    except requests.RequestException as e:
        print(f"[Telegram error] {e}")


def get_symbols(market, quote):
    url = f"{SPOT_BASE}/api/v3/exchangeInfo" if market == "spot" else f"{FUTURES_BASE}/fapi/v1/exchangeInfo"
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


def get_last_kline(market, symbol, interval):
    url = f"{SPOT_BASE}/api/v3/klines" if market == "spot" else f"{FUTURES_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": 2}
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code != 200:
        return None
    return resp.json()


def analyze(kline):
    if not kline or len(kline) < 1:
        return None
    last = kline[-1]
    open_p = float(last[1])
    close_p = float(last[4])
    volume = float(last[5])
    quote_volume = float(last[7])  # volume in quote asset (USDT)
    if open_p == 0:
        return None
    pct_change = (close_p - open_p) / open_p * 100
    candle_time = datetime.fromtimestamp(last[0] / 1000, tz=timezone.utc).strftime("%H:%M UTC")
    return {
        "pct_change": round(pct_change, 2),
        "close": close_p,
        "volume_usdt": quote_volume,
        "candle_time": candle_time,
    }


def main():
    symbols = get_symbols(MARKET, QUOTE)
    print(f"Scanning {len(symbols)} symbols on {MARKET} / {INTERVAL}")

    results = []
    for i, symbol in enumerate(symbols):
        kline = get_last_kline(MARKET, symbol, INTERVAL)
        info = analyze(kline)
        if info and info["volume_usdt"] >= MIN_VOLUME_USDT:
            info["symbol"] = symbol
            results.append(info)
        if i % 20 == 0:
            time.sleep(0.2)

    if not results:
        print("No data collected this run.")
        return

    results.sort(key=lambda x: abs(x["pct_change"]), reverse=True)
    top = results[:TOP_N]

    lines = [f"📊 <b>Top {len(top)} movers (5m)</b>"]
    for r in top:
        direction = "🟢" if r["pct_change"] > 0 else "🔴"
        lines.append(
            f"{direction} <b>{r['symbol']}</b> {r['pct_change']}% | "
            f"Vol: {int(r['volume_usdt']):,} USDT | Close: {r['close']}"
        )
    msg = "\n".join(lines)
    print(msg.replace("\n", " | "))
    send_telegram_message(msg)



if __name__ == "__main__":
    main()
