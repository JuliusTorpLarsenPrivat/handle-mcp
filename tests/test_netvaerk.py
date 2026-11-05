"""HTTP-tilstand: uden den rigtige nøgle må intet komme igennem til MCP-serveren."""

import httpx
import pytest

from handle_mcp import netvaerk
from handle_mcp.server import mcp

NOEGLE = "test-" + "x" * 40
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
HOVEDER = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def klient(host="127.0.0.1"):
    app = netvaerk.app(mcp, NOEGLE, host)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}:8765")


@pytest.mark.parametrize("auth", [None, "Bearer forkert", f"Bearer {NOEGLE}x", NOEGLE])
async def test_uden_rigtig_noegle_afvises(auth):
    hoveder = HOVEDER | ({"authorization": auth} if auth else {})
    async with klient() as k:
        svar = await k.post("/mcp", json=INIT, headers=hoveder)
    assert svar.status_code == 401
    assert NOEGLE not in svar.text


def test_for_kort_noegle_afvises():
    with pytest.raises(ValueError):
        netvaerk.KraevNoegle(object(), "kort")


async def test_rigtig_noegle_slipper_igennem_til_serveren():
    app = netvaerk.app(mcp, NOEGLE, "127.0.0.1")
    starlette = app.app  # ASGITransport kører ikke opstarten (lifespan), så det gør vi selv
    async with starlette.router.lifespan_context(starlette), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as k:
        svar = await k.post("/mcp", json=INIT, headers=HOVEDER | {"authorization": f"Bearer {NOEGLE}"})
    assert svar.status_code == 200
    assert '"serverInfo"' in svar.text and '"handle"' in svar.text
