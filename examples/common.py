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

import httpx
from openai import OpenAI

# The model id, read once. Every script uses the same one for this course.
MODEL = os.environ.get("MODEL", "openai/gpt-oss-120b")

# An embedding model id, used from Section 15 onward (usually a different model).
EMBED_MODEL = os.environ.get("EMBED_MODEL", "")


def ssl_verify():
    """Decide how TLS certificates are verified, from the environment.

    On some machines (a corporate proxy that inspects HTTPS, a self-signed
    endpoint, an out-of-date CA store) the default verification fails with
    `CERTIFICATE_VERIFY_FAILED`. These knobs let you fix it without editing code:

      * OPENAI_CA_BUNDLE  - path to a CA bundle / cert to trust (preferred).
                            REQUESTS_CA_BUNDLE / SSL_CERT_FILE are also honored.
      * OPENAI_INSECURE=1 - skip verification entirely (last resort, insecure;
                            only on a network you trust).

    Returns a value suitable for both `requests` (verify=) and httpx (verify=):
    a path string, or True/False.
    """
    if os.environ.get("OPENAI_INSECURE", "").lower() in ("1", "true", "yes"):
        return False
    bundle = (os.environ.get("OPENAI_CA_BUNDLE")
              or os.environ.get("REQUESTS_CA_BUNDLE")
              or os.environ.get("SSL_CERT_FILE"))
    return bundle if bundle else True


def get_client() -> OpenAI:
    """Build an OpenAI SDK client pointed at our hosted endpoint."""
    return OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        http_client=httpx.Client(verify=ssl_verify()),
    )
