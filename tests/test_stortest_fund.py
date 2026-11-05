"""Regressionstest for fejl fundet i den store live-test (88 varer i nemlig og Bilka).
Varenavnene er de rigtige navne fra butikkerne."""

from decimal import Decimal

import pytest

from handle_mcp.data import Vare
from handle_mcp.maengde import Maengde, behov, pakkestoerrelse
from handle_mcp.sammenlign import antal_pakker, bedste, er_blanding, er_ikke_mad, har_tilbehoer, ord_i, point


def v(id_, navn, pris=10, maengde=None, enhed="kg"):
    return Vare("x", id_, navn, Decimal(str(pris)), maengde=maengde, enhed=enhed)


@pytest.mark.parametrize(
    "soegning, navn, maengde",
    [
        ("hakket svinekød", "Hakket grisekød 8-12% fedt", "0.5 kg"),  # svin = gris
        ("svinemørbrad", "Mørbrad af gris øko", "0.45 kg"),  # dyret foran, stykket som eget ord
        ("feta", "Fetaost øko.", "150 g"),  # fetaost er feta
        ("icebergsalat", "Iceberg salat øko.", "1 stk."),  # sammenskrevet
        ("makrel i tomat", "Makrel i tomat", "125 g"),  # søgningen har selv tilbehør
        ("tun i vand", "Tun i vand", "185 g / 140 g"),
        ("frosne ærter", "Ærter øko.", "500 g / frost / Frigodan"),  # frost i mængden tæller
        ("stegeflæsk", "Stegeflæsk i skiver", "500 g"),
    ],
)
def test_rigtig_vare_faar_fuldt_point(soegning, navn, maengde):
    assert point(soegning, v("1", navn, maengde=maengde)) == 1


def test_decimalkomma_er_ikke_tilbehoer():
    assert not har_tilbehoer(v("1", "Letmælk 1,5% fedt øko"))
    assert not har_tilbehoer(v("1", "Skyr naturel 0,2% fedt"))
    assert har_tilbehoer(v("1", "Kartofler, bagekartofler"))


def test_svinekoed_vaelger_koed_ikke_spaek():
    """Fundet live hos Bilka: 'hakket svinekød' blev til hakket grisespæk."""
    spaek = v("spaek", "Hakket grisespæk", 25, "500 g")
    lever = v("lever", "Hakket griselever", 25, "500 g")
    koed = v("koed", "Hakket grisekød 8-12% fedt", 27.95, "0.5 kg")
    (valgt, *_), p = bedste("hakket svinekød", [spaek, lever, koed], Maengde(500, "g"))
    assert valgt.id == "koed" and p == 1


def test_sandwichskiver_og_snittet_kaal_er_forarbejdet():
    """Fundet i runde 2: kyllingebryst blev til sandwichskiver, spidskål til snittet kål."""
    skiver = v("skiver", "Kyllingebryst sandwichskiver", 32, "300 g")
    filet = v("filet", "Kyllingebrystfilet", 65, "650 g")
    (valgt, *_), _ = bedste("kyllingebryst", [skiver, filet], Maengde(500, "g"))
    assert valgt.id == "filet"
    snittet = v("snit", "Snittet og skyllet spidskål øko", 15, "250 g", "stk")
    hel = v("hel", "Spidskål øko", 18, "1 stk", "stk")
    (valgt, *_), _ = bedste("spidskål", [snittet, hel], Maengde(1, "stk"))
    assert valgt.id == "hel"


@pytest.mark.parametrize(
    "soegning, skidt, god",
    [  # fundet ved manuel gennemgang af runde 3
        ("tomater", "Flåede tomater", "Tomater øko."),
        ("bacon", "Crispy bacon snacks", "Bacon i skiver"),
        ("rejer", "Indbagte rejer", "Koldtvandsrejer"),
        ("laks", "Pulled laks", "Laksefilet"),
        ("ingefær", "Knust ingefær", "Ingefær øko"),
        ("blåbær", "Mini frugtstænger m blåbær øko", "Blåbær"),
    ],
)
def test_frisk_raavare_frem_for_forarbejdet(soegning, skidt, god):
    s, g = v("skidt", skidt, 5, "100 g"), v("god", god, 30, "100 g")
    (valgt, *_), _ = bedste(soegning, [s, g], Maengde(100, "g"))
    assert valgt.id == "god"


def test_ukendt_pakkestoerrelse_skoennes_ud_fra_kilopris():
    """Fundet live hos Bilka: almindelige bananer (pr. stk) røg bagerst, og Saba-bananer til 85 kr vandt."""
    alm = Vare("x", "alm", "Bananer", Decimal(3), maengde="1 stk", enhedspris=Decimal(16), enhed="kg")
    saba = Vare("x", "saba", "Saba bananer", Decimal("84.75"), maengde="1 kg", enhedspris=Decimal("84.75"), enhed="kg")
    (valgt, *_), _ = bedste("bananer", [saba, alm], Maengde(1000, "g"))
    assert valgt.id == "alm"


