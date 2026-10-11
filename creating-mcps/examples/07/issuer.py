"""
Unit 7: a tiny local token issuer -- the course's stand-in for your identity provider.

It signs short-lived JWT access tokens with an Ed25519 key. Servers hold only the PUBLIC key:
they can check a token, never mint one. Each token says

    iss     who issued it (this issuer)
    sub     who the person is (priya, marc, ...)
    groups  the directory groups they're in (soc-l1, soc-l3, ...)
    aud     the ONE server it's for (that server's URL): no other server accepts it
    exp     when it stops working (default: 1 hour)
    jti     a unique id, so logs can name the token without containing it

What a group may do is NOT in the token; it's the server's policy (policy.toml). The issuer
says who you are; the server decides what that allows. The full OAuth flow (discovery,
client registration, consent, refresh) is enterprise track E3. Here tokens are issued
directly, as a lab IdP would hand them out.

    python examples/07/issuer.py keys                                 # make the key pair
    python examples/07/issuer.py issue priya http://127.0.0.1:8707/mcp
    python examples/07/issuer.py inspect <token>                      # decode, don't trust

The private key lives in env/keys/ (gitignored). On a real deployment it never leaves the
identity provider; the servers fetch the public key (a JWKS URL) instead of a file.
"""

from __future__ import annotations

import argparse
import json
import secrets
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

COURSE_DIR = Path(__file__).resolve().parents[2]
KEY_DIR = COURSE_DIR / "env" / "keys"
PRIVATE_KEY = KEY_DIR / "issuer.pem"          # the issuer's only: never give it to a server
PUBLIC_KEY = KEY_DIR / "issuer.pub.pem"        # what servers verify with
ISSUER = "https://idp.creating-mcps.local"
ALGORITHM = "EdDSA"
CLIENT_ID = "unit7-host"

# The lab's directory: person -> groups. In real life this is your IdP (Entra ID, Okta, ...).
DIRECTORY = {
    "priya": ["soc-l1"],                       # tier-1 analyst
    "marc": ["soc-l3"],                        # tier-3 incident responder
    "zoe": ["audit"],                          # compliance
    "sam": ["contractors"],                    # in the directory, but no policy grants it
}


def keys(force: bool = False) -> Path:
    """Create the key pair once (env/keys/, mode 0600 for the private key)."""
    if PRIVATE_KEY.exists() and PUBLIC_KEY.exists() and not force:
        return PUBLIC_KEY
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    PRIVATE_KEY.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    PRIVATE_KEY.chmod(0o600)
    PUBLIC_KEY.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return PUBLIC_KEY


def issue(sub: str, audience: str, ttl: int = 3600, groups: list[str] | None = None,
          **extra) -> str:
    """A signed access token for `sub`, valid at `audience` only, for `ttl` seconds."""
    if groups is None:
        if sub not in DIRECTORY:
            raise ValueError(f"{sub!r} isn't in the directory: {sorted(DIRECTORY)}")
        groups = DIRECTORY[sub]
    keys()
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": sub, "groups": groups, "aud": audience,
              "client_id": CLIENT_ID, "scope": "mcp:tools", "iat": now, "nbf": now,
              "exp": now + ttl, "jti": secrets.token_hex(8), **extra}
    return jwt.encode(claims, PRIVATE_KEY.read_bytes(), algorithm=ALGORITHM)


def inspect(token: str) -> dict:
    """The claims, WITHOUT checking anything: for reading a token, never for trusting it."""
    return {"header": jwt.get_unverified_header(token),
            "claims": jwt.decode(token, options={"verify_signature": False})}


def main() -> None:
    parser = argparse.ArgumentParser(description="The course's local token issuer.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("keys", help="create the key pair (once)")
    p.add_argument("--force", action="store_true", help="replace it: every token dies")
    p = sub.add_parser("issue", help="issue an access token")
    p.add_argument("user", choices=sorted(DIRECTORY))
    p.add_argument("audience", help="the server's URL, e.g. http://127.0.0.1:8707/mcp")
    p.add_argument("--ttl", type=int, default=3600, help="seconds (default 3600)")
    p = sub.add_parser("inspect", help="decode a token without verifying it")
    p.add_argument("token")
    args = parser.parse_args()
    if args.command == "keys":
        print(f"public key: {keys(args.force)}")
    elif args.command == "issue":
        print(issue(args.user, args.audience, args.ttl))
    else:
        print(json.dumps(inspect(args.token), indent=2))


if __name__ == "__main__":
    main()
