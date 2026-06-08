"""
Shared helpers for the course examples (introduced in Section 2).

In Section 1 every script built its own client and read its own env vars, on
purpose — so you saw the moving parts. From here on we factor that one bit of
boilerplate into a single place and import it.

Because our example scripts live in numbered folders (examples/02/, examples/03/,
...), they aren't an importable package. The two lines at the top of each script
that uses this helper add the `examples/` directory to Python's import path so
`from common import ...` works no matter what folder you run from:

    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
    from common import get_client, MODEL
"""

import os

from openai import OpenAI

# The model id, read once. Every script uses the same one for this course.
MODEL = os.environ.get("MODEL", "openai/gpt-oss-120b")

# An embedding model id, used from Section 15 onward (usually a different model).
EMBED_MODEL = os.environ.get("EMBED_MODEL", "")


def get_client() -> OpenAI:
    """Build an OpenAI SDK client pointed at our hosted endpoint."""
    return OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
