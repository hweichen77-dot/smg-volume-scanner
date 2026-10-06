import json
import sys
from datetime import date

import numpy as np
import pandas as pd

from volume_scanner import CACHE, MIN_PRICE, MIN_VOLUME, RVOL_THRESHOLD, download_bars, listed_stocks, passes_hard_rules

TARGET = 0.90
ACI_TARGET = 0.91
ACI_GAMMA = 0.005
WINDOW = 3000
WARMUP = 500
HORIZONS = (1, 5)
EWMA_LAMBDA = 0.94
HISTORY = "10y"
CALIBRATION = CACHE / "range_calibration.json"
VOL_BUCKETS = (0.03, 0.06)


def bucket(vol):
    return int(np.searchsorted(VOL_BUCKETS, vol))


def ewma_vol(log_returns):
    return np.sqrt(pd.Series(log_returns).pow(2).ewm(alpha=1 - EWMA_LAMBDA, adjust=False).mean().to_numpy())


def load_history(today):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"bars_{HISTORY}_{today}.parquet"
    if not path.exists():
        symbols = list(passes_hard_rules(listed_stocks())["symbol"])
        wide = download_bars(symbols, HISTORY)
        bars = wide.stack(level=1, future_stack=True).dropna(subset=["Close"])
        bars.index.names = ["date", "symbol"]
        bars.columns = [c.lower() for c in bars.columns]
        bars.reset_index().to_parquet(path)
    bars = pd.read_parquet(path)
    return bars[bars["date"] < pd.Timestamp(today)]


@np.errstate(divide="ignore", invalid="ignore")
def flag_events(bars):
    rows = []
    for symbol, g in bars.groupby("symbol"):
        close, volume, dates = g["close"].to_numpy(), g["volume"].to_numpy(), g["date"].to_numpy()
        if len(close) < 60:
            continue
        rvol = volume / pd.Series(volume).shift(1).rolling(20).mean().to_numpy()
        log_close = np.log(close)
        vol = ewma_vol(np.diff(log_close, prepend=log_close[0]))
        prev = np.r_[0, close[:-1]]
        for i in np.flatnonzero((rvol >= RVOL_THRESHOLD) & (close > MIN_PRICE) & (prev > MIN_PRICE) & (volume >= MIN_VOLUME)):
            if i < 40:
                continue
            row = dict(symbol=symbol, flagged=pd.Timestamp(dates[i]), vol=vol[i])
            for h in HORIZONS:
                if i + h < len(close):
                    row[f"move{h}"] = log_close[i + h] - log_close[i]
                    row[f"resolved{h}"] = pd.Timestamp(dates[i + h])
            rows.append(row)
    return pd.DataFrame(rows)


def walk_forward_all(events, h):
    parts, finals = [], {}
    for b, group in events.groupby(events["vol"].map(bucket)):
        scored, final = walk_forward(group, h)
        parts.append(scored)
        finals[b] = final
    return pd.concat(parts).sort_values("flagged"), finals


def walk_forward(events, h):
    done = events.dropna(subset=[f"move{h}"]).copy()
    done["score"] = done[f"move{h}"].abs() / (done["vol"] * np.sqrt(h))
    done = done.sort_values("flagged").reset_index(drop=True)
    by_resolve = done.sort_values(f"resolved{h}")
    history, alpha = [], 1 - ACI_TARGET
    resolved_upto = 0
    multipliers = np.full(len(done), np.nan)
    for flagged, group in done.groupby("flagged", sort=True):
        while resolved_upto < len(by_resolve) and by_resolve[f"resolved{h}"].iat[resolved_upto] <= flagged:
            row = by_resolve.iloc[resolved_upto]
            if not np.isnan(multipliers[row.name]):
                miss = float(row["score"] > multipliers[row.name])
                alpha = np.clip(alpha + ACI_GAMMA * ((1 - ACI_TARGET) - miss), 0.001, 0.5)
            history.append(row["score"])
            resolved_upto += 1
        if len(history) >= WARMUP:
            multipliers[group.index] = np.quantile(history[-WINDOW:], np.clip(1 - alpha, 0.5, 0.999))
    done["multiplier"] = multipliers
    final = np.quantile(history[-WINDOW:], np.clip(1 - alpha, 0.5, 0.999))
    return done.dropna(subset=["multiplier"]), final


