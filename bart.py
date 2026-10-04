import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from volume_scanner import CACHE, MIN_PRICE, RVOL_THRESHOLD, download_bars, listed_stocks, passes_hard_rules

SPIKE = np.log(1.25)
MIN_FLAT_DAYS = 2
MAX_FLAT_DAYS = 10
FLAT_RANGE = np.log(1.15)
HOLD_FRACTION = 0.5
MAX_HOLD_DAYS = 10
HISTORY = "2y"


def load_bars(symbols, today):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"bars_{HISTORY}_{today}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    wide = download_bars(symbols, HISTORY)
    bars = wide.stack(level=1, future_stack=True).dropna(subset=["Close"])
    bars.index.names = ["date", "symbol"]
    bars.columns = [c.lower() for c in bars.columns]
    bars = bars.reset_index()
    bars.to_parquet(path)
    return bars


def find_spikes(close, volume):
    rvol = volume / pd.Series(volume).shift(1).rolling(20).mean().to_numpy()
    jump = np.diff(np.log(close), prepend=np.nan)
    return {side: np.flatnonzero((sign * jump >= SPIKE) & (rvol >= RVOL_THRESHOLD)) for side, sign in (("short", 1), ("long", -1))}


def find_barts(close, volume):
    logc = np.log(close)
    events = []
    for side, spikes in find_spikes(close, volume).items():
        x = logc if side == "short" else -logc
        price = (lambda v: np.exp(v)) if side == "short" else (lambda v: np.exp(-v))
        for spike in spikes:
            base, top = x[spike - 1], x[spike]
            floor = base + HOLD_FRACTION * (top - base)
            hi = lo = top
            entry, status = None, "open"
            for day in range(spike + 1, len(x)):
                new_hi, new_lo = max(hi, x[day]), min(lo, x[day])
                if new_hi - new_lo <= FLAT_RANGE and new_lo >= floor and day - spike <= MAX_FLAT_DAYS:
                    hi, lo = new_hi, new_lo
                    continue
                status = "dead"
                if x[day] < lo and MIN_FLAT_DAYS <= day - spike - 1 <= MAX_FLAT_DAYS:
                    entry, status = day, "signal"
                break
            flat_days = (entry or len(x)) - spike - 1
            if status == "dead" or flat_days < MIN_FLAT_DAYS:
                continue
            events.append(dict(side=side, spike=spike, entry=entry, flat_days=flat_days,
                               target=price(base), stop=price(hi), trigger=price(lo)))
    return events


def trade_return(side, close, entry, target, stop):
    sign = 1 if side == "long" else -1
    for day in range(entry + 1, min(entry + MAX_HOLD_DAYS + 1, len(close))):
        move = sign * (close[day] - close[entry])
        if move >= sign * (target - close[entry]) or move <= sign * (stop - close[entry]):
            break
    else:
        day = min(entry + MAX_HOLD_DAYS, len(close) - 1)
    return sign * (close[day] / close[entry] - 1), day - entry


def backtest(bars):
    trades, baseline = [], []
    for symbol, g in bars.groupby("symbol"):
        close, volume, dates = g["close"].to_numpy(), g["volume"].to_numpy(), g["date"].to_numpy()
        for side, spikes in find_spikes(close, volume).items():
            sign = 1 if side == "long" else -1
            for entry in spikes + 1:
                if entry + MAX_HOLD_DAYS < len(close) and close[entry] > MIN_PRICE and close[entry - 1] > MIN_PRICE:
                    baseline.append(dict(side=side, ret=sign * (close[entry + MAX_HOLD_DAYS] / close[entry] - 1)))
        for e in find_barts(close, volume):
            if e["entry"] is None or close[e["entry"]] <= MIN_PRICE or close[e["entry"] - 1] <= MIN_PRICE:
                continue
            ret, held = trade_return(e["side"], close, e["entry"], e["target"], e["stop"])
            trades.append(dict(symbol=symbol, date=pd.Timestamp(dates[e["entry"]]).date(), side=e["side"], ret=ret, held=held))
    return pd.DataFrame(trades), pd.DataFrame(baseline)


def summarize(trades, baseline):
    print(f"Bart backtest, {HISTORY} of daily bars, enter at breakout close, exit at target/stop close or {MAX_HOLD_DAYS} days\n")
    print(f"{'SIDE':<7}{'GROUP':<10}{'TRADES':>7}{'WIN%':>7}{'MEAN':>8}{'MEDIAN':>8}{'AVG DAYS':>9}")
    for side in ("short", "long"):
        t = trades[trades.side == side]
        b = baseline[baseline.side == side]
        if len(t):
            print(f"{side:<7}{'bart':<10}{len(t):>7}{(t.ret > 0).mean():>7.0%}{t.ret.mean():>+8.1%}{t.ret.median():>+8.1%}{t.held.mean():>9.1f}")
        if len(b):
            print(f"{side:<7}{'any spike':<10}{len(b):>7}{(b.ret > 0).mean():>7.0%}{b.ret.mean():>+8.1%}{b.ret.median():>+8.1%}{MAX_HOLD_DAYS:>9.1f}")
    print("\nBy year (bart trades):")
    trades["year"] = pd.to_datetime(trades["date"]).dt.year
    print(trades.groupby(["side", "year"])["ret"].agg(["count", "mean", "median", lambda r: (r > 0).mean()])
          .rename(columns={"<lambda_0>": "win"}).round(3).to_string())


def scan(bars, universe):
    signals, setups = [], []
    for symbol, g in bars[bars.symbol.isin(universe.index)].groupby("symbol"):
        g = g.tail(60)
        close = g["close"].to_numpy()
        for e in find_barts(close, g["volume"].to_numpy()):
            row = dict(symbol=symbol, side=e["side"], price=close[-1], flat_days=e["flat_days"],
                       target=e["target"], stop=e["stop"], name=universe.at[symbol, "name"])
            if e["entry"] == len(close) - 1:
                signals.append(row)
            elif e["entry"] is None:
                setups.append(row | dict(trigger=e["trigger"]))
    return pd.DataFrame(signals), pd.DataFrame(setups)


def print_scan(signals, setups, now):
    print(f"Bart scan, {now:%Y-%m-%d %H:%M} ET")
    if now.hour < 16 and now.weekday() < 5:
        print("Market still open. Today's bar is partial, so a breakout can still undo itself by the close.")
    print("\nSIGNALS (plateau broke today, enter by hand):")
    if signals.empty:
        print("  none")
    for _, s in signals.iterrows():
        print(f"  {s.side.upper():<6}{s.symbol:<7}{s.price:>9.2f}  target {s.target:.2f}  stop {s.stop:.2f}  flat {s.flat_days}d  {s['name'][:35]}")
    print("\nSETUPS (spike + plateau holding, watch the trigger):")
    if setups.empty:
        print("  none")
    for _, s in setups.iterrows():
        print(f"  {s.side.upper():<6}{s.symbol:<7}{s.price:>9.2f}  trigger {s.trigger:.2f}  target {s.target:.2f}  stop {s.stop:.2f}  flat {s.flat_days}d  {s['name'][:35]}")


def main():
    now = datetime.now(ZoneInfo("America/New_York"))
    universe = passes_hard_rules(listed_stocks()).set_index("symbol")
    bars = load_bars(list(universe.index), now.date())
    if sys.argv[1:] == ["backtest"]:
        summarize(*backtest(bars))
    else:
        print_scan(*scan(bars, universe), now)


if __name__ == "__main__":
    main()
