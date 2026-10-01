import json
import logging
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
MIN_PRICE = 3.00
MIN_MARKET_CAP = 25_000_000
MIN_VOLUME = 100_000
RVOL_THRESHOLD = 3.0
LOOKBACK_DAYS = 20
TOP_N = 40
CHUNK = 200
NOT_COMMON = ("%", "NOTES", "PREFERRED", "DEBENTURE", "WARRANTS", " UNITS", " RIGHTS", " FUND", "INCOME TRUST", "MUNICIPAL", "DEPOSITARY SHARES EACH")
CACHE = Path(__file__).with_name("data")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def number(text):
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0


def listed_stocks():
    req = urllib.request.Request(NASDAQ_SCREENER, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        rows = json.load(res)["data"]["rows"]
    frame = pd.DataFrame(
        {
            "symbol": [r["symbol"].strip() for r in rows],
            "name": [r["name"] for r in rows],
            "price": [number(r["lastsale"]) for r in rows],
            "prev_close": [number(r["lastsale"]) - number(r["netchange"]) for r in rows],
            "change_pct": [number(r["pctchange"]) for r in rows],
            "volume": [number(r["volume"]) for r in rows],
            "market_cap": [number(r["marketCap"]) for r in rows],
        }
    )
    return frame[~frame["symbol"].str.contains(r"[\^/ ]")]


def passes_hard_rules(stocks):
    return stocks[
        (stocks["price"] > MIN_PRICE)
        & (stocks["prev_close"] > MIN_PRICE)
        & (stocks["market_cap"] > MIN_MARKET_CAP)
        & (stocks["volume"] >= MIN_VOLUME)
        & ~stocks["name"].str.upper().apply(lambda name: any(tag in name for tag in NOT_COMMON))
    ]


def download_volume(symbols):
    frames = []
    for start in range(0, len(symbols), CHUNK):
        chunk = symbols[start:start + CHUNK]
        for attempt in range(3):
            volume = yf.download(chunk, period="3mo", interval="1d", progress=False, threads=True)["Volume"]
            missing = [s for s in chunk if s not in volume or volume[s].isna().all()]
            if len(missing) < len(chunk) / 2 or attempt == 2:
                break
            time.sleep(20 * (attempt + 1))
        frames.append(volume)
        time.sleep(2)
    return pd.concat(frames, axis=1)


def average_volume(symbols, today):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"avg_volume_{today}.parquet"
    cached = pd.read_parquet(path)["avg_volume"] if path.exists() else pd.Series(dtype=float)
    todo = [s for s in symbols if s not in cached.index]
    if todo:
        history = download_volume(todo)
        history = history[history.index.date < today]
        fresh = history.tail(LOOKBACK_DAYS).mean().dropna()
        cached = pd.concat([cached, fresh])
        cached.to_frame("avg_volume").to_parquet(path)
    return cached.reindex(symbols)


def scan(today):
    stocks = passes_hard_rules(listed_stocks()).set_index("symbol")
    stocks["avg_volume"] = average_volume(list(stocks.index), today)
    unpriced = stocks["avg_volume"].isna().sum()
    if unpriced:
        print(f"No volume history for {unpriced} stocks, skipped. Rerun to retry them.", file=sys.stderr)
    stocks = stocks[stocks["avg_volume"] > 0]
    stocks["rvol"] = stocks["volume"] / stocks["avg_volume"]
    return stocks[stocks["rvol"] >= RVOL_THRESHOLD].sort_values("rvol", ascending=False)


def print_report(hits, now):
    print(f"Unusual volume scan, {now:%Y-%m-%d %H:%M} ET")
    print(f"Rules: price > ${MIN_PRICE:.0f}, market cap > ${MIN_MARKET_CAP / 1e6:.0f}M, volume >= {RVOL_THRESHOLD:.0f}x {LOOKBACK_DAYS}-day average")
    if now.hour < 16:
        print("Market still open. Today's volume is partial, so RVOL runs low until the close.")
    print()
    if hits.empty:
        print("Nothing passed.")
        return
    print(f"{'SYMBOL':<7}{'RVOL':>7}{'PRICE':>10}{'CHG%':>8}{'VOLUME':>13}{'AVG VOL':>13}{'MCAP':>10}  NAME")
    for symbol, s in hits.head(TOP_N).iterrows():
        print(
            f"{symbol:<7}{s.rvol:>6.1f}x{s.price:>10.2f}{s.change_pct:>+8.1f}"
            f"{s.volume:>13,.0f}{s.avg_volume:>13,.0f}{s.market_cap / 1e6:>9,.0f}M  {s['name'][:40]}"
        )
    print(f"\n{len(hits)} stocks passed, showing top {min(TOP_N, len(hits))}.")


def main():
    now = datetime.now(ZoneInfo("America/New_York"))
    if len(sys.argv) > 1:
        global RVOL_THRESHOLD
        RVOL_THRESHOLD = float(sys.argv[1])
    print_report(scan(now.date()), now)


if __name__ == "__main__":
    main()
