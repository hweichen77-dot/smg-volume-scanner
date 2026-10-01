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
