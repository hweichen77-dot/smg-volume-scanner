import logging

import numpy as np
import yfinance as yf

FUNDS = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow 30", "IWM": "Russell 2000 small caps"}
HORIZONS = {5: "1 week", 20: "1 month", 60: "3 months", 125: "6 months"}
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


def main():
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
