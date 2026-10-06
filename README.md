# SMG Volume Scanner

Lists stocks trading at least 3x their 20-day average volume. It only keeps names the Stock Market Game (SMG) lets you buy. Price and yesterday's close both have to be over $3 and market cap over $25M. Notes, preferreds, warrants, units and closed-end funds get dropped.

The stock list, price, market cap and today's volume come from Nasdaq's public screener. The 20-day averages come from yfinance and get cached in `data/` for the day. The first run takes about two minutes and later runs that day take a few seconds.

## Setup

Needs Python 3.11 to 3.14. Python 3.15 is still a pre-release and pyarrow has no wheels for it yet, so the install tries to build pyarrow from source and fails. The repo pins 3.13 in `.python-version`.

With [uv](https://docs.astral.sh/uv/), which picks up the pin and downloads 3.13 if you don't have it:

```
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
```

Without uv, call a specific interpreter instead of plain `python3`:

```
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Usage

```
.venv/bin/python volume_scanner.py      # 3x threshold
.venv/bin/python volume_scanner.py 5    # 5x threshold
```

While the market is open, today's volume only covers part of the day, so RVOL reads low. The numbers are most useful near the close.

SMG doesn't allow automated order entry, so this only finds stocks. Trades go in by hand.

## Price ranges

Each flagged stock gets two ranges, one for the next close and one for the close 5 trading days out. They're sized so the actual close lands inside 90% of the time. A walk-forward backtest over 10 years of past flags (Oct 2016 to Oct 2026) hit that.

| range | flags scored | inside | median width |
|---|---|---|---|
| next close | 111,515 | 90.9% | 13% of price |
| 5 days out | 111,277 | 90.6% | 25% of price |

Misses split about evenly above and below the range. Calm stocks (under 3% daily moves), middle ones (3% to 6%) and wild ones (over 6%) all land between 90% and 91%, because each group is sized separately. Coverage held in every year from 2017 to 2026, the 2020 crash and the 2022 bear market included.

The worst quarter was 2020 Q3, when the next-close range caught 83%. Most of that came from Sep 18, 2020, a quad witching and S&P rebalance day. 525 calm stocks got flagged on index-fund volume and fell together the next session. Flags from the third Friday of March, June, September and December are often mechanical, so give them less weight.

A range is the flag-day close plus or minus a multiple of the stock's recent volatility. The multiple comes from how far past flagged stocks actually moved, and it's recalibrated walk-forward with adaptive conformal inference. Each new flag only uses outcomes that were known by its own date, and the multiple widens after misses and tightens after hits. The scanner recalibrates once a week, which downloads 10 years of bars (about 2 minutes).

The ranges don't predict direction. Up-or-down calls on flagged stocks came out between 47% and 51%. The closest thing to a signal is stocks that closed up more than 10% on the flag day after rising more than 20% over the prior 20 days. Across 8,484 of them, only 47% were higher 5 days later. That lean is too weak to trade on by itself, and the range misses for that group still split evenly.

Some limits matter if you trade on this.

- The 90% is per stock. Misses cluster on market-wide days, so several flagged stocks you hold can break their ranges on the same day.
- The history only covers stocks listed today. Companies that crashed and got delisted are missing, so real downside misses probably run a little higher than the backtest shows.
- Splits and spin-offs show up in the free data as fake crashes, which make the range far too wide. Any stock with a one-day move over 50% in the last 6 months gets a `*`, so check those by hand.

### Holdout test

The last 200 trading days (Dec 17, 2025 to Oct 5, 2026) were held out as unseen data. "Frozen" calibrates only on the 102,000 flags that resolved before the window and never updates. "Live" keeps recalibrating the way the scanner does.

| range | test flags | frozen | live |
|---|---|---|---|
| next close | 10,711 | 89.9% | 91.3% |
| 5 days out | 10,564 | 89.8% | 91.0% |

Frozen ranges finished a hair under 90% and live ones stayed above it, so leave the weekly refresh on. The next-close range fell under 70% on 1 of 199 days, with the worst at 68% on Aug 25, 2026. Direction stayed a coin flip, with 51.3% up after 1 day and 48.9% after 5.

```
.venv/bin/python price_range.py           # rerun the range backtest
.venv/bin/python price_range.py holdout   # 200-day holdout test
.venv/bin/python test_price_range.py      # checks calibration on made-up data
```
