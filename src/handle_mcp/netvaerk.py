"""Serveren over netværket (HTTP), så den kan køre på en maskine, der altid er tændt.

Til hverdag taler serveren med Claude Desktop over stdin/stdout på samme maskine. Over HTTP
kan andre enheder (fx telefoner via Tailscale) også nå den. Derfor kræver hver forespørgsel
en adgangsnøgle:

    Authorization: Bearer <nøgle>

Nøglen laves med `handle-mcp http-noegle`, gemmes i nøgleringen og vises kun den ene gang.
Som standard lytter serveren kun på 127.0.0.1 (denne maskine). Den må aldrig stilles åbent
på internettet: brug et privat netværk som Tailscale.
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any

import keyring

from handle_mcp.login import TJENESTE

NOEGLE_NAVN = "http-noegle"


def ny_noegle() -> str:
    noegle = secrets.token_urlsafe(32)
    keyring.set_password(TJENESTE, NOEGLE_NAVN, noegle)
    return noegle


def gemt_noegle() -> str | None:
    return keyring.get_password(TJENESTE, NOEGLE_NAVN)


class KraevNoegle:
    """ASGI-mellemled: afviser alt uden den rigtige nøgle, før MCP-serveren ser det."""

    def __init__(self, app: Any, noegle: str):
        if len(noegle) < 32:
            raise ValueError("Adgangsnøglen er for kort.")
        self.app = app
        self.forventet = f"Bearer {noegle}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            givet = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(givet, self.forventet):  # samme tid uanset hvor meget der passer
                await self._afvis(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _afvis(send: Any) -> None:
        krop = json.dumps({"fejl": "Forkert eller manglende adgangsnøgle."}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
            }
        )
        await send({"type": "http.response.body", "body": krop})


def app(mcp: Any, noegle: str, host: str) -> KraevNoegle:
    """MCP-serverens HTTP-app med nøglekrav foran. `host` styrer beskyttelsen mod DNS-rebinding."""
    from mcp.server.transport_security import TransportSecuritySettings

    sikkerhed = None
    if host not in ("127.0.0.1", "localhost", "::1"):
        # Uden for denne maskine er nøglen beskyttelsen. Host-tjekket ville afvise fx Tailscale-navne.
        sikkerhed = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return KraevNoegle(mcp.streamable_http_app(host=host, transport_security=sikkerhed), noegle)


def koer(mcp: Any, host: str = "127.0.0.1", port: int = 8765) -> int:
    import uvicorn

    noegle = gemt_noegle()
    if not noegle:
        print("Der er ingen adgangsnøgle endnu. Kør først: handle-mcp http-noegle")
        return 1
    print(f"handle-mcp lytter på http://{host}:{port}/mcp  (stop med Ctrl+C)")
    uvicorn.run(app(mcp, noegle, host), host=host, port=port, log_level="warning")
    return 0
