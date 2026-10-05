# SMG Volume Scanner

Lists stocks trading at least 3x their 20-day average volume. It only keeps names the Stock Market Game (SMG) lets you buy. Price and yesterday's close both have to be over $3 and market cap over $25M. Notes, preferreds, warrants, units and closed-end funds get dropped.

The stock list, price, market cap and today's volume come from Nasdaq's public screener. The 20-day averages come from yfinance and get cached in `data/` for the day. The first run takes about two minutes and later runs that day take a few seconds.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python volume_scanner.py      # 3x threshold
.venv/bin/python volume_scanner.py 5    # 5x threshold
```

While the market is open, today's volume only covers part of the day, so RVOL reads low. The numbers are most useful near the close.

SMG doesn't allow automated order entry, so this only finds stocks. Trades go in by hand.

## Price ranges

Each flagged stock gets two ranges, one for the next close and one for the close 5 trading days out. In a backtest of 2 years of past flags, the actual close landed inside the range about 86% of the time. The target is 85%.

| range | flags scored | inside | median width |
|---|---|---|---|
| next close | 22,392 | 86.2% | 12% of price |
| 5 days out | 22,105 | 85.9% | 24% of price |

Misses split about evenly above and below the range. Coverage holds across calm stocks (under 3% daily moves), middle ones (3% to 6%) and wild ones (over 6%), all at 86%, because each group is sized separately.

A range is the flag-day close plus or minus a multiple of the stock's recent volatility. The multiple comes from how far past flagged stocks actually moved, and it's recalibrated walk-forward with adaptive conformal inference. Each new flag only uses outcomes that were known by its own date, and the multiple widens after misses and tightens after hits. The scanner recalibrates once a week, which downloads 2 years of bars (about 90 seconds).

The ranges don't predict direction. Up-or-down calls on flagged stocks came out at 49% to 50% in the same backtest. If a stock moved more than 50% on the day, its range gets a `*`. Splits and spin-offs show up in the free data as fake crashes, which make the range far too wide.

### Holdout test

The last 200 trading days (Dec 16, 2025 to Oct 2, 2026) were held out as unseen data. "Frozen" calibrates only on flags that resolved before the window and never updates. "Live" keeps recalibrating the way the scanner does.

| range | test flags | frozen | live |
|---|---|---|---|
| next close | 10,685 | 85.2% | 86.4% |
| 5 days out | 10,526 | 83.8% | 85.9% |

Frozen ranges slipped under target from January to May 2026, when markets got rougher than in the calibration data, and the 5-day range ended at 83.8%. Recalibrating keeps both above 85%, so leave the weekly refresh on. On a few days many flagged stocks moved together on market news. The next-close range fell under 70% on 7 of 199 days, with the worst at 64% on Jul 10, 2026. Direction stayed a coin flip, with 50.9% up after 1 day and 48.8% after 5.

```
.venv/bin/python price_range.py           # rerun the range backtest
.venv/bin/python price_range.py holdout   # 200-day holdout test
.venv/bin/python test_price_range.py      # checks calibration on made-up data
```
