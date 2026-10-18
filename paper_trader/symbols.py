"""Nifty 50 members verified from published constituent lists in Oct 2026.

This list is version-controlled. It can become stale after an index review;
update it from the official NSE Indices constituent CSV. Yahoo Finance tickers
are used for personal research/paper simulation, not exchange-grade execution.
"""

NIFTY50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BSE", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INFY", "ITC", "INDIGO", "JSWSTEEL",
    "JIOFIN", "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH",
    "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TCS", "TATACONSUM", "TMPV",
    "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
]

NIFTY50_SYMBOLS = list(dict.fromkeys(NIFTY50_SYMBOLS))
YAHOO_TICKERS = {symbol: f"{symbol}.NS" for symbol in NIFTY50_SYMBOLS}
BENCHMARK_TICKER = "^NSEI"
