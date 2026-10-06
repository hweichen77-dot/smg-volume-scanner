import json
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from price_range import EWMA_LAMBDA, calibrate, load_history, ranges
from research import full_report, heading
from volume_scanner import CACHE, MIN_PRICE, MIN_VOLUME, RVOL_THRESHOLD

HOLD_DAYS = 5
FEATURES = ["r1", "r5", "r20", "r60", "vol20", "rvol", "clv", "hi52"]
MODEL = CACHE / "direction_model.json"
CALIBRATION_YEARS = 2
TRAIN_EVERY = 5
MAX_MOVE = np.log(1.5)


@np.errstate(divide="ignore", invalid="ignore")
def features(bars):
    close, high, low, volume = (bars[k].astype(float) for k in ("close", "high", "low", "volume"))
    log_close = np.log(close.where(close > 0))
    r1 = log_close.diff()
    out = pd.DataFrame(index=bars.index)
    for n in (1, 5, 20, 60):
        out[f"r{n}"] = log_close - log_close.shift(n)
    out["vol20"] = r1.rolling(20).std()
    out["rvol"] = np.log(volume / volume.shift(1).rolling(20).mean())
    out["clv"] = ((close - low) / (high - low)).where(high > low, 0.5)
    out["hi52"] = (log_close - np.log(high.rolling(250, min_periods=60).max())).fillna(0)
    out["target"] = log_close.shift(-1 - HOLD_DAYS) - log_close.shift(-1)
    return out.replace([np.inf, -np.inf], np.nan)


def training_rows(history):
    parts = []
    for symbol, g in history.sort_values("date").groupby("symbol"):
        if len(g) < 80:
            continue
        f = features(g.reset_index(drop=True))
        f["date"] = g["date"].to_numpy()
        keep = (g["close"].to_numpy() > MIN_PRICE) & (g["volume"].to_numpy() >= MIN_VOLUME)
        parts.append(f[keep])
    rows = pd.concat(parts).dropna()
    return rows[(rows[["r1", "target"]].abs() < MAX_MOVE).all(axis=1)]


def design(f, mu, sd):
    z = ((f[FEATURES] - mu) / sd).clip(-4, 4).to_numpy()
    return np.column_stack([np.ones(len(z)), z, z**2])


def fit_logistic(X, y, l2=1.0, iters=15):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        hessian = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(len(w))
        w += np.linalg.solve(hessian, X.T @ (y - p) - l2 * w)
    return w


def logit(p):
    return np.log(p / (1 - p))


def raw_probability(f, model):
    return 1 / (1 + np.exp(-design(f, pd.Series(model["mu"]), pd.Series(model["sd"])) @ np.array(model["w"])))


def probability(f, model):
    a, b = model["platt"]
    return 1 / (1 + np.exp(-(a + b * logit(raw_probability(f, model)))))


def train(rows, calibration_start):
    fit_rows = rows[rows["date"] < calibration_start].iloc[::TRAIN_EVERY]
    calib = rows[rows["date"] >= calibration_start]
    mu, sd = fit_rows[FEATURES].mean(), fit_rows[FEATURES].std()
    model = {"mu": mu.to_dict(), "sd": sd.to_dict(), "w": fit_logistic(design(fit_rows, mu, sd), (fit_rows["target"] > 0).to_numpy(float)).tolist(), "platt": [0.0, 1.0]}
    x = logit(raw_probability(calib, model))
    slope = fit_logistic(np.column_stack([np.ones(len(x)), x]), (calib["target"] > 0).to_numpy(float), l2=0.0)[1]
    sample = rows.iloc[::TRAIN_EVERY]
    x, base = logit(raw_probability(sample, model)), (sample["target"] > 0).mean()
    low, high = -1.0, 1.0
    for _ in range(40):
        mid = (low + high) / 2
        low, high = (mid, high) if (1 / (1 + np.exp(-(mid + slope * x)))).mean() < base else (low, mid)
    model["platt"] = [mid, float(slope)]
    return model


def reliability(rows, model):
    p = probability(rows, model)
    up = (rows["target"] > 0).to_numpy()
    bins = pd.cut(p, [0, 0.48, 0.5, 0.52, 1])
    return {str(k): [float(v.mean()), float(up[v.index].mean()), int(len(v))] for k, v in pd.Series(p).groupby(bins, observed=True)}


