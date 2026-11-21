"""Model prices for the spend view, in USD per million tokens.

Source: Anthropic's published API prices (as of 2026-11-03). Keyed by the model id stored on each stage result.
Change this table, not the dashboard code, when prices or the enterprise provider change. Cache-creation tokens are
not recorded per stage, so spend is a slight underestimate when prompt caching is in use.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float  # includes thinking tokens, which are billed as output
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10),
    # Haiku 5.5's cache-read price is not in the table this was built from; priced at the input rate (an upper bound).
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.10),
}


def cost(price: Price, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    return (input_tokens * price.input + output_tokens * price.output + cache_read_tokens * price.cache_read) / 1_000_000
