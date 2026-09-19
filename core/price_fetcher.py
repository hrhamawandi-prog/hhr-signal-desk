"""
Fetches live prices for every instrument type we support:
  - crypto            -> CoinGecko (free, no key required)
  - forex/commodities -> OANDA practice API (free demo account required)

Both return a flat {symbol: price_in_usd} dict so the rest of the bot doesn't need
to care where a price came from.
"""

import requests

import config

# CoinGecko uses its own id slugs rather than ticker symbols.
SYMBOL_TO_COINGECKO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LTC": "litecoin",
}

OANDA_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


def get_crypto_prices(coins: list[str]) -> dict[str, float]:
    ids = [SYMBOL_TO_COINGECKO_ID[c] for c in coins if c in SYMBOL_TO_COINGECKO_ID]
    if not ids:
        return {}

    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ",".join(ids), "vs_currencies": "usd"}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[price_fetcher] Crypto price fetch failed: {e}")
        return {}

    prices = {}
    id_to_symbol = {v: k for k, v in SYMBOL_TO_COINGECKO_ID.items()}
    for coingecko_id, price_data in data.items():
        symbol = id_to_symbol.get(coingecko_id)
        if symbol and "usd" in price_data:
            prices[symbol] = price_data["usd"]

    return prices


def get_oanda_prices(symbols: list[str]) -> dict[str, float]:
    """Fetches prices for forex/commodity symbols via OANDA's pricing endpoint.
    Returns {} silently if OANDA isn't configured yet — the bot still runs on
    crypto-only until you add OANDA keys."""
    if not config.OANDA_API_KEY or not config.OANDA_ACCOUNT_ID:
        return {}

    oanda_symbols = [
        config.INSTRUMENTS[s]["oanda_symbol"]
        for s in symbols
        if s in config.INSTRUMENTS and "oanda_symbol" in config.INSTRUMENTS[s]
    ]
    if not oanda_symbols:
        return {}

    host = OANDA_HOSTS.get(config.OANDA_ENVIRONMENT, OANDA_HOSTS["practice"])
    url = f"{host}/v3/accounts/{config.OANDA_ACCOUNT_ID}/pricing"
    headers = {"Authorization": f"Bearer {config.OANDA_API_KEY}"}
    params = {"instruments": ",".join(oanda_symbols)}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[price_fetcher] OANDA price fetch failed: {e}")
        return {}

    oanda_to_symbol = {
        meta["oanda_symbol"]: sym
        for sym, meta in config.INSTRUMENTS.items()
        if "oanda_symbol" in meta
    }

    prices = {}
    for price in data.get("prices", []):
        oanda_symbol = price.get("instrument")
        symbol = oanda_to_symbol.get(oanda_symbol)
        if not symbol:
            continue
        bids = price.get("bids", [])
        asks = price.get("asks", [])
        if bids and asks:
            # midpoint of best bid/ask — good enough for decision-making and paper trading
            prices[symbol] = (float(bids[0]["price"]) + float(asks[0]["price"])) / 2

    return prices


def get_prices(instruments: list[str] | None = None) -> dict[str, float]:
    """
    Returns {symbol: price} across every instrument type in one call.
    Defaults to config.ALL_INSTRUMENTS if no list is given.
    """
    instruments = instruments or config.ALL_INSTRUMENTS

    crypto_symbols = [s for s in instruments if config.INSTRUMENTS.get(s, {}).get("type") == "crypto"]
    oanda_symbols = [s for s in instruments if config.INSTRUMENTS.get(s, {}).get("type") in ("forex", "commodity")]

    prices = {}
    prices.update(get_crypto_prices(crypto_symbols))
    prices.update(get_oanda_prices(oanda_symbols))
    return prices


if __name__ == "__main__":
    prices = get_prices()
    for symbol, price in prices.items():
        name = config.INSTRUMENTS.get(symbol, {}).get("display_name", symbol)
        print(f"{symbol} ({name}): ${price:,.4f}")

    missing = set(config.ALL_INSTRUMENTS) - set(prices.keys())
    if missing:
        print(f"\nNo price for: {', '.join(missing)}")
        if any(config.INSTRUMENTS[s]["type"] in ("forex", "commodity") for s in missing):
            print("(forex/commodity prices need OANDA_API_KEY + OANDA_ACCOUNT_ID in .env — see README)")