def backtest(today):
    rows = training_rows(load_history(today))
    test_start = pd.Timestamp(today - timedelta(days=365 * CALIBRATION_YEARS))
    model = train(rows[rows["date"] < test_start], pd.Timestamp(today - timedelta(days=365 * 2 * CALIBRATION_YEARS)))
    test = rows[rows["date"] >= test_start].reset_index(drop=True)
    print(f"Trained before {test_start - pd.Timedelta(days=365 * CALIBRATION_YEARS):%Y-%m-%d}, calibrated up to {test_start:%Y-%m-%d}, tested on {len(test):,} unseen stock-days after.\n")
    print(f"{'predicted buy':<16}{'avg predicted':>14}{'went up':>10}{'stock-days':>12}")
    for band, (pred, actual, n) in reliability(test, model).items():
        print(f"{band:<16}{pred:>14.1%}{actual:>10.1%}{n:>12,}")
    test["p"] = probability(test, model)
    rank = test.groupby("date")["p"].rank(pct=True)
    up = test["target"] > 0
    print(f"\nTop 10% each day went up {up[rank > 0.9].mean():.1%}, bottom 10% went up {up[rank <= 0.1].mean():.1%}, all {up.mean():.1%}.")
    print(f"Middle 98% of predictions: {np.quantile(test['p'], 0.01):.1%} to {np.quantile(test['p'], 0.99):.1%}.")
    by_year = test.assign(up=up, year=test["date"].dt.year)
    print("Top 10% went up by year: " + ", ".join(f"{y} {g['up'].mean():.1%}" for y, g in by_year[rank > 0.9].groupby("year")))


def load_model(today):
    if MODEL.exists():
        saved = json.loads(MODEL.read_text())
        if (today - date.fromisoformat(saved["asof"])).days < 7:
            return saved
    rows = training_rows(load_history(today))
    calibration_start = pd.Timestamp(today - timedelta(days=365 * CALIBRATION_YEARS))
    model = train(rows, calibration_start)
    model["asof"] = today.isoformat()
    model["calibration_start"] = f"{calibration_start:%Y-%m-%d}"
    MODEL.write_text(json.dumps(model))
    return model


def latest_bars(symbol, now):
    bars = yf.download(symbol, period="2y", interval="1d", progress=False, auto_adjust=True, multi_level_index=False)
    bars = bars.rename(columns=str.lower).dropna(subset=["close"])
    if len(bars) and bars.index[-1].date() == now.date() and now.hour < 16:
        bars = bars.iloc[:-1]
    return bars


def report(symbol, now, model):
    bars = latest_bars(symbol, now)
    if len(bars) < 80:
        print(f"{symbol}: not enough price history (need 80 trading days, got {len(bars)}).\n")
        return
    f = features(bars).iloc[[-1]]
    last = bars.iloc[-1]
    buy = float(probability(f, model)[0])
    full_report(symbol, bars)
    heading("Model odds")
    print(f"  buy  {buy:.1%}  chance it closes higher {HOLD_DAYS} trading days after your fill")
    print(f"  sell {1 - buy:.1%}  chance it closes lower")
    if last["close"] <= MIN_PRICE or last["volume"] < MIN_VOLUME:
        print(f"  Outside what the model was trained on (price over ${MIN_PRICE:.0f}, volume over {MIN_VOLUME:,}), so treat this as a guess.")
    rvol = last["volume"] / bars["volume"].iloc[-21:-1].mean()
    if rvol >= RVOL_THRESHOLD:
        r = np.log(bars["close"]).diff().dropna()
        vol = float(np.sqrt((r**2).ewm(alpha=1 - EWMA_LAMBDA, adjust=False).mean().iat[-1]))
        band = ranges(last["close"], vol, calibrate(now.date()))
        print(f"  90% range, next close {band[1][0]:.2f} - {band[1][1]:.2f}, 5 days out {band[5][0]:.2f} - {band[5][1]:.2f} (volume-flagged at {rvol:.1f}x)")
    print()


def main():
    now = datetime.now(ZoneInfo("America/New_York"))
    if sys.argv[1:] == ["backtest"]:
        return backtest(now.date())
    symbols = [s.upper() for s in sys.argv[1:]]
    if not symbols:
        sys.exit("usage: analyze.py SYMBOL [SYMBOL ...] | analyze.py backtest")
    model = load_model(now.date())
    fill = "today's close" if now.hour < 16 and now.weekday() < 5 else "the next trading day's close"
    print(f"Stock report, {now:%Y-%m-%d %H:%M} ET. Model odds assume an order placed now fills at {fill} and is held {HOLD_DAYS} trading days.\n")
    for symbol in symbols:
        report(symbol, now, model)
    print("Calibrated on unseen data, so a 52% means about 52 in 100 went up. Almost every stock lands between 47% and 54%.")
    print("Anything near 50% is a coin flip. These are odds for sizing and tie-breaking, and they are too weak to trade on by themselves.")


if __name__ == "__main__":
    main()
