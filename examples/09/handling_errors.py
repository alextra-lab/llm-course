"""
Section 9 - Robustness: the error types, and which to retry.

The openai SDK raises a small hierarchy of exceptions. The key split is:
  - CLIENT errors (4xx): your request was wrong. Fix it; retrying won't help.
  - TRANSIENT errors (429, 5xx, network): try again, after a wait.

Here we trigger a client error on purpose and show the catch ladder.

    python examples/09/handling_errors.py
"""

import sys
from pathlib import Path

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# An empty messages list is an invalid request -> a 4xx client error.
try:
    client.chat.completions.create(model=MODEL, messages=[])
except BadRequestError as err:
    print("BadRequestError (400): the request was malformed.")
    print("  -> FIX the request; do NOT retry.")
    print("  detail:", err)
except AuthenticationError:
    print("AuthenticationError (401): bad/missing API key. Fix your token.")
except RateLimitError:
    print("RateLimitError (429): you're going too fast. Back off and retry.")
except (APIConnectionError, APITimeoutError, InternalServerError) as err:
    print(f"Transient ({type(err).__name__}): retry with backoff.")

print(
    "\nThe ladder above, from most specific to least, is the pattern to reuse.\n"
    "Catch what you can act on; let the rest bubble up."
)
