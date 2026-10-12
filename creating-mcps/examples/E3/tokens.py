"""
E3: checking a token, as a resource server does -- with the issuer's published keys.

Unit 7's verifier read the issuer's public key from a file. A resource server in a real
deployment fetches it from the authorization server's `jwks_uri` instead, picks the key by the
token's `kid`, and refetches when it meets a `kid` it doesn't know (the issuer rotated). It
accepts a token only if ALL of these hold:

    signature   one of the issuer's published keys; EdDSA, pinned
    iss         that issuer, exactly
    aud         THIS resource: a token for the gateway is refused at the ticket API, and the
                other way round
    exp / nbf   in date
    scope       whatever this resource needs (the caller of `check` decides what)
"""

from __future__ import annotations

import json
import sys
import time

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

ALGORITHM = "EdDSA"


class Verifier:
    """The SDK's TokenVerifier protocol (`verify_token`), plus `check` for plain code."""

    def __init__(self, issuer: str, audience: str, jwks_uri: str | None = None):
        self.issuer, self.audience = issuer.rstrip("/"), audience.rstrip("/")
        self.jwks_uri = jwks_uri or f"{self.issuer}/jwks.json"
        self.keys: dict[str, jwt.PyJWK] = {}

    def _key(self, kid: str | None) -> jwt.PyJWK:
        if kid not in self.keys:                     # first use, or the issuer rotated
            with httpx.Client(trust_env=False, timeout=5) as http:
                found = http.get(self.jwks_uri).raise_for_status().json()
            self.keys = {k["kid"]: jwt.PyJWK(k) for k in found["keys"]}
        if kid not in self.keys:
            raise jwt.InvalidTokenError(f"no published key {kid!r}")
        return self.keys[kid]

    def check(self, token: str) -> dict:
        """The verified claims, or jwt.InvalidTokenError saying why not."""
        kid = jwt.get_unverified_header(token).get("kid")
        return jwt.decode(token, self._key(kid), algorithms=[ALGORITHM],
                          audience=self.audience, issuer=self.issuer, leeway=5,
                          options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]})

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = self.check(token)
        except (jwt.InvalidTokenError, httpx.HTTPError) as e:
            print(json.dumps({"operation": "auth.refused", "reason": type(e).__name__,
                              "detail": str(e)[:120], "at": round(time.time(), 3)}),
                  file=sys.stderr, flush=True)
            return None
        return AccessToken(token=token, client_id=claims.get("client_id", ""),
                           scopes=str(claims.get("scope", "")).split(),
                           expires_at=claims["exp"], resource=self.audience,
                           subject=claims["sub"], claims=claims)
