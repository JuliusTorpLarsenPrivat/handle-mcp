from decimal import Decimal

import pytest

from handle_mcp.data import Vare
from handle_mcp.maengde import Maengde, behov, pakker, pakkestoerrelse
from handle_mcp.sammenlign import Linje, bedste, er_oeko


@pytest.mark.parametrize(
    "tekst, rent, maengde",
    [
        ("500 g hakket oksekød", "hakket oksekød", Maengde(500, "g")),
        ("hakket oksekød 1,5 kg", "hakket oksekød", Maengde(1500, "g")),
        ("1 l letmælk", "letmælk", Maengde(1000, "ml")),
        ("2 stk løg", "løg", Maengde(2, "stk")),
        ("4 dl fløde", "fløde", Maengde(400, "ml")),
        ("hakkede tomater", "hakkede tomater", None),
        ("mælk 3,5%", "mælk 3,5%", None),  # procent er ikke en mængde
        ("500 g", "500 g", None),  # kun en mængde: søg på det hele
    ],
)
def test_behov(tekst, rent, maengde):
    assert behov(tekst) == (rent, maengde)


@pytest.mark.parametrize(
    "tekster, enhed, stoerrelse",
    [
        (("1,50 kg / tilsat vand / Rose",), "g", 1500),
        (("12 x 400 g / Taste of Nature",), "g", 4800),
        (("400 g / 240 g / Taste of Nature",), "g", 400),  # første, ikke drænet vægt
        (("2 stk. / 250 g / m. ostecreme",), "g", 250),
        (("2 stk. / 250 g / m. ostecreme",), "stk", 2),
        (("0.5 l",), "ml", 500),
        ((None, "Squash 750 g"), "g", 750),  # står mængden kun i navnet
        (("1 bdt.",), "g", None),
    ],
)
def test_pakkestoerrelse(tekster, enhed, stoerrelse):
    assert pakkestoerrelse(*tekster, enhed=enhed) == stoerrelse


def test_pakker():
    assert pakker(Maengde(500, "g"), 400) == 2
    assert pakker(Maengde(400, "g"), 400) == 1
    assert pakker(Maengde(100, "g"), 4000) == 1
    assert pakker(Maengde(30000, "g"), 400) is None  # urimeligt mange


def kylling(id_, pris, maengde, enhedspris, navn="Kyllingebrystfilet"):
    return Vare("x", id_, navn, Decimal(pris), maengde=maengde, enhedspris=Decimal(enhedspris), enhed="kg")


def test_mindste_pakke_der_daekker_behovet_slaar_billig_kilopris():
    """Fundet live: 4 kg kylling til 239 kr vandt på kg-pris, selvom der kun skulle bruges 500 g."""
    stor = kylling("stor", 239, "4 kg", "59.75")
    lille = kylling("lille", 45, "500 g", "90")
    (valgt, *_), _ = bedste("kyllingebryst", [stor, lille], Maengde(500, "g"))
    assert valgt.id == "lille"
    (valgt, *_), _ = bedste("kyllingebryst", [stor, lille])  # uden mængde: kg-prisen vinder
    assert valgt.id == "stor"


def test_to_smaa_pakker_kan_vaere_billigst():
    en_kilo = kylling("1kg", 110, "1 kg", "110")
    halv = kylling("halv", 40, "500 g", "80")
    (valgt, *_), _ = bedste("kyllingebryst", [en_kilo, halv], Maengde(900, "g"))
    assert valgt.id == "halv"  # 2 x 40 kr slår 110 kr
    ln = Linje("900 g kyllingebryst", 1, valgt, [], True, Maengde(900, "g"))
    assert ln.pakker == 2 and ln.beloeb == 80


def test_oeko_maa_koste_lidt_mere():
    alm = kylling("alm", 40, "500 g", "80")
    oeko = kylling("oeko", 55, "500 g", "110", "Kyllingebrystfilet øko.")
    for_dyr = kylling("dyr", 70, "500 g", "140", "Kyllingebrystfilet økologisk")
    assert er_oeko(oeko) and er_oeko(for_dyr) and not er_oeko(alm)
    (valgt, *_), _ = bedste("kyllingebryst", [alm, oeko], Maengde(500, "g"), oeko=True)
    assert valgt.id == "oeko"  # 55 kr < 40 kr x 1,5
    (valgt, *_), _ = bedste("kyllingebryst", [alm, for_dyr], Maengde(500, "g"), oeko=True)
    assert valgt.id == "alm"  # 70 kr > 40 kr x 1,5
    (valgt, *_), _ = bedste("kyllingebryst", [alm, oeko], Maengde(500, "g"))
    assert valgt.id == "alm"  # uden øko-ønske vinder prisen


