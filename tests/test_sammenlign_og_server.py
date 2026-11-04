from decimal import Decimal

from handle_mcp.data import Kurv, Vare
from handle_mcp.sammenlign import point, sammenlign


class FalskButik:
    def __init__(self, navn, priser, gebyr):
        self.navn, self.priser, self.gebyr = navn, priser, gebyr

    async def kurv(self):
        return Kurv(self.navn, [], Decimal(0), {"levering": Decimal(self.gebyr)}, None, Decimal(300))

    async def soeg(self, tekst, antal=10):
        return [Vare(self.navn, navn, navn, Decimal(p), enhedspris=Decimal(p)) for navn, p in self.priser if tekst.split()[0] in navn.lower()]


def test_point_taeller_ord_og_boejninger():
    v = Vare("x", "1", "Hakkede tomater", Decimal(5), maengde="400 g")
    assert point("hakkede tomat", v) == 1
    assert point("flåede tomater", v) == 0.5


async def test_billigste_butik_foerst():
    dyr = FalskButik("dyr", [("Letmælk", 15), ("Pasta", 20)], 29)
    billig = FalskButik("billig", [("Letmælk", 11), ("Pasta", 12)], 39)
    (forst, anden) = await sammenlign([dyr, billig], [("letmælk", 2), ("pasta", 1)])
    assert forst.butik == "billig" and forst.i_alt == Decimal(11 * 2 + 12 + 39)
    assert anden.i_alt == Decimal(15 * 2 + 20 + 29)


async def test_manglende_vare_markeres():
    b = FalskButik("b", [("Letmælk", 11)], 0)
    (r,) = await sammenlign([b], [("letmælk", 1), ("safran", 1)])
    assert r.mangler == ["safran"]


async def test_serveren_starter_og_viser_vaerktoejer():
    """Starter serveren som et rigtigt program, sådan som Claude Desktop gør."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    p = StdioServerParameters(command=sys.executable, args=["-m", "handle_mcp.server"])
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        navne = {t.name for t in (await s.list_tools()).tools}
    assert {"soeg_varer", "sammenlign_liste", "saet_antal_i_kurv", "vis_kurv"} <= navne
    assert not any(ord_ in n for n in navne for ord_ in ("bestil", "betal", "order", "pay"))
