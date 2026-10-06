import logging
import sys

import numpy as np
import yfinance as yf

FUNDS = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow 30", "IWM": "Russell 2000 small caps"}
HORIZONS = {5: "1 week", 20: "1 month", 60: "3 months", 125: "6 months"}
SECTOR_FUNDS = {
    "Technology": "XLK", "Healthcare": "XLV", "Financial Services": "XLF", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Energy": "XLE", "Industrials": "XLI", "Basic Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU", "Communication Services": "XLC",
}
OTHER_MARKETS = {"IWM": "Small caps", "TLT": "Long Treasury bonds", "UUP": "US dollar", "USO": "Oil", "GLD": "Gold", "BTC-USD": "Bitcoin", "^VIX": "VIX (fear gauge)"}
PERIODS = {5: "1 week", 21: "1 month", 63: "3 months", 126: "6 months", 252: "1 year"}
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def odds(close, h):
    moves = (close.shift(-h) / close - 1).dropna()
    middle = moves.index[len(moves) // 2]
    older, recent = moves[moves.index < middle], moves[moves.index >= middle]
    return {
        "up": (moves > 0).mean(),
        "older": (older > 0).mean(),
        "recent": (recent > 0).mean(),
        "split": middle,
        "median": moves.median(),
        "low": moves.quantile(0.05),
        "high": moves.quantile(0.95),
    }


def beta(y, x):
    return float(np.cov(y, x)[0, 1] / np.var(x, ddof=1))


def residual(y, x):
    return y - beta(y, x) * x


def stock_in_market(symbol):
    info = yf.Ticker(symbol).info
    sector = info.get("sector")
    fund = SECTOR_FUNDS.get(sector)
    names = {symbol: symbol, "SPY": "S&P 500", **({fund: f"{sector} sector"} if fund else {}), **OTHER_MARKETS}
    close = yf.download(list(names), period="5y", interval="1d", progress=False, auto_adjust=True)["Close"]
    close = close[close[symbol].notna()].ffill()
    if len(close) < 260:
        print(f"{symbol}: needs a year of price history, has {len(close)} days.\n")
        return
    returns = np.log(close).diff().iloc[1:]

    print(f"{info.get('longName', symbol)} ({symbol})   {sector or 'sector unknown'} / {info.get('industry', 'n/a')}   ${close[symbol].iat[-1]:,.2f}")
    heading = f"  {'':<22}" + "".join(f"{name:>10}" for name in PERIODS.values())
    print("\nPerformance against its market\n" + heading)
    rows = [symbol, "SPY"] + ([fund] if fund else [])
    for col in rows:
        print(f"  {names[col]:<22}" + "".join(f"{close[col].iat[-1] / close[col].iat[-1 - n] - 1:>+10.1%}" for n in PERIODS))
    for col in rows[1:]:
        print(f"  {'vs ' + names[col]:<22}" + "".join(f"{(close[symbol].iat[-1] / close[symbol].iat[-1 - n]) - (close[col].iat[-1] / close[col].iat[-1 - n]):>+10.1%}" for n in PERIODS))

    last_year = returns.tail(252)
    y, m = last_year[symbol], last_year["SPY"]
    explained = np.corrcoef(y, m)[0, 1] ** 2
    if fund:
        X = np.column_stack([np.ones(len(y)), m, residual(last_year[fund], m)])
        fit = np.linalg.lstsq(X, y, rcond=None)[0]
        explained = 1 - np.var(y - X @ fit) / np.var(y)
    print(f"\nOver the last year the S&P 500{' and its sector' if fund else ''} explain {explained:.0%} of its daily moves. The other {1 - explained:.0%} is company-specific.")
    print(f"Beta to the S&P 500 is {beta(y, m):.2f}, so a 1% S&P day has meant about a {beta(y, m):.1f}% move in {symbol}.")

    print("\nHow other markets move with it")
    print(f"  {'market':<22}{'link 1y':>9}{'link 3y':>9}{'beyond S&P 1y':>15}{'beyond S&P 3y':>15}{'its last month':>16}")
    for col in [c for c in names if c not in (symbol, "SPY")] + ["SPY"]:
        if close[col].isna().all():
            continue
        cells = []
        for days in (252, 756):
            window = returns.tail(days)
            cells.append(np.corrcoef(window[symbol], window[col])[0, 1])
        for days in (252, 756):
            window = returns.tail(days)
            if col == "SPY":
                cells.append(np.nan)
            else:
                cells.append(np.corrcoef(residual(window[symbol], window["SPY"]), residual(window[col], window["SPY"]))[0, 1])
        month = close[col].iat[-1] / close[col].iat[-22] - 1
        print(f"  {names[col]:<22}" + f"{cells[0]:>+9.2f}{cells[1]:>+9.2f}" + "".join(f"{'':>15}" if np.isnan(c) else f"{c:>+15.2f}" for c in cells[2:]) + f"{month:>+16.1%}")
    print("\nLinks run from -1 (moves opposite) through 0 (unrelated) to +1 (moves together). Beyond S&P strips out the shared market move, so it shows")
    print("whether that market matters to this stock on its own. Under about 0.10 either way is noise. A link that flips sign between 1y and 3y isn't reliable.\n")


def main():
    if sys.argv[1:]:
        for symbol in sys.argv[1:]:
            stock_in_market(symbol.upper())
        return
    print("Index fund odds from every past window since each fund started. Dividends included.\n")
    for symbol, label in FUNDS.items():
        close = yf.download(symbol, period="max", interval="1d", progress=False, auto_adjust=True, multi_level_index=False)["Close"].dropna()
        last = close.iat[-1]
        print(f"{symbol}  {label}   ${last:,.2f} on {close.index[-1]:%b %d}   1 month {last / close.iat[-22] - 1:+.1%}   {last / close.max() - 1:+.1%} from its high   data since {close.index[0]:%Y}")
        print(f"  {'hold':<10}{'ended higher':>13}{'older half':>12}{'recent half':>13}{'median':>9}{'90% of outcomes':>22}")
        for h, name in HORIZONS.items():
            o = odds(close, h)
            print(f"  {name:<10}{o['up']:>13.1%}{o['older']:>12.0%}{o['recent']:>13.0%}{o['median']:>+9.1%}{o['low']:>+11.1%} to {o['high']:+.1%}")
        print(f"  older half ends {o['split']:%Y}\n")
    print("These are history, not a forecast. US stocks had a strong 30 years, and a fund that ended higher still could have dropped a lot in between.")
    print("The 200-day trend filter was tested and left out because it helped SPY and QQQ and hurt IWM, so it isn't reliable.")


if __name__ == "__main__":
    main()
