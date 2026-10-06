import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import cache

import numpy as np
import pandas as pd
import yfinance as yf

from volume_scanner import NOT_COMMON, listed_stocks

WIDTH = 100
PEERS = 5
NEWS_ITEMS = 6
NAME_SUFFIXES = (" inc.", " inc", " corp.", " corporation", " ltd.", " ltd", " plc", " co.", " holdings", ", inc.", " n.v.", " s.a.", " limited")


def safe(fn, default=None):
    try:
        value = fn()
        return default if value is None else value
    except Exception:
        return default


def missing(x):
    return x is None or (isinstance(x, float) and not np.isfinite(x))


def money(x):
    if missing(x):
        return "n/a"
    sign = "-" if x < 0 else ""
    for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= size:
            return f"{sign}${abs(x) / size:,.2f}{unit}"
    return f"{sign}${abs(x):,.2f}"


def pct(x, signed=False):
    return "n/a" if missing(x) else f"{x:+.1%}" if signed else f"{x:.1%}"


def ratio(x):
    return "n/a" if missing(x) or x <= 0 else f"{x:.2f}x" if x < 1 else f"{x:.1f}x"


def price(x):
    return "n/a" if missing(x) else f"${x:,.2f}"


def heading(title):
    print(f"\n{title}\n{'-' * len(title)}")


def grid(pairs, columns=3):
    width = WIDTH // columns
    for i in range(0, len(pairs), columns):
        print("".join(f"{label:<18}{value:<{width - 18}}" for label, value in pairs[i:i + columns]).rstrip())


def table(frame):
    print(frame.to_string(na_rep="n/a"))


def statement_row(frame, *names):
    for name in names:
        if frame is not None and name in frame.index:
            return frame.loc[name]
    return None


@cache
def universe():
    return listed_stocks()


def company_name(info):
    name = (info.get("longName") or info.get("shortName") or "").lower()
    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip(" ,")


def headlines(info, symbol):
    query = f'"{company_name(info)}" when:30d'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
    items = [(pd.Timestamp(i.findtext("pubDate")), i.findtext("title")) for i in ET.fromstring(raw).findall(".//item")]
    first_word = company_name(info).split()[0]
    items = [(when, title) for when, title in items if first_word in title.lower() or f"({symbol})" in title or f" {symbol} " in f" {title} "]
    return sorted(items, reverse=True)[:NEWS_ITEMS]


def screen_peers(field, value, cap):
    query = yf.EquityQuery("and", [yf.EquityQuery("eq", [field, value]), yf.EquityQuery("eq", ["region", "us"]), yf.EquityQuery("btwn", ["intradaymarketcap", cap / 20, cap * 20])])
    return [(q["symbol"], q.get("marketCap") or 0) for q in yf.screen(query, size=250)["quotes"]]


def peer_rows(symbol, info):
    cap = info.get("marketCap")
    if not cap:
        return None
    listed = universe()
    listed = set(listed[~listed["name"].str.upper().apply(lambda n: any(tag in n for tag in NOT_COMMON))]["symbol"])
    for field, label in (("industry", info.get("industry")), ("sector", info.get("sector"))):
        found = [(s, c) for s, c in safe(lambda: screen_peers(field, label, cap), []) if s in listed and s != symbol and c > 0]
        if len(found) >= 3:
            break
    picks = [s for s, c in sorted(found, key=lambda sc: abs(np.log(sc[1] / cap)))[:PEERS]]
    if not picks:
        return None
    with ThreadPoolExecutor(PEERS) as pool:
        infos = dict(zip(picks, pool.map(lambda s: safe(lambda: yf.Ticker(s).info, {}), picks)))
    rows = {symbol: info, **infos}
    return pd.DataFrame({
        s: {
            "Mkt cap": i.get("marketCap"),
            "P/E": i.get("trailingPE"),
            "P/S": i.get("priceToSalesTrailing12Months") if i.get("financialCurrency", "USD") == i.get("currency", "USD") else None,
            "EV/EBITDA": i.get("enterpriseToEbitda") if i.get("financialCurrency", "USD") == i.get("currency", "USD") else None,
            "Net margin": i.get("profitMargins"),
            "Rev growth": i.get("revenueGrowth"),
            "1Y chg": i.get("52WeekChange"),
        }
        for s, i in rows.items()
    }).T, label


