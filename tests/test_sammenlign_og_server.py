from decimal import Decimal

from handle_mcp.data import Kurv, Vare
from handle_mcp.sammenlign import SOEGEDYBDE, bedste, point, sammenlign


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


def test_sammensat_ord_giver_kun_halvt_point():
    assert point("mælk", Vare("x", "1", "Mælkechokolade", Decimal(10))) == 0.5
    assert point("mælk", Vare("x", "2", "Mælk 1 l", Decimal(10))) == 1
    assert point("æble", Vare("x", "3", "Æbler", Decimal(10))) == 1
    assert point("løg", Vare("x", "4", "Løgene", Decimal(10))) == 1


def test_hovedordet_staar_sidst():
    """'letmælk' er mælk, og en kyllingebrystfilet er stadig kyllingebryst (fundet live på nemlig)."""
    assert point("mælk", Vare("x", "1", "Letmælk 1,5%", Decimal(10))) == 1
    assert point("mælk", Vare("x", "2", "Skummetmælk 0,1%", Decimal(10))) == 1
    assert point("kyllingebryst", Vare("x", "3", "Kyllingebrystfilet", Decimal(10))) == 1
    assert point("mælk", Vare("x", "5", "Mælkesnitte", Decimal(10))) == 0.5


def test_gratisvarer_og_andre_enheder_vaelges_ikke():
    varer = [
        Vare("x", "gratis", "Sandwich kyllingebryst", Decimal(0), enhedspris=Decimal(0), enhed="kg"),
        Vare("x", "portion", "Minimælk", Decimal(87), enhedspris=Decimal("0.87"), enhed="stk"),
        Vare("x", "liter1", "Minimælk", Decimal("10.50"), enhedspris=Decimal("10.50"), enhed="l"),
        Vare("x", "liter2", "Minimælk øko", Decimal("12.95"), enhedspris=Decimal("12.95"), enhed="l"),
    ]
    (valgt, *_), _ = bedste("minimælk", varer)
    assert valgt.id == "liter1"
    (valgt, *_), _ = bedste("kyllingebryst", varer + [Vare("x", "rigtig", "Kyllingebrystfilet", Decimal(40), enhedspris=Decimal(80), enhed="kg")])
    assert valgt.id == "rigtig"


def test_rigtig_vare_slaar_billigere_sammensat_ord():
    varer = [
        Vare("x", "1", "Mælkechokolade", Decimal(5), enhedspris=Decimal(5)),
        Vare("x", "2", "Mælk 1 l", Decimal(12), enhedspris=Decimal(12)),
    ]
    (valgt, *_), score = bedste("mælk", varer)
    assert valgt.id == "2" and score == 1


class DybButik:
    """Som nemlig: de populære (dyre) varer først, den billige langt nede i resultaterne."""

    navn = "dyb"

    def __init__(self):
        self.antal_spurgt = []

    async def kurv(self):
        return Kurv(self.navn, [], Decimal(0), {}, None, None)

    async def soeg(self, tekst, antal=10):
        self.antal_spurgt.append(antal)
        dyre = [Vare(self.navn, str(i), f"Kyllingebryst premium {i}", Decimal(60), enhedspris=Decimal(120)) for i in range(24)]
        billig = Vare(self.navn, "billig", "Kyllingebryst", Decimal(40), enhedspris=Decimal(80))
        return (dyre + [billig])[:antal]


async def test_sammenligning_finder_billig_vare_under_top_10():
    b = DybButik()
    (r,) = await sammenlign([b], [("kyllingebryst", 1)])
    assert b.antal_spurgt == [SOEGEDYBDE]
    assert r.linjer[0].valgt.id == "billig"


async def test_soeg_varer_billigst_foerst():
    from handle_mcp import server

    b = DybButik()
    gammel, server.hent_butik = server.hent_butik, lambda _navn: b
    try:
        svar = await server.soeg_varer("kyllingebryst", antal=3, billigst_foerst=True)
        normal = await server.soeg_varer("kyllingebryst", antal=3)
    finally:
        server.hent_butik = gammel
    assert b.antal_spurgt == [SOEGEDYBDE, 3]
    assert len(svar) == 3 and svar[0]["id"] == "billig"
    assert len(normal) == 3 and all(v["id"] != "billig" for v in normal)


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
