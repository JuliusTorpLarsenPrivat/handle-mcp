"""Grænsetilfælde: tomme, mærkelige og urimelige input må give en klar fejl eller et
fornuftigt svar, aldrig et nedbrud."""

from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from handle_mcp import server
from handle_mcp.data import Vare
from handle_mcp.maengde import Maengde, behov, pakker
from handle_mcp.sammenlign import bedste, point, rangeret


@pytest.mark.parametrize("tekst", ["", "   ", "!!!", "0", "500 g"])
def test_tom_eller_meningsloes_soegning_giver_intet(tekst):
    v = Vare("x", "1", "Mælk", Decimal(10))
    rent, _ = behov(tekst)
    assert point(rent, v) == 0
    assert bedste(rent, [v]) == ([], 0.0)


def test_meget_lang_soegning_gaar_ikke_ned():
    v = Vare("x", "1", "Mælk", Decimal(10))
    assert 0 < point("mælk " + "x" * 5000, v) < 1


@pytest.mark.parametrize("tekst", ["0 g mælk", "999 kg mælk", "0,5 l mælk", "1.000 g mælk"])
def test_maerkelige_maengder(tekst):
    rent, b = behov(tekst)
    assert rent == "mælk" or "mælk" in rent
    v = Vare("x", "1", "Mælk", Decimal(10), maengde="1 l", enhed="l")
    bedste(rent, [v], b)  # må ikke gå ned


def test_urimeligt_mange_pakker_giver_ingen_pakkeberegning():
    assert pakker(Maengde(999000, "g"), 500) is None


def test_ingen_varer_eller_kun_udsolgte():
    udsolgt = Vare("x", "1", "Mælk", Decimal(10), paa_lager=False)
    gratis = Vare("x", "2", "Mælk", Decimal(0))
    assert rangeret("mælk", []) == []
    assert rangeret("mælk", [udsolgt, gratis]) == []


def test_mangler_enhedspris_og_maengde():
    a = Vare("x", "a", "Mælk", Decimal(12))
    b = Vare("x", "b", "Mælk", Decimal(10))
    (valgt, *_), _ = bedste("mælk", [a, b], Maengde(1000, "ml"))
    assert valgt.id == "b"  # falder tilbage til prisen


@pytest.mark.parametrize(
    "kald",
    [
        lambda: server.soeg_varer("mælk", butik="coop"),
        lambda: server.soeg_varer("mælk", butik="coop", billigst_foerst=True),
        lambda: server.vis_kurv("netto"),
        lambda: server.ugens_tilbud("rema"),
        lambda: server.leveringstider("lidl"),
        lambda: server.tidligere_ordrer("coop"),
        lambda: server.saet_antal_i_kurv("coop", {"1": 1}),
        lambda: server.sammenlign_liste(["mælk"], butikker=["coop"]),
    ],
)
async def test_ukendt_butik_giver_klar_fejl(kald):
    """Fundet i testen: soeg_varer og vis_kurv sendte en rå ButikFejl i stedet for en ToolError."""
    with pytest.raises(ToolError, match="Kender ikke butikken"):
        await kald()


async def test_tom_indkoebsliste(monkeypatch):
    class Tom:
        navn = "tom"

        async def kurv(self):
            from handle_mcp.data import Kurv

            return Kurv("tom", [], Decimal(0), {}, None)

        async def tilbud(self, antal=25):
            return []

        async def soeg(self, tekst, antal=10):
            return []

    monkeypatch.setattr(server, "hent_butik", lambda _n: Tom())
    (r,) = await server.sammenlign_liste([], butikker=["tom"])
    assert r["linjer"] == [] and r["i_alt"] == 0 and r["fejl"] is None


async def test_ugens_tilbud_giver_hoejst_antal(monkeypatch):
    """Fundet live: ugens_tilbud('nemlig', 3) gav 12 (3 pr. tilbudsliste x 4 lister)."""

    class FlereLister:
        async def tilbud(self, antal=25):
            return [Vare("x", str(i), f"Tilbud {i}", Decimal(10)) for i in range(antal * 4)]

    monkeypatch.setattr(server, "hent_butik", lambda _n: FlereLister())
    assert len(await server.ugens_tilbud("nemlig", 3)) == 3
    assert len(await server.ugens_tilbud("nemlig", 0)) == 1


async def test_saet_antal_afviser_negative_og_store_tal():
    for antal in (-1, 51):
        with pytest.raises(ToolError, match="mellem 0 og 50"):
            await server.saet_antal_i_kurv("nemlig", {"123": antal})


@pytest.mark.parametrize("andel", [0, -1, 1.5])
async def test_koebsmoenster_afviser_urimelig_andel(andel, tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    with pytest.raises(ToolError):
        await server.koebsmoenster(andel)


async def test_beholdning_taaler_emoji_og_mange_sprog(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    svar = await server.opdater_beholdning(tilfoej=[{"navn": "Jalapeño 🌶️", "maengde": "½ glas"}, {"navn": "Crème fraîche"}])
    assert {v["navn"] for v in svar["beholdning"]} == {"Jalapeño 🌶️", "Crème fraîche"}
