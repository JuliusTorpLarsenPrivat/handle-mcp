"""Mængdetilbud ("2 for 150 kr"). Varens pris er normalprisen pr. stk (set live i begge butikker)."""

from decimal import Decimal

import pytest

from handle_mcp.data import Vare
from handle_mcp.maengde import Maengde, maengdetilbud, pris_for_antal
from handle_mcp.sammenlign import Linje, bedste, tilbud_tip


@pytest.mark.parametrize(
    "tekst, forventet",
    [  # de rigtige tekster fra butikkerne
        ("3 for 85 kr", (3, Decimal(85))),
        ("2 for 150 kr", (2, Decimal(150))),
        ("Mix 2 stk. 35,-", (2, Decimal(35))),
        ("Mix 3 stk. 89,-", (3, Decimal(89))),
        ("Mix 2 for 40,00", (2, Decimal("40.00"))),
        ("Mix 10 for 25,00", (10, Decimal("25.00"))),
        ("Skarp pris", None),
        ("-30 %", None),
        ("Tilbud, før 85 kr", None),
        ("Ugens priskup, før 15,75 kr", None),
        ("Spar 2,73", None),
        (None, None),
        ("1 for 10 kr", None),  # ikke et mængdetilbud
    ],
)
def test_laes_maengdetilbud(tekst, forventet):
    assert maengdetilbud(tekst) == forventet


def test_pris_for_antal():
    p = Decimal("79.95")
    assert pris_for_antal(p, "2 for 150 kr", 1) == p
    assert pris_for_antal(p, "2 for 150 kr", 2) == 150
    assert pris_for_antal(p, "2 for 150 kr", 3) == 150 + p
    assert pris_for_antal(p, "2 for 150 kr", 4) == 300
    assert pris_for_antal(p, None, 3) == p * 3
    assert pris_for_antal(Decimal(10), "3 for 50 kr", 3) == 30  # "tilbuddet" er dyrere: brug normalpris


def test_tilbud_aendrer_valget():
    """2 x 400 g til normalpris 45 = 90 kr, men 2 x 400 g af '2 for 70 kr' = 70 kr."""
    alm = Vare("x", "alm", "Hakket grisekød 8-12%", Decimal(40), maengde="400 g", enhed="kg")
    tilbud = Vare("x", "tilbud", "Hakket grisekød 8-12%", Decimal("40.70"), maengde="400 g", enhed="kg", tilbud="2 for 70 kr")
    (valgt, *_), _ = bedste("hakket grisekød", [alm, tilbud], Maengde(800, "g"))
    assert valgt.id == "tilbud"
    ln = Linje("800 g hakket grisekød", 1, valgt, [], True, Maengde(800, "g"))
    assert ln.pakker == 2 and ln.beloeb == 70 and ln.tip is None


def test_tip_naar_tilbuddet_kraever_flere():
    kylling = Vare("x", "k", "Hel kylling", Decimal("79.95"), maengde="1,35 kg", enhed="kg", tilbud="2 for 150 kr")
    ln = Linje("1 stk hel kylling", 1, kylling, [], True, Maengde(1, "stk"))
    assert ln.beloeb == Decimal("79.95")
    assert ln.tip == "Mængdetilbud: køb 2 for 150.00 kr (75.00 kr/stk) i stedet for 1"
    assert tilbud_tip(kylling, 2) is None  # passer allerede


async def test_tip_i_sammenlign_liste(monkeypatch):
    from handle_mcp import server
    from handle_mcp.data import Kurv

    class Butik:
        navn = "b"

        async def kurv(self):
            return Kurv("b", [], Decimal(0), {}, None)

        async def tilbud(self, antal=25):
            return []

        async def soeg(self, tekst, antal=10):
            # "1 stk." skal stå der: "2 stk løg" må ikke blive til 2 poser á 1 kg
            return [Vare("b", "k", "Hel kylling", Decimal("79.95"), maengde="1 stk. / 1,35 kg", enhed="kg", tilbud="2 for 150 kr")]

    monkeypatch.setattr(server, "hent_butik", lambda _n: Butik())
    (r,) = (await server.sammenlign_liste(["2 stk hel kylling"], butikker=["b"]))["butikker"]
    assert r["linjer"][0]["beloeb"] == 150 and r["linjer"][0]["tip"] is None
    (r,) = (await server.sammenlign_liste(["1 stk hel kylling"], butikker=["b"]))["butikker"]
    assert r["linjer"][0]["tip"].startswith("Mængdetilbud: køb 2 for 150")
