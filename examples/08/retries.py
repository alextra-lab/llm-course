"""
Section 8 - Robustness: retry transient failures with backoff.

Transient errors (rate limits, brief server hiccups, dropped connections) should
be retried after a short, GROWING wait, with a little randomness ("jitter") so
many clients don't all retry in lockstep. Client errors (4xx) are NOT retried.

The SDK can also do this for you -- see the note at the bottom -- but writing it
once by hand shows what "exponential backoff" actually means.

    python examples/08/retries.py
"""

import random
import sys
import time
from pathlib import Path

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

TRANSIENT = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)


def with_retries(make_call, attempts=5):
    for attempt in range(attempts):
        try:
            return make_call()
        except TRANSIENT as err:
            if attempt == attempts - 1:
                raise  # out of tries -> let it fail
            delay = min(2 ** attempt, 30) + random.uniform(0, 1)  # 1s, 2s, 4s, ... + jitter
            print(f"  transient {type(err).__name__}; retrying in {delay:.1f}s")
            time.sleep(delay)


response = with_retries(
    lambda: client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Say hello."}],
    )
)
print("succeeded:", response.choices[0].message.content)

# In practice you often just let the SDK retry for you:
#
#     from openai import OpenAI
#     client = OpenAI(max_retries=5, timeout=20.0)   # built-in backoff + timeout
#
# The hand-rolled version above is here so you understand what it's doing.