def red_flags(info, t, annual_bs, filings, next_earnings):
    flags = []
    if next_earnings and 0 <= (next_earnings - date.today()).days <= 7:
        flags.append(f"Earnings on {next_earnings:%a %b %d}, inside a 5-day hold. Moves around earnings run larger than the ranges assume.")
    if (info.get("netIncomeToCommon") or 0) < 0:
        flags.append(f"Losing money, {money(info['netIncomeToCommon'])} net income over the last 12 months.")
    if (info.get("freeCashflow") or 0) < 0:
        flags.append(f"Burning cash, {money(info['freeCashflow'])} free cash flow. Watch for share sales to raise money.")
    if (info.get("shortPercentOfFloat") or 0) > 0.15:
        flags.append(f"{pct(info['shortPercentOfFloat'])} of the float is sold short.")
    if (info.get("debtToEquity") or 0) > 200:
        flags.append(f"Debt is {info['debtToEquity'] / 100:.1f}x equity.")
    if (info.get("currentRatio") or 9) < 1:
        flags.append(f"Current ratio {info['currentRatio']:.2f}, so short-term bills exceed short-term assets.")
    shares = statement_row(annual_bs, "Ordinary Shares Number", "Share Issued")
    if shares is not None and len(shares.dropna()) >= 2 and shares.dropna().iat[0] > 1.1 * shares.dropna().iat[1]:
        flags.append(f"Share count up {shares.dropna().iat[0] / shares.dropna().iat[1] - 1:.0%} in the last fiscal year (dilution).")
    for f in filings:
        if (date.today() - f["date"]).days <= 180 and any(k.startswith("EX-16") for k in (f.get("exhibits") or {})):
            flags.append(f"Changed auditors, per an 8-K filed {f['date']:%b %d, %Y}.")
            break
    if not info.get("numberOfAnalystOpinions"):
        flags.append("No Wall Street analyst coverage.")
    return flags