def test_raa_vare_frem_for_forarbejdet():
    """Fundet live: kyllingebryst blev til pålæg og en færdigret, laks til indbagt laks."""
    paalaeg = kylling("paalaeg", 10, "100 g", "100", "Kyllingebryst (klassisk)")
    faerdig = kylling("faerdig", 60, "800 g", "75", "Kyllingebrystfilet m. kartofler og grøntsager")
    skiver = kylling("skiver", 32, "350 g", "91", "Kyllingebryst i skiver")
    raa = kylling("raa", 35, "450 g", "78", "Kyllingebrystfilet")
    (valgt, *_), _ = bedste("kyllingebryst", [paalaeg, faerdig, skiver, raa], Maengde(600, "g"))
    assert valgt.id == "raa"
    indbagt = Vare("x", "indbagt", "Indbagt laksefilet m. spinat", Decimal(18), maengde="250 g", enhed="kg")
    laks = Vare("x", "laks", "Laksefilet m. skind øko.", Decimal(118), maengde="600 g", enhed="kg")
    (valgt, *_), _ = bedste("laksefilet", [indbagt, laks], Maengde(400, "g"))
    assert valgt.id == "laks"
    (valgt, *_), _ = bedste("kyllingebryst i skiver", [skiver, raa])  # søger man efter det, får man det
    assert valgt.id == "skiver"


def test_roeget_og_blandet_koed_er_ikke_varen():
    """Fundet live i ugeplan: røget kyllingebryst (pålæg) og okse- og grisekød for oksekød."""
    roeget = kylling("roeget", 11, "100 g", "110", "Røget kyllingebryst")
    stegt = kylling("stegt", 31, "200 g", "154", "Stegt kyllingebryst")
    raa = kylling("raa", 65, "650 g", "100", "Kyllingebrystfilet")
    (valgt, *_), _ = bedste("kyllingebryst", [roeget, stegt, raa], Maengde(400, "g"))
    assert valgt.id == "raa"
    blandet = Vare("x", "bl", "Hakket okse- og grisekød 8-12% fedt", Decimal(36), maengde="0.4 kg", enhed="kg")
    okse = Vare("x", "okse", "Hakket oksekød 8-12% fedt", Decimal(45), maengde="0.4 kg", enhed="kg")
    (valgt, *_), _ = bedste("hakket oksekød", [blandet, okse], Maengde(400, "g"))
    assert valgt.id == "okse"
    (valgt, *_), _ = bedste("hakket okse- og grisekød", [blandet, okse])  # søger man efter det, får man det
    assert valgt.id == "bl"


def test_skind_og_bundt_er_ikke_tilbehoer():
    """Fundet live: 3 x 180 g back loin (240 kr) slog 600 g økolaks m. skind (118 kr)."""
    loin = Vare("x", "loin", "Laks - back loin", Decimal("79.95"), maengde="180 g", enhed="kg")
    skind = Vare("x", "skind", "Laksefilet m. skind øko.", Decimal("117.57"), maengde="600 g", enhed="kg")
    (valgt, *_), _ = bedste("laks", [loin, skind], Maengde(400, "g"))
    assert valgt.id == "skind"
    smaa = Vare("x", "smaa", "Hele grønne asparges", Decimal(29), maengde="100 g", enhed="kg")
    bundt = Vare("x", "bundt", "Grønne asparges i bundt", Decimal(30), maengde="250 g", enhed="kg")
    (valgt, *_), _ = bedste("grønne asparges", [smaa, bundt], Maengde(250, "g"))
    assert valgt.id == "bundt"


def test_fire_alternativer():
    varer = [kylling(str(i), 40 + i, "500 g", 80 + i) for i in range(8)]
    fundne, _ = bedste("kyllingebryst", varer)
    assert len(fundne) == 5
