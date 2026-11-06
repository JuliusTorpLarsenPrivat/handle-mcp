import pytest

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

    async def tilbud(self, antal=25):
        return []


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


@pytest.mark.parametrize(
    "soegning, navn",
    [  # fundet live i en madplantest: ordet står kun som smag eller tilbehør
        ("fennikel", "Urtete m. fennikel, kamille og humle øko"),
        ("timian", "Kartoffel- og porresuppe m. timian"),
        ("hvidløg", "Baguette m. hvidløg"),
        ("parmesan", "Salami snack (parmesan)"),
        ("kokosmælk", "Karrysuppe m. kokosmælk"),
        ("karry", "Boller i karry m. ris"),
        ("persille", "Kartoffelsalat m. persille og purløg"),
        ("feldsalat", "Iceberg salat"),
    ],
)
def test_smag_og_tilbehoer_er_ikke_varen(soegning, navn):
    assert point(soegning, Vare("x", "1", navn, Decimal(10))) == 0.5


@pytest.mark.parametrize(
    "soegning, navn",
    [
        ("kartofler", "Kartofler m. krydderurter"),
        ("hakket oksekød", "Hakket oksekød m. 50% grønt"),
        ("karry", "Karry (madras)"),
        ("squash", "Squash"),
        ("kyllingebryst", "Kyllingebrystfilet tilsat vand"),
        ("mandler", "Saltede og ristede mandler"),
    ],
)
def test_hoveddelen_er_stadig_varen(soegning, navn):
    assert point(soegning, Vare("x", "1", navn, Decimal(10))) == 1


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

    def __init__(self, tilbud=()):
        self.antal_spurgt = []
        self._tilbud = list(tilbud)

    async def tilbud(self, antal=25):
        return self._tilbud

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
    assert len(svar) == 3 and svar[0]["vare"]["id"] == "billig"
    assert len(normal) == 3 and all(v["id"] != "billig" for v in normal)


async def test_tilbud_uden_for_soegningen_kommer_med():
    """Et tilbud, som søgningen ikke viser, skal stadig kunne vælges. Irrelevante tilbud må ikke."""
    tilbud = [
        Vare("dyb", "tilbud", "Kyllingebryst", Decimal(30), enhedspris=Decimal(60), tilbud="-25 %"),
        Vare("dyb", "andet", "Vaskepulver", Decimal(5), enhedspris=Decimal(1)),
        # fundet live: ender på søgeordet, men er noget helt andet
        Vare("dyb", "indlaeg", "Trusseindlæg", Decimal(5), enhedspris=Decimal(1)),
        Vare("dyb", "frost", "Hel and frost", Decimal(5), enhedspris=Decimal(1)),
    ]
    b = DybButik(tilbud)
    (r,) = await sammenlign([b], [("kyllingebryst", 1)])
    assert r.linjer[0].valgt.id == "tilbud"
    assert all(v.id != "andet" for v in r.linjer[0].alternativer)
    (r,) = await sammenlign([DybButik(tilbud)], [("ost", 1), ("æg", 1)])
    assert r.mangler == ["ost", "æg"]  # trusseindlæg er ikke æg, frost er ikke ost


def test_tilbud_skal_matche_direkte():
    indlaeg = Vare("x", "1", "Trusseindlæg", Decimal(5))
    frilandsaeg = Vare("x", "2", "Frilandsæg", Decimal(5))
    assert point("æg", indlaeg) == 1 and point("æg", indlaeg, sammensat=False) == 0
    assert point("æg", frilandsaeg, sammensat=False) == 0  # søgningen finder den stadig
    assert point("kyllingebryst", Vare("x", "3", "Kyllingebrystfilet", Decimal(5)), sammensat=False) == 1


async def test_fejl_i_tilbud_stopper_ikke_sammenligningen():
    from handle_mcp.butikker import ButikFejl

    class TilbudFejler(DybButik):
        async def tilbud(self, antal=25):
            raise ButikFejl("tilbudssiden har ændret sig")

    (r,) = await sammenlign([TilbudFejler()], [("kyllingebryst", 1)])
    assert r.fejl is None and r.linjer[0].valgt.id == "billig"


