"""
Fetches recent news relevant to whatever we're watching.

  - Crypto coins  -> CryptoPanic (free tier, coin-filtered) + general crypto RSS fallback
  - Forex/commodities -> general market/macro RSS (there's no free coin-style filtered
    API for these at retail level, so the AI reads broad financial news for these,
    the same way a human trader would scan a newsfeed)
"""

import requests
import feedparser
from datetime import datetime, timezone

import config

# General crypto market news — no API key required.
CRYPTO_RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]

# General macro/markets news — relevant to gold, silver, oil, and forex moves.
MACRO_RSS_FEEDS = [
    "https://oilprice.com/rss/main",
    "https://www.investing.com/rss/news_25.rss",  # commodities
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",  # CNBC markets
]


def fetch_cryptopanic_news(coin: str, limit: int = 10) -> list[dict]:
    """Fetch recent news for a specific coin from CryptoPanic."""
    if not config.CRYPTOPANIC_API_KEY:
        return []

    url = "https://cryptopanic.com/api/v1/posts/"
    params = {
        "auth_token": config.CRYPTOPANIC_API_KEY,
        "currencies": coin,
        "public": "true",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])[:limit]
        return [
            {
                "title": item.get("title", ""),
                "source": item.get("source", {}).get("title", "CryptoPanic"),
                "url": item.get("url", ""),
                "published_at": item.get("published_at", ""),
                "coin": coin,
            }
            for item in results
        ]
    except requests.RequestException as e:
        print(f"[news_fetcher] CryptoPanic fetch failed for {coin}: {e}")
        return []


def fetch_rss(feed_urls: list[str], limit: int = 10) -> list[dict]:
    """Fetch headlines from a list of public RSS feeds (no API key needed)."""
    items = []
    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:limit]:
                items.append(
                    {
                        "title": entry.get("title", ""),
                        "source": feed.get("feed", {}).get("title", feed_url),
                        "url": entry.get("link", ""),
                        "published_at": entry.get("published", ""),
                    }
                )
        except Exception as e:
            print(f"[news_fetcher] RSS fetch failed for {feed_url}: {e}")
    return items


def fetch_all_news(instruments: list[str] | None = None) -> dict[str, list[dict]]:
    """
    Fetch news for every watched instrument, grouped by symbol, plus a GENERAL
    bucket for market-wide news relevant to non-crypto instruments.
    Returns a dict: {symbol_or_GENERAL: [news items]}
    """
    instruments = instruments or config.ALL_INSTRUMENTS
    news_by_symbol: dict[str, list[dict]] = {}

    crypto_symbols = [s for s in instruments if config.INSTRUMENTS.get(s, {}).get("type") == "crypto"]
    non_crypto_symbols = [s for s in instruments if config.INSTRUMENTS.get(s, {}).get("type") != "crypto"]

    for coin in crypto_symbols:
        news_by_symbol[coin] = fetch_cryptopanic_news(coin)

    general_items = fetch_rss(CRYPTO_RSS_FEEDS) + fetch_rss(MACRO_RSS_FEEDS)
    news_by_symbol["GENERAL"] = general_items

    # Forex/commodities don't get a per-symbol feed at the free tier — the AI reads
    # the general macro news for these, same as it would scanning headlines by hand.
    for symbol in non_crypto_symbols:
        news_by_symbol[symbol] = []

    fetched_at = datetime.now(timezone.utc).isoformat()
    total = sum(len(v) for v in news_by_symbol.values())
    print(f"[news_fetcher] Fetched {total} news items at {fetched_at}")

    return news_by_symbol


if __name__ == "__main__":
    news = fetch_all_news()
    for symbol, items in news.items():
        print(f"\n=== {symbol} ({len(items)} items) ===")
        for item in items[:3]:
            print(f"  - {item['title']}")
