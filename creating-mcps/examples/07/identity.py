"""
Unit 7: who is calling -- verifying the bearer token on every request.

The SDK's HTTP transport asks a TokenVerifier about each request's `Authorization: Bearer`
header before anything else runs; no token, or a bad one, is a 401 and the MCP request never
reaches the server. This verifier accepts a token only if ALL of these hold:

    signature   made with the issuer's key (we hold only its public half)
    algorithm   EdDSA, pinned: "none", or an HMAC made with the public key, is refused
    iss         our issuer
    aud         THIS server's URL: a token minted for another server is refused, so a server
                that receives your token can't replay it anywhere else
    exp / nbf   in date
    sub, jti    present

The token itself is never logged or passed on. Logs carry its `jti`; backends see the
caller's persona through the gateway login (common_mcp.acting_for), never the token. That is
"no token passthrough": the token is for this server, and only this server.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import jwt
from mcp.server.auth.provider import AccessToken

import issuer as idp


class JwtVerifier:
    """The SDK's TokenVerifier protocol: `verify_token(token) -> AccessToken | None`."""

    def __init__(self, audience: str, public_key: Path = idp.PUBLIC_KEY,
                 issuer: str = idp.ISSUER, leeway: int = 5):
        self.audience, self.issuer, self.leeway = audience, issuer, leeway
        self.key = public_key.read_bytes()

    def check(self, token: str) -> dict:
        """The verified claims, or jwt.InvalidTokenError saying why not."""
        return jwt.decode(token, self.key, algorithms=[idp.ALGORITHM], audience=self.audience,
                          issuer=self.issuer, leeway=self.leeway,
                          options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]})

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = self.check(token)
        except jwt.InvalidTokenError as e:
            # Why it failed goes to the server's log (never the token); the caller gets a 401.
            print(json.dumps({"operation": "auth.refused", "reason": type(e).__name__,
                              "detail": str(e)[:120], "at": round(time.time(), 3)}),
                  file=sys.stderr, flush=True)
            return None
        return AccessToken(token=token, client_id=claims.get("client_id", ""),
                           scopes=str(claims.get("scope", "")).split(),
                           expires_at=claims["exp"], resource=self.audience,
                           subject=claims["sub"], claims=claims)
