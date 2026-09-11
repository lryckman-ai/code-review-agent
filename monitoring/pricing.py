"""
Small illustrative pricing table backing the cost_estimate log event.

Real cost is ~$0 — the LLM is self-hosted (gx10.lan), not a metered API.
reference_cost_usd answers "what would this have cost on a hosted model" —
approximate public list prices, illustrative only, not billing-accurate.
"""

# model -> (price per 1M prompt tokens, price per 1M completion tokens), USD
PRICING_PER_MILLION = {
    "gpt-4o": (2.50, 10.00),
}

REFERENCE_MODEL = "gpt-4o"


def real_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """Cost of the actual self-hosted/tunneled model — effectively free."""
    return 0.0


def reference_cost_usd(
    prompt_tokens: int, completion_tokens: int, model: str = REFERENCE_MODEL,
) -> float | None:
    pricing = PRICING_PER_MILLION.get(model)
    if pricing is None:
        return None
    prompt_price, completion_price = pricing
    return (
        (prompt_tokens / 1_000_000) * prompt_price
        + (completion_tokens / 1_000_000) * completion_price
    )