def full_report(symbol, bars):
    t = yf.Ticker(symbol)
    info = safe(lambda: t.info, {})
    if not info.get("longName") and not info.get("shortName"):
        print(f"{symbol}: Yahoo has no company data for this symbol.")
        return
    close = bars["close"]
    last = close.iat[-1]
    calendar = safe(lambda: t.calendar, {})
    next_earnings = next(iter(calendar.get("Earnings Date") or []), None)
    annual = safe(lambda: t.income_stmt)
    quarterly = safe(lambda: t.quarterly_income_stmt)
    annual_bs = safe(lambda: t.balance_sheet)
    annual_cf = safe(lambda: t.cashflow)
    filings = safe(lambda: t.sec_filings, [])

    title = f"{info.get('longName') or info.get('shortName')} ({symbol})  {info.get('fullExchangeName', '')}"
    print("=" * WIDTH + f"\n{title}\n{info.get('sector', 'n/a')} / {info.get('industry', 'n/a')}   {info.get('city', '')}, {info.get('country', '')}   {info.get('website', '')}\n" + "=" * WIDTH)

    summary = info.get("longBusinessSummary")
    if summary:
        sentences, text = summary.split(". "), ""
        for sentence in sentences:
            if text and len(text) + len(sentence) > 450:
                break
            text += sentence.rstrip(".") + ". "
        print(textwrap.fill(text.strip(), WIDTH))

    heading("Price and trading")
    r = np.log(close).diff()
    ytd = close[close.index.year == close.index[-1].year]
    back = lambda n: close.iat[-1] / close.iat[-n - 1] - 1 if len(close) > n else None
    hi, lo = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow")
    grid([
        ("Last close", f"{price(last)} {bars.index[-1]:%b %d}"),
        ("Market cap", money(info.get("marketCap"))),
        ("Enterprise value", money(info.get("enterpriseValue"))),
        ("52-wk range", f"{price(lo)} - {price(hi)}"),
        ("In 52-wk range", pct((last - lo) / (hi - lo)) if hi and lo and hi > lo else "n/a"),
        ("Off 52-wk high", pct(info.get("fiftyTwoWeekHighChangePercent"), True)),
        ("vs 50-day avg", pct(last / close.tail(50).mean() - 1, True)),
        ("vs 200-day avg", pct(last / close.tail(200).mean() - 1, True)),
        ("Beta", "n/a" if missing(info.get("beta")) else f"{info['beta']:.2f}"),
        ("1 week", pct(back(5), True)), ("1 month", pct(back(21), True)), ("3 months", pct(back(63), True)),
        ("6 months", pct(back(126), True)), ("Year to date", pct(last / ytd.iat[0] - 1 if len(ytd) else None, True)), ("1 year", pct(back(252), True)),
        ("S&P 500 1 year", pct(info.get("SandP52WeekChange"), True)),
        ("Volatility (ann.)", pct(r.tail(20).std() * np.sqrt(252))),
        ("Avg volume 3M", f"{info.get('averageVolume') or 0:,.0f}"),
        ("Last-day RVOL", f"{bars['volume'].iat[-1] / bars['volume'].iloc[-21:-1].mean():.1f}x"),
        ("Float", f"{info.get('floatShares') or 0:,.0f}"),
        ("Shares out", f"{info.get('sharesOutstanding') or 0:,.0f}"),
    ])

    covered = bool(info.get("numberOfAnalystOpinions"))
    same_currency = info.get("financialCurrency", "USD") == info.get("currency", "USD")
    heading("Valuation")
    fcf, cap = info.get("freeCashflow"), info.get("marketCap")
    grid([
        ("P/E (trailing)", ratio(info.get("trailingPE"))),
        ("P/E (forward)", ratio(info.get("forwardPE")) if covered else "n/a"),
        ("PEG", ratio(info.get("trailingPegRatio"))),
        ("Price/sales", ratio(info.get("priceToSalesTrailing12Months")) if same_currency else "n/a"),
        ("Price/book", ratio(info.get("priceToBook")) if same_currency else "n/a"),
        ("EV/EBITDA", ratio(info.get("enterpriseToEbitda")) if same_currency else "n/a"),
        ("EV/revenue", ratio(info.get("enterpriseToRevenue")) if same_currency else "n/a"),
        ("FCF yield", pct(fcf / cap) if fcf and cap and same_currency else "n/a"),
        ("Dividend yield", "n/a" if missing(info.get("dividendYield")) else pct(info["dividendYield"] / 100)),
        ("EPS (ttm)", price(info.get("trailingEps"))),
        ("EPS (forward)", price(info.get("forwardEps")) if covered else "n/a"),
        ("Payout ratio", pct(info.get("payoutRatio"))),
    ])

    heading("Profitability and balance sheet")
    if not same_currency:
        print(f"Financial statements are in {info.get('financialCurrency')}, so the $ amounts below and in the tables are {info.get('financialCurrency')}. Ratios that mix them with the USD share price are left out.\n")
    cash, debt = info.get("totalCash"), info.get("totalDebt")
    grid([
        ("Gross margin", pct(info.get("grossMargins"))),
        ("Operating margin", pct(info.get("operatingMargins"))),
        ("Net margin", pct(info.get("profitMargins"))),
        ("EBITDA margin", pct(info.get("ebitdaMargins"))),
        ("Return on equity", pct(info.get("returnOnEquity"))),
        ("Return on assets", pct(info.get("returnOnAssets"))),
        ("Revenue (ttm)", money(info.get("totalRevenue"))),
        ("Net income (ttm)", money(info.get("netIncomeToCommon"))),
        ("Free cash flow", money(fcf)),
        ("Cash", money(cash)),
        ("Debt", money(debt)),
        ("Net cash", money((cash or 0) - (debt or 0)) if cash is not None or debt is not None else "n/a"),
        ("Debt/equity", "n/a" if missing(info.get("debtToEquity")) else f"{info['debtToEquity'] / 100:.2f}x"),
        ("Current ratio", "n/a" if missing(info.get("currentRatio")) else f"{info['currentRatio']:.2f}"),
        ("Quick ratio", "n/a" if missing(info.get("quickRatio")) else f"{info['quickRatio']:.2f}"),
        ("Revenue growth", pct(info.get("revenueGrowth"), True)),
        ("Earnings growth", pct(info.get("earningsGrowth"), True)),
        ("Book value/share", price(info.get("bookValue"))),
    ])

    if annual is not None and len(annual.columns):
        heading("Annual financials")
        cols = sorted(annual.columns)[-5:]
        revenue = statement_row(annual, "Total Revenue")
        rows = {
            "Revenue": revenue,
            "Gross profit": statement_row(annual, "Gross Profit"),
            "Operating income": statement_row(annual, "Operating Income"),
            "Net income": statement_row(annual, "Net Income", "Net Income Common Stockholders"),
            "Free cash flow": statement_row(annual_cf, "Free Cash Flow"),
        }
        frame = pd.DataFrame({k: v.reindex(cols) for k, v in rows.items() if v is not None}).T.dropna(axis=1, how="all")
        cols = list(frame.columns)
        out = frame.map(money)
        if revenue is not None:
            out.loc["Revenue growth"] = [pct(x, True) for x in revenue.reindex(cols).astype(float).pct_change(fill_method=None)]
        eps = statement_row(annual, "Diluted EPS")
        if eps is not None:
            out.loc["Diluted EPS"] = [price(x) for x in eps.reindex(cols)]
        out.columns = [f"FY{c:%Y}" for c in cols]
        table(out)

    if quarterly is not None and len(quarterly.columns):
        heading("Last 5 quarters")
        cols = sorted(quarterly.columns)[-5:]
        rows = {"Revenue": statement_row(quarterly, "Total Revenue"), "Net income": statement_row(quarterly, "Net Income", "Net Income Common Stockholders")}
        out = pd.DataFrame({k: v.reindex(cols) for k, v in rows.items() if v is not None}).T.map(money)
        out.columns = [f"{c:%b %Y}" for c in cols]
        table(out)

    heading("Analysts")
    targets = safe(lambda: t.analyst_price_targets, {})
    recs = safe(lambda: t.recommendations)
    if covered:
        counts = recs.iloc[0] if recs is not None and len(recs) else {}
        mean = targets.get("mean")
        grid([
            ("Analysts", str(info.get("numberOfAnalystOpinions"))),
            ("Consensus", str(info.get("recommendationKey", "n/a")).replace("_", " ")),
            ("Ratings", ", ".join(f"{counts.get(k, 0)} {label}" for k, label in (("strongBuy", "SB"), ("buy", "B"), ("hold", "H"), ("sell", "S"), ("strongSell", "SS")))),
            ("Mean target", f"{price(mean)} ({pct(mean / last - 1, True)})" if mean else "n/a"),
            ("Target range", f"{price(targets.get('low'))} - {price(targets.get('high'))}"),
        ], columns=2)
        estimates = safe(lambda: t.earnings_estimate)
        if estimates is not None and "avg" in estimates:
            print()
            table(estimates[["avg", "low", "high", "yearAgoEps", "numberOfAnalysts", "growth"]].rename(index={"0q": "This quarter", "+1q": "Next quarter", "0y": "This year", "+1y": "Next year"}).round(3))
        changes = safe(lambda: t.upgrades_downgrades)
        if changes is not None and len(changes):
            recent = changes[changes.index >= pd.Timestamp.now() - pd.Timedelta(days=180)].head(5)
            if len(recent):
                print("\nRecent rating changes")
                for when, row in recent.iterrows():
                    target = f", target {price(row['currentPriceTarget'])}" if row.get("currentPriceTarget") else ""
                    print(f"  {when:%b %d}  {row['Firm']:<24} {row['FromGrade'] or '-'} -> {row['ToGrade']}{target}")
    else:
        print("No analysts cover this stock.")

    history = safe(lambda: t.earnings_dates)
    heading("Earnings")
    print(f"Next report: {next_earnings:%a %b %d, %Y}" if next_earnings else "Next report: not announced")
    if history is not None and len(history):
        done = history.dropna(subset=["Reported EPS"])
        done = done[done.index >= pd.Timestamp.now(tz=done.index.tz) - pd.Timedelta(days=730)].head(4)
        if not len(done):
            print("No reported EPS against estimates in the last 2 years.")
        else:
            table(done.rename_axis("Reported").set_index(done.index.strftime("%b %d, %Y")).round(2))

    heading("Ownership and short interest")
    short_now, short_before = info.get("sharesShort"), info.get("sharesShortPriorMonth")
    grid([
        ("Insiders", pct(info.get("heldPercentInsiders"))),
        ("Institutions", pct(info.get("heldPercentInstitutions"))),
        ("Short % float", pct(info.get("shortPercentOfFloat"))),
        ("Days to cover", "n/a" if missing(info.get("shortRatio")) else f"{info['shortRatio']:.1f}"),
        ("Shares short", f"{short_now or 0:,.0f}"),
        ("Short vs last mo", pct(short_now / short_before - 1, True) if short_now and short_before else "n/a"),
    ])
    holders = safe(lambda: t.institutional_holders)
    if holders is not None and len(holders):
        print("\nTop holders")
        for _, h in holders.head(5).iterrows():
            print(f"  {h['Holder'][:40]:<42}{pct(h.get('pctHeld')):>8} held  {pct(h.get('pctChange'), True):>8} last qtr")
    insiders = safe(lambda: t.insider_transactions)
    if insiders is not None and len(insiders):
        recent = insiders[pd.to_datetime(insiders["Start Date"]) >= pd.Timestamp.now() - pd.Timedelta(days=90)]
        if len(recent):
            print("\nInsider activity, last 90 days")
            for _, row in recent.head(6).iterrows():
                what = (row.get("Text") or row.get("Transaction") or "").split(" at price")[0]
                print(f"  {pd.Timestamp(row['Start Date']):%b %d}  {str(row['Insider']).title()[:24]:<26}{what[:44]:<46}{money(row.get('Value')) if row.get('Value') else ''}")

    found = safe(lambda: peer_rows(symbol, info))
    if found is not None:
        frame, industry = found
        heading(f"Peers, {industry}")
        numeric = frame.astype(float)
        peers_only = numeric.drop(index=symbol)
        median = peers_only.median()
        for col in ("P/E", "P/S", "EV/EBITDA"):
            median[col] = peers_only[col][peers_only[col] > 0].median()
        out = numeric.copy()
        out.loc["Peer median"] = median
        shown = pd.DataFrame(index=out.index)
        shown["Mkt cap"] = out["Mkt cap"].map(money)
        for col in ("P/E", "P/S", "EV/EBITDA"):
            shown[col] = out[col].map(ratio)
        for col in ("Net margin", "Rev growth", "1Y chg"):
            shown[col] = out[col].map(lambda x: pct(x, True))
        table(shown)

    flags = red_flags(info, t, annual_bs, filings, next_earnings)
    if flags:
        heading("Watch out for")
        for flag in flags:
            print(textwrap.fill(flag, WIDTH, initial_indent="  - ", subsequent_indent="    "))

    if filings:
        heading("Recent SEC filings")
        for f in filings[:6]:
            print(f"  {f['date']:%b %d, %Y}  {f['type']:<8}{f.get('title', '')}")

    news = safe(lambda: headlines(info, symbol), [])
    if news:
        heading("News, last 30 days")
        for when, title in news:
            print(textwrap.fill(title, WIDTH, initial_indent=f"  {when:%b %d}  ", subsequent_indent=" " * 10))
