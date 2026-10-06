# SMG Volume Scanner

Lists stocks trading at least 3x their 20-day average volume. It only keeps names the Stock Market Game (SMG) lets you buy. Price and yesterday's close both have to be over $3 and market cap over $25M. Notes, preferreds, warrants, units and closed-end funds get dropped.

Each flagged stock also gets a 90% price range for the next close and the close 5 trading days out. A separate tool, `analyze.py`, prints a full research report on any stock, with valuation, financials, analysts, peers, holders, filings and news, and ends with calibrated odds of it closing higher or lower a week after you buy.

The stock list, price, market cap and volume come from Nasdaq's public screener. The 20-day averages come from yfinance, cover the 20 sessions before the one being scanned, and get cached in `data/` for the day. The first run takes about two minutes and later runs that day take a few seconds.

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
.venv/bin/python analyze.py APT AAPL     # full report on one or more stocks
.venv/bin/python market.py               # odds for the big index funds
```

For shorter commands that work from any folder, add these to `~/.zshrc` and open a new terminal. Then `run3x`, `run5x`, `analyze APT` and `market` do the same as the lines above.

```
run3x() { (cd ~/smg-volume-scanner && .venv/bin/python volume_scanner.py 3) }
run5x() { (cd ~/smg-volume-scanner && .venv/bin/python volume_scanner.py 5) }
analyze() { (cd ~/smg-volume-scanner && .venv/bin/python analyze.py "$@") }
market() { (cd ~/smg-volume-scanner && .venv/bin/python market.py) }
```

During market hours Nasdaq's screener still shows the previous full session, and the report header names the session it covers. An order you place that day fills at that day's close, one session after the flag, so the "next close" range is the range for your fill.

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

## Stock report

`analyze.py APT` prints one report per symbol, built from Yahoo Finance data, Yahoo's equity screener and Google News. It takes about 5 seconds a stock. The sections are below.

- Company description, sector, industry and website.
- Price and trading, covering market cap, enterprise value, the 52-week range, distance from the 50- and 200-day averages, returns from 1 week to 1 year against the S&P 500, beta, volatility, RVOL and float.
- Valuation, with trailing and forward P/E, PEG, price to sales and book, EV/EBITDA, EV/revenue, free cash flow yield and dividend yield.
- Profitability and balance sheet, with margins, returns on equity and assets, cash, debt, liquidity ratios and growth.
- Up to 5 years of annual financials and the last 5 quarters.
- Analyst ratings, price targets, EPS estimates and rating changes from the last 6 months.
- Earnings, with the next report date and the last 4 results against estimates.
- Ownership, covering insider and institutional stakes, top holders, insider trades from the last 90 days and short interest.
- Five peers from the same Yahoo industry, closest in market cap and listed on an exchange SMG trades, plus their median.
- A list of things to watch, such as earnings inside a 5-day hold, losses, cash burn, heavy shorting, high debt, dilution, an auditor change or no analyst coverage.
- The last 6 SEC filings and the last 30 days of headlines.
- The model's buy and sell odds, and the 90% range when the stock is volume-flagged.

Forward P/E and forward EPS only show when analysts cover the stock, because Yahoo fills them in for uncovered small caps with no stated source. For foreign stocks that report in another currency, like TSM in Taiwan dollars, ratios that would mix that currency with the dollar share price show as n/a.

## Buy and sell odds

`analyze.py` gives any stock a buy and a sell percentage. Buy is the chance the stock closes higher 5 trading days after your order fills, and sell is the chance it closes lower. Run before 4pm ET, it assumes you fill at that day's close. Run after, it assumes the next day's close.

```
.venv/bin/python analyze.py APT PTC AAPL
.venv/bin/python analyze.py backtest   # rerun the calibration test
.venv/bin/python test_analyze.py        # checks for look-ahead and a planted signal
```

The model is a logistic regression on 8 numbers from the stock's own daily bars. They are its return over 1, 5, 20 and 60 days, its 20-day volatility, today's volume against its 20-day average, where it closed inside the day's range, and its distance from the 52-week high. It trains on about 5 million stock-days of the same universe the scanner uses and refits once a week.

The percentages are calibrated. When it says 52%, about 52 in 100 stocks like that went up. Expect small numbers, because the backtest found only a weak edge. Trained before Oct 2022, calibrated through Oct 2024 and tested on 1.37 million stock-days after that, it gave these results.

| predicted buy | average prediction | went up | stock-days |
|---|---|---|---|
| under 48% | 46.9% | 46.4% | 15,086 |
| 48% to 50% | 49.2% | 48.5% | 52,944 |
| 50% to 52% | 51.3% | 49.9% | 281,959 |
| over 52% | 53.0% | 51.5% | 1,017,346 |

98% of predictions fell between 47.9% and 54.5%. The 10% of stocks it liked most each day went up 51.9% of the time and the 10% it liked least went up 49.0%, against 51.0% for everything. The top group came out at 45.5% in late 2024, 53.7% in 2025 and 51.5% in 2026, so the edge isn't steady from year to year. Predictions run 1 to 1.5 points high in the test, mostly because the overall share of stocks going up moves with the market and no single-stock number can see that coming. A reading near 50% means the model has nothing. Use the odds to break ties between picks and to size positions, and keep them out of the decision to trade at all.

## Index fund odds

Single stocks are close to a coin flip over a week, but the whole market drifts up over time. `market.py` shows how often SPY, QQQ, DIA and IWM ended higher over every past 1-week, 1-month, 3-month and 6-month window since each fund started, with dividends included. It also prints the median move and the range that held 90% of outcomes.

| SPY since 1993 | ended higher | 90% of outcomes |
|---|---|---|
| 1 week | 58.7% | -3.6% to +3.7% |
| 1 month | 65.4% | -6.5% to +6.9% |
| 3 months | 72.1% | -10.2% to +12.6% |
| 6 months | 75.9% | -12.0% to +21.2% |

Each row also splits history into an older and a recent half, so you can see how steady a number is. SPY's 3-month odds were 67% before 2009 and 77% after. These rates come from 30 strong years for US stocks and aren't a guarantee. A fund that ends a window higher can still drop a long way in the middle of it. A filter that only buys when the fund trades above its 200-day average was tested and left out, because it raised the odds for SPY and QQQ but lowered them for IWM.
