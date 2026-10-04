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

## Bart pattern scanner

`bart.py` looks for the Bart Simpson pattern on daily bars. A stock jumps at least 25% in one day on 3x its 20-day average volume, then goes sideways for 2 to 10 days in a band no wider than 15%, holding at least half the jump. When the close breaks out of the band in the direction it came from, that's the signal. The bearish version (spike up, flat top, break down) is a short. The inverse (crash, flat bottom, break up) is a long. Target is the close before the spike. Stop is the far side of the band.

```
.venv/bin/python bart.py            # today's signals and setups still forming
.venv/bin/python bart.py backtest   # replay the last 2 years
.venv/bin/python test_bart.py       # checks the detector on made-up price paths
```

The first run downloads 2 years of bars for every stock that passes the SMG rules (about 90 seconds) and caches them in `data/` for the day.

SMG End-of-Day games fill every order at the day's close, and anything entered after 4pm ET fills at the next trading day's close. You can't see a breakout close and still trade at it, so the backtest runs two ways. Lag 0 means you run the scan just before 4pm and the late price holds into the close. Lag 1 means you run it after the close and fill a day later. Target and stop exits get the same delay. Results from Oct 2024 to Oct 2026, exiting at target, stop, or 10 days:

| fill  | side  | entries     | trades | win | mean  | median |
|-------|-------|-------------|--------|-----|-------|--------|
| lag 0 | short | Bart        | 334    | 51% | -2.3% | +1.0%  |
| lag 0 | long  | Bart        | 266    | 44% | +0.2% | -1.4%  |
| lag 1 | short | Bart        | 333    | 52% | -1.3% | +0.9%  |
| lag 1 | long  | Bart        | 271    | 47% | +1.2% | -0.5%  |
| lag 1 | short | every spike | 1300   | 55% | -1.4% | +1.7%  |
| lag 1 | long  | every spike | 988    | 47% | +1.2% | -0.7%  |

The pattern doesn't make money on its own. Spiked stocks usually drift back, so most shorts win a little, but the few that squeeze lose big enough to wipe that out. Crashed stocks are the mirror image. A grid of 81 settings fit on 2024-25 had near-zero correlation with how the same settings did in 2026, so tuning the numbers is curve fitting. Treat the output as a watchlist with levels, not a system. The universe is today's listed stocks, so names that delisted after crashing are missing, which flatters the longs and hurts the shorts.

Shorting in SMG needs a margin account, and the $3 rule applies to the day before and the day of the trade.
