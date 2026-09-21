"""
The AI decision engine.

Sends recent news + current prices + current portfolio state to Claude and asks for a
structured trading decision per coin: action (buy/sell/hold), confidence, and reasoning.

This is the ONLY place the bot uses AI to decide what to do. Everything downstream
(risk engine, paper trader) treats these decisions as untrusted input to be checked
against hard rules, never executed blindly.
"""

import json
import os
from datetime import datetime, timezone

import anthropic

import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

DECISION_SCHEMA_INSTRUCTIONS = """
Respond with ONLY a JSON array, one object per instrument, in this exact shape:

[
  {
    "coin": "BTC",
    "action": "buy" | "sell" | "hold",
    "confidence": 0.0 to 1.0,
    "reasoning": "one or two sentences explaining the call"
  }
]

Use the exact symbol given for each instrument (e.g. "BTC", "XAU", "EURUSD") in the "coin" field.

Rules for your reasoning:
- Base decisions only on the news and price data provided below. Do not invent facts.
- Different instrument types respond to different drivers: crypto often moves on
  project-specific and exchange news; gold and silver often move on interest rate
  expectations, inflation data, and safe-haven demand during risk-off events; oil moves on
  supply/demand data, OPEC+ decisions, and geopolitical events affecting production or
  shipping; forex pairs move on relative central bank policy and macro data between the two
  economies. Weigh the news accordingly for each instrument's type.
- Also consider current time and typical trading-session liquidity where relevant (e.g. forex
  and commodities are far more liquid during London/New York session overlap than off-hours;
  thin liquidity means wider spreads and less reliable price action) — factor this into
  confidence, not just the news itself.
- "confidence" should reflect how strong and unambiguous the signal actually is. Most
  routine news should produce LOW confidence (below 0.5) and a "hold". Reserve high
  confidence (above 0.7) for genuinely clear, significant, and well-corroborated signals.
- If news is sparse, contradictory, or purely speculative, default to "hold" with low confidence.
- Never recommend an action just to have something to say — "hold" is a completely valid
  and often correct answer.
"""


def build_prompt(news_by_symbol: dict, prices: dict, portfolio: dict) -> str:
    now = datetime.now(timezone.utc)
    lines = [f"Current UTC time: {now.isoformat()} ({now.strftime('%A')})", ""]
    lines.append("Current portfolio state:")
    lines.append(json.dumps(portfolio, indent=2))
    lines.append("")
    lines.append("Current prices (USD):")
    lines.append(json.dumps(prices, indent=2))
    lines.append("")
    lines.append("Instruments and their type:")
    for symbol in prices:
        meta = config.INSTRUMENTS.get(symbol, {})
        lines.append(f"  {symbol}: {meta.get('display_name', symbol)} ({meta.get('type', 'unknown')})")

    lines.append("\nRecent news by instrument:")
    for symbol, items in news_by_symbol.items():
        if symbol == "GENERAL" or not items:
            continue
        lines.append(f"\n{symbol}:")
        for item in items[:8]:
            lines.append(f"  - {item['title']} ({item.get('source', 'unknown')})")

    general = news_by_symbol.get("GENERAL", [])
    if general:
        lines.append("\nGeneral market news (relevant to commodities, forex, and broad crypto sentiment):")
        for item in general[:8]:
            lines.append(f"  - {item['title']} ({item.get('source', 'unknown')})")

    lines.append("\n" + DECISION_SCHEMA_INSTRUCTIONS)
    return "\n".join(lines)


def get_decisions(news_by_symbol: dict, prices: dict, portfolio: dict) -> list[dict]:
    """
    Calls Claude with the current market context and returns a list of decision dicts.
    Returns an empty list (safe default = do nothing) if the call fails or the response
    can't be parsed.
    """
    prompt = build_prompt(news_by_symbol, prices, portfolio)

    try:
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        # The response can contain more than one block (e.g. an internal "thinking"
        # block followed by the actual answer), so find the actual text block
        # instead of assuming it's always content[0].
        raw_text = None
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = block.text.strip()
                break

        if raw_text is None:
            print("[ai_decision] No text block found in response, skipping this cycle.")
            return []

        # Strip markdown code fences if the model added them despite instructions
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        decisions = json.loads(raw_text)

    except (anthropic.APIError, json.JSONDecodeError, IndexError, KeyError, AttributeError) as e:
        print(f"[ai_decision] Failed to get/parse decisions: {e}")
        return []

    timestamp = datetime.now(timezone.utc).isoformat()
    for d in decisions:
        d["timestamp"] = timestamp

    _log_decisions(decisions)
    return decisions


def _log_decisions(decisions: list[dict]) -> None:
    """Append decisions to the decisions log so every AI call is auditable later."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(config.DECISIONS_LOG, "a") as f:
        for d in decisions:
            f.write(json.dumps(d) + "\n")


if __name__ == "__main__":
    import news_fetcher
    import price_fetcher

    news = news_fetcher.fetch_all_news()
    prices = price_fetcher.get_prices()
    fake_portfolio = {"cash_usdt": config.STARTING_BALANCE_USDT, "positions": {}}

    decisions = get_decisions(news, prices, fake_portfolio)
    print(json.dumps(decisions, indent=2))