def test_citroner_er_ikke_te_slik_eller_halspastiller():
    """Fundet i runde 4 hos nemlig: '4 stk citroner' blev til urtete. Skønnet regnede med
    prisen pr. tebrev (1,25 kr/stk), og halspastiller og slik stod blandt alternativerne."""
    te = Vare("x", "te", "Ingefær & Citron urtete", Decimal("20.05"), maengde="16 breve / Fredsted", enhedspris=Decimal("1.25"), enhed="stk")
    bites = Vare("x", "bites", "Citron Bites", Decimal("28.95"), maengde="8 stk. / 144 g", enhed="kg")
    strepsils = Vare("x", "strep", "Strepsils honning og citron", Decimal("80.25"), maengde="24 stk / 0,6 mg amylmetacresol", enhed="stk")
    citron = Vare("x", "citron", "Citron øko.", Decimal(5), maengde="1 stk.", enhedspris=Decimal(5), enhed="stk")
    fundne, _ = bedste("citroner", [te, bites, strepsils, citron], Maengde(4, "stk"), oeko=True)
    assert fundne[0].id == "citron" and "strep" not in [f.id for f in fundne]


def test_stegeflaesk_i_skiver_er_raat():
    """Fundet live hos nemlig: stegeflæsk i skiver blev markeret som forarbejdet (pålæg)."""
    from handle_mcp.sammenlign import er_forarbejdet

    assert not er_forarbejdet(v("1", "Stegeflæsk i skiver", maengde="500 g"), ord_i("stegeflæsk"))


@pytest.mark.parametrize(
    "navn, maengde",
    [
        ("Clematis bdt.", "1 bdt. / 10 stilke / ca. 50 cm"),
        ("Salling blomsterløg Tulipaner Van Eijk 5", "1 stk"),
        ("Claus Dalby Prydløg Gladiator 1 løg", "1 stk"),
        ("1-2-Spray Max gulvmoppe refill", "1 stk"),
        ("Opvaskemiddel Citron", "1.5 l"),
    ],
)
def test_ikke_mad_vaelges_aldrig_til_mad(navn, maengde):
    """Fundet live: buketter for persille/basilikum, prydløg for forårsløg, moppe for basilikum."""
    skidt = v("skidt", navn, 1, maengde, "stk")
    assert er_ikke_mad(skidt)
    fundne, _ = bedste("persille", [skidt, v("ok", "Persille", 10, "1 stk", "stk")])
    assert [x.id for x in fundne] == ["ok"]


def test_ikke_mad_maa_godt_findes_naar_man_soeger_efter_det():
    (valgt, *_), _ = bedste("opvaskemiddel", [v("1", "Opvaskemiddel Citron", 45, "1.5 l", "l")])
    assert valgt.id == "1"


def test_blanding_kommer_efter_ren_vare():
    """Fundet live hos nemlig: 'frosne ærter' blev til 'Ærter og gulerødder'."""
    mix = v("mix", "Ærter og gulerødder", 16.19, "750 g / frost")
    rene = v("rene", "Ærter øko.", 30.5, "500 g / frost")
    assert er_blanding(mix, ord_i("frosne ærter")) and not er_blanding(rene, ord_i("frosne ærter"))
    assert not er_blanding(v("m", "Saltede og ristede mandler"), ord_i("mandler"))  # "og" mellem tillægsord
    (valgt, *_), _ = bedste("frosne ærter", [mix, rene], Maengde(500, "g"))
    assert valgt.id == "rene"


@pytest.mark.parametrize(
    "tekst, rent, maengde",
    [
        ("1 bdt persille", "persille", Maengde(1, "stk")),
        ("2 pk tortillas", "tortillas", Maengde(2, "stk")),
        ("1 bakke jordbær", "jordbær", Maengde(1, "stk")),
    ],
)
def test_bundt_og_pakke_er_maengder(tekst, rent, maengde):
    """Fundet live: '1 bdt persille' gav kun halvt point, fordi 'bdt' blev søgt som et ord."""
    assert behov(tekst) == (rent, maengde)


def test_antal_x_vaegt_er_stk():
    """Fundet live hos Bilka: '8 stk tortilla' kunne ikke regnes ud for 'Tortillas øko (8 x 40 g)'."""
    assert pakkestoerrelse("8 x 40 g", enhed="stk") == 8
    assert antal_pakker(v("1", "Tortillas øko", 15, "8 x 40 g"), Maengde(8, "stk")) == 1


def test_et_stk_af_vare_solgt_efter_vaegt_er_en_pakke():
    """Fundet live: '1 stk hel kylling' (1,35 kg) og '1 stk broccoli' (500 g) gav 'pakke ukendt'."""
    assert antal_pakker(v("1", "Hel kylling", 69, "1,35 kg"), Maengde(1, "stk")) == 1
    assert antal_pakker(v("1", "Tortillas", 10, "370 g"), Maengde(8, "stk")) is None  # 8 stk kan ikke gættes