def calibrate(today):
    if CALIBRATION.exists():
        saved = json.loads(CALIBRATION.read_text())
        if saved.get("target") == ACI_TARGET and (today - date.fromisoformat(saved["asof"])).days < 7:
            return {int(h): {int(b): m for b, m in per.items()} for h, per in saved["multipliers"].items()}
    events = flag_events(load_history(today))
    multipliers = {h: walk_forward_all(events, h)[1] for h in HORIZONS}
    CALIBRATION.write_text(json.dumps({"asof": today.isoformat(), "target": ACI_TARGET, "multipliers": {h: {b: float(m) for b, m in per.items()} for h, per in multipliers.items()}}))
    return multipliers


def ranges(close, vol, multipliers):
    out = {}
    for h, per in multipliers.items():
        half = per[bucket(vol)] * vol * np.sqrt(h)
        out[h] = (close * np.exp(-half), close * np.exp(half))
    return out


def backtest(today):
    events = flag_events(load_history(today))
    print(f"{TARGET:.0%} price range for stocks the scanner flags, walk-forward, no future data")
    print(f"{len(events):,} flags. Range is centered on the flag-day close and sized from the stock's own recent volatility.\n")
    for h in HORIZONS:
        scored, final = walk_forward_all(events, h)
        inside = scored["score"] <= scored["multiplier"]
        width = np.exp(scored["multiplier"] * scored["vol"] * np.sqrt(h)) - np.exp(-scored["multiplier"] * scored["vol"] * np.sqrt(h))
        above = scored[f"move{h}"] > scored["multiplier"] * scored["vol"] * np.sqrt(h)
        label = "next close" if h == 1 else f"close {h} days out"
        print(f"{label}: {len(scored):,} scored, hit {inside.mean():.1%}, median range width {width.median():.1%} of price")
        print(f"  misses above the range {above.mean():.1%}, below {(~inside & ~above).mean():.1%}")
        by_month = inside.groupby(scored["flagged"].dt.to_period("Q")).agg(["mean", "size"])
        print("  by quarter " + ", ".join(f"{q} {r['mean']:.0%}" for q, r in by_month.iterrows()))
        for band, lo, hi in (("low vol", 0, 0.03), ("mid vol", 0.03, 0.06), ("high vol", 0.06, 9)):
            part = inside[(scored["vol"] >= lo) & (scored["vol"] < hi)]
            print(f"  {band:<9}({lo:.0%}-{min(hi, 1):.0%} daily) hit {part.mean():.1%} n={len(part):,}")
        print()


def holdout(today, days=200):
    bars = load_history(today)
    trading_days = np.sort(bars["date"].unique())
    test_start = pd.Timestamp(trading_days[-days])
    events = flag_events(bars)
    events["bucket"] = events["vol"].map(bucket)
    print(f"Holdout test on the last {days} trading days, {test_start:%Y-%m-%d} to {pd.Timestamp(trading_days[-1]):%Y-%m-%d}")
    print("Frozen uses only flags resolved before the window and never updates. Live keeps recalibrating like the scanner does.\n")
    for h in HORIZONS:
        test = events[events["flagged"] >= test_start].dropna(subset=[f"move{h}"]).copy()
        train = events[events[f"resolved{h}"] < test_start].dropna(subset=[f"move{h}"]).copy()
        for part in (train, test):
            part["score"] = part[f"move{h}"].abs() / (part["vol"] * np.sqrt(h))
        frozen = train.groupby("bucket")["score"].quantile(TARGET)
        inside = test["score"] <= test["bucket"].map(frozen)
        above = test[f"move{h}"] > test["bucket"].map(frozen) * test["vol"] * np.sqrt(h)
        live, _ = walk_forward_all(events, h)
        live = live[live["flagged"] >= test_start]
        daily = inside.groupby(test["flagged"]).mean()
        label = "next close" if h == 1 else f"close {h} days out"
        print(f"{label}: trained on {len(train):,} flags, tested on {len(test):,}")
        print(f"  frozen inside {inside.mean():.1%} (above {above.mean():.1%}, below {(~inside & ~above).mean():.1%})")
        print(f"  live inside {(live['score'] <= live['multiplier']).mean():.1%}")
        print(f"  frozen days under 70%: {(daily < 0.7).sum()}/{len(daily)}, worst {daily.min():.0%} on {daily.idxmin():%Y-%m-%d}")
        print(f"  went up, for reference: {(test[f'move{h}'] > 0).mean():.1%}\n")


if __name__ == "__main__":
    holdout(date.today()) if sys.argv[1:] == ["holdout"] else backtest(date.today())
