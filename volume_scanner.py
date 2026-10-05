import json
import logging
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
MIN_PRICE = 3.00
MIN_MARKET_CAP = 25_000_000
MIN_VOLUME = 100_000
RVOL_THRESHOLD = 3.0
LOOKBACK_DAYS = 20
TOP_N = 40
EWMA_LAMBDA = 0.94
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


def download_bars(symbols, period):
    frames = []
    for start in range(0, len(symbols), CHUNK):
        chunk = symbols[start:start + CHUNK]
        for attempt in range(3):
            bars = yf.download(chunk, period=period, interval="1d", progress=False, threads=True, auto_adjust=True)
            missing = [s for s in chunk if s not in bars["Close"] or bars["Close"][s].isna().all()]
            if len(missing) < len(chunk) / 2 or attempt == 2:
                break
            time.sleep(20 * (attempt + 1))
        frames.append(bars)
        time.sleep(2)
    return pd.concat(frames, axis=1)


def volume_and_volatility(symbols, today):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"history_stats_{today}.parquet"
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["avg_volume", "ewma_var"])
    todo = [s for s in symbols if s not in cached.index]
    if todo:
        history = download_bars(todo, "6mo")
        history = history[history.index.date < today]
        log_returns = np.log(history["Close"]).diff()
        fresh = pd.DataFrame({
            "avg_volume": history["Volume"].tail(LOOKBACK_DAYS).mean(),
            "ewma_var": log_returns.pow(2).ewm(alpha=1 - EWMA_LAMBDA, adjust=False).mean().iloc[-1],
        }).dropna()
        cached = pd.concat([cached, fresh])
        cached.to_parquet(path)
    return cached.astype(float).reindex(symbols)


def scan(today):
    stocks = passes_hard_rules(listed_stocks()).set_index("symbol")
    stats = volume_and_volatility(list(stocks.index), today)
    stocks["avg_volume"] = stats["avg_volume"]
    today_return = np.log(stocks["price"] / stocks["prev_close"])
    stocks["vol"] = np.sqrt(EWMA_LAMBDA * stats["ewma_var"] + (1 - EWMA_LAMBDA) * today_return**2)
    unpriced = stocks["avg_volume"].isna().sum()
    if unpriced:
        print(f"No volume history for {unpriced} stocks, skipped. Rerun to retry them.", file=sys.stderr)
    stocks = stocks[stocks["avg_volume"] > 0]
    stocks["rvol"] = stocks["volume"] / stocks["avg_volume"]
    return stocks[stocks["rvol"] >= RVOL_THRESHOLD].sort_values("rvol", ascending=False)


def print_report(hits, now, multipliers):
    from price_range import TARGET, ranges

    print(f"Unusual volume scan, {now:%Y-%m-%d %H:%M} ET")
    print(f"Rules: price > ${MIN_PRICE:.0f}, market cap > ${MIN_MARKET_CAP / 1e6:.0f}M, volume >= {RVOL_THRESHOLD:.0f}x {LOOKBACK_DAYS}-day average")
    if now.hour < 16:
        print("Market still open. Today's volume is partial, so RVOL runs low until the close.")
    print()
    if hits.empty:
        print("Nothing passed.")
        return
    print(f"{'SYMBOL':<7}{'RVOL':>7}{'PRICE':>10}{'CHG%':>8}{'AVG VOL':>13}{'MCAP':>10}{'NEXT CLOSE':>21}{'5 DAYS OUT':>21}  NAME")
    for symbol, s in hits.head(TOP_N).iterrows():
        band = ranges(s.price, s.vol, multipliers)
        print(
            f"{symbol:<7}{s.rvol:>6.1f}x{s.price:>10.2f}{s.change_pct:>+8.1f}"
            f"{s.avg_volume:>13,.0f}{s.market_cap / 1e6:>9,.0f}M"
            f"{band[1][0]:>11.2f}-{band[1][1]:<9.2f}{band[5][0]:>11.2f}-{band[5][1]:<9.2f}"
            f"{'*' if abs(s.change_pct) > 50 else ' '} {s['name'][:30]}"
        )
    print(f"\n{len(hits)} stocks passed, showing top {min(TOP_N, len(hits))}.")
    print(f"Ranges are where the close landed {TARGET:.0%} of the time in backtests of past flags. They say how far a stock may move, not which way.")
    if (hits.head(TOP_N)["change_pct"].abs() > 50).any():
        print("* Moved more than 50% today. If that came from a split or spin-off, the data source didn't adjust for it and the range is wrong.")


def main():
    now = datetime.now(ZoneInfo("America/New_York"))
    if len(sys.argv) > 1:
        global RVOL_THRESHOLD
        RVOL_THRESHOLD = float(sys.argv[1])
    from price_range import calibrate
    print_report(scan(now.date()), now, calibrate(now.date()))


if __name__ == "__main__":
    main()