async def test_billigste_butik_foerst():
    dyr = FalskButik("dyr", [("Letmælk", 15), ("Pasta", 20)], 29)
    billig = FalskButik("billig", [("Letmælk", 11), ("Pasta", 12)], 39)
    (forst, anden) = await sammenlign([dyr, billig], [("letmælk", 2), ("pasta", 1)])
    assert forst.butik == "billig" and forst.i_alt == Decimal(11 * 2 + 12 + 39)
    assert anden.i_alt == Decimal(15 * 2 + 20 + 29)


async def test_nemligs_posegebyr_regnes_med():
    """Fundet i brug: planen sagde 1.180 kr, kurven 1.198 kr. Poserne står ikke på en tom kurv."""
    (r,) = await sammenlign([FalskButik("nemlig", [("Letmælk", 10)], 36)], [("letmælk", 1)])
    assert r.gebyrer == {"levering": Decimal(36), "poser": Decimal("17.85")}
    assert r.i_alt == Decimal("63.85")


async def test_posegebyr_fra_kurven_bruges_hvis_det_findes():
    class MedPoser(FalskButik):
        async def kurv(self):
            k = await super().kurv()
            k.gebyrer["poser"] = Decimal(20)
            return k

    (r,) = await sammenlign([MedPoser("nemlig", [("Letmælk", 10)], 36)], [("letmælk", 1)])
    assert r.gebyrer["poser"] == Decimal(20)


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


@pytest.mark.parametrize("navn", ["Græskinspireret yoghurt øko.", "Yoghurt græsk-stil", "Yoghurt græsk type 10%"])
def test_efterligning_er_ikke_varen(navn):
    """Fundet i test 3: "Græskinspireret yoghurt" blev valgt til græsk yoghurt."""
    assert point("græsk yoghurt", Vare("x", "1", navn, Decimal(20))) <= 0.5
    assert point("græsk yoghurt", Vare("x", "2", "Græsk yoghurt 10% øko.", Decimal(30))) == 1


# --- fundet ved live-tjek af butiksvalget (test 4-listen) ----------------------------------


@pytest.mark.parametrize(
    "soegning, forkert",
    [
        ("Nakkekoteletter 400 g øko", Vare("b", "1", "Majsmel øko", Decimal(28), maengde="400 g")),
        ("Kværnsalt 500 g Jozo", Vare("b", "2", "Ketchup u. tilsat sukker og salt", Decimal(20), maengde="500 g")),
        ("Avocado i net 700 g øko", Vare("b", "3", "Kernerugbrød øko", Decimal(25), maengde="700 g")),
    ],
)
def test_maengde_og_oeko_alene_er_ikke_et_match(soegning, forkert):
    assert point(soegning, forkert) < 0.5


def test_maengde_midt_i_soegningen():
    from handle_mcp.maengde import behov

    tekst, b = behov("Nakkekoteletter 400 g øko")
    assert tekst == "Nakkekoteletter øko" and str(b) == "400 g"


def test_kasse_med_12_daaser_vinder_ikke_over_en_daase():
    from handle_mcp.sammenlign import rangeret

    kasse = Vare("n", "1", "Hakkede tomater øko.", Decimal(156), maengde="12 x 400 g", enhedspris=Decimal("32.5"), enhed="kg")
    daase = Vare("n", "2", "Hakkede tomater øko.", Decimal("13.82"), maengde="400 g", enhedspris=Decimal("34.55"), enhed="kg")
    assert rangeret("hakkede tomater øko", [kasse, daase])[0][1].id == "2"


def test_procent_skal_passe():
    """Fundet live: "Chokolade 85%" blev til "Chokolade 50%", da tal blev ignoreret."""
    assert point("chokolade 85%", Vare("n", "1", "Chokolade 50%", Decimal(20), maengde="100 g")) <= 0.5
    assert point("chokolade 85%", Vare("n", "2", "Chokolade 85%", Decimal(40), maengde="100 g")) == 1
