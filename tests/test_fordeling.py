"""Hvor skal der købes? Billigst samlet, med levering, pakning og minimumskøb (Julius' regel)."""

from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from handle_mcp import madplan, server
from handle_mcp.data import AntalResultat, Kurv, Kurvlinje, Vare
from handle_mcp.fordeling import find_billigste
from handle_mcp.sammenlign import Butiksresultat, Linje

NEMLIG_GEBYR = {"levering": Decimal(36)}
BILKA_GEBYR = {"levering": Decimal(39), "pakkeservice": Decimal(39)}


def resultat(butik, priser, gebyrer, minimum):
    """priser: liste af (søgning, pris) eller (søgning, None) hvis varen ikke findes."""
    linjer = [Linje(s, 1, Vare(butik, f"{butik}-{s}", s, Decimal(p)) if p is not None else None, [], True) for s, p in priser]
    return Butiksresultat(butik, linjer, gebyrer, Decimal(minimum))


def test_en_billig_agurk_er_ikke_en_ekstra_levering_vaerd():
    """Julius' eksempel: alt ét sted, én agurk 3 kr billigere et andet sted. Gebyrerne æder det."""
    varer = [("kød", 300), ("grønt", 200), ("mejeri", 100)]
    nemlig = resultat("nemlig", varer + [("agurk", 12)], NEMLIG_GEBYR, 550)
    bilka = resultat("bilka", [(s, p + 20) for s, p in varer] + [("agurk", 9)], BILKA_GEBYR, 500)
    a = find_billigste([nemlig, bilka])
    assert a["anbefaling"] == "nemlig" and a["besparelse_ved_deling"] == 0
    assert "gebyrerne æder besparelsen" in a["forklaring"] or "minimumskøbet" in a["forklaring"]


def test_deling_naar_besparelsen_er_stoerre_end_gebyret():
    # kød er 300 kr billigere i nemlig, vin 250 kr billigere i Bilka: det betaler gebyret
    nemlig = resultat("nemlig", [("kød", 600), ("vin", 800)], NEMLIG_GEBYR, 550)
    bilka = resultat("bilka", [("kød", 900), ("vin", 550)], BILKA_GEBYR, 500)
    a = find_billigste([nemlig, bilka])
    assert a["anbefaling"] == "nemlig + bilka"
    assert a["fordeling"] == {"nemlig": ["kød"], "bilka": ["vin"]}
    assert a["i_alt"] == 600 + 550 + 36 + 78  # 1264
    assert a["besparelse_ved_deling"] == (600 + 800 + 36) - 1264  # mod alt i nemlig (1436)


def test_deling_maa_ikke_give_en_kurv_under_minimum():
    """Billigst pr. vare ville give Bilka-kurv på 200 kr: under minimum på 500. Så ingen deling."""
    nemlig = resultat("nemlig", [("a", 400), ("b", 300), ("c", 250)], NEMLIG_GEBYR, 550)
    bilka = resultat("bilka", [("a", 700), ("b", 600), ("c", 200)], BILKA_GEBYR, 500)
    a = find_billigste([nemlig, bilka])
    assert a["anbefaling"] == "nemlig"
    assert a["muligheder"]["nemlig + bilka"]["under_minimum"]


def test_minimum_naas_ved_at_flytte_det_der_koster_mindst_ekstra():
    nemlig = resultat("nemlig", [("a", 300), ("b", 300), ("c", 310), ("d", 900)], NEMLIG_GEBYR, 550)
    bilka = resultat("bilka", [("a", 320), ("b", 330), ("c", 300), ("d", 400)], BILKA_GEBYR, 500)
    a = find_billigste([nemlig, bilka])
    # billigst pr. vare: nemlig a,b (600), bilka c,d (700) – begge over minimum
    assert a["anbefaling"] == "nemlig + bilka" and a["fordeling"]["nemlig"] == ["a", "b"]


def test_vare_der_kun_findes_et_sted():
    nemlig = resultat("nemlig", [("a", 600), ("vildt", None)], NEMLIG_GEBYR, 550)
    bilka = resultat("bilka", [("a", 650), ("vildt", 80)], BILKA_GEBYR, 500)
    a = find_billigste([nemlig, bilka])
    assert a["anbefaling"] == "bilka"  # nemlig mangler en vare, deling giver Bilka-kurv under minimum
    assert a["muligheder"]["nemlig"]["mangler"] == ["vildt"]


def test_en_butik_med_fejl_ignoreres():
    nemlig = resultat("nemlig", [("a", 600)], NEMLIG_GEBYR, 550)
    bilka = Butiksresultat("bilka", fejl="kø på hjemmesiden")
    assert find_billigste([nemlig, bilka])["anbefaling"] == "nemlig"


# --- plan og kurv med to butikker -------------------------------------------------------


@pytest.fixture(autouse=True)
def egen_mappe(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))


class FalskButik:
    def __init__(self, navn, kurv=None):
        self.navn, self.indhold, self.kald = navn, dict(kurv or {}), []

    async def kurv(self):
        linjer = [Kurvlinje(i, i, n, Decimal(10) * n) for i, n in self.indhold.items()]
        total = sum((ln.beloeb for ln in linjer), Decimal(0))
        return Kurv(self.navn, linjer, total, {}, total, minimumskoeb=Decimal(1))

    async def saet_antal(self, antal):
        self.kald.append(dict(antal))
        self.indhold.update(antal)
        return [AntalResultat(i, n, n, True) for i, n in antal.items()]


DELT = [
    {"vare_id": "1", "navn": "Kød", "antal": 1, "pris": 600.0, "butik": "nemlig"},
    {"vare_id": "1", "navn": "Vin", "antal": 2, "pris": 550.0, "butik": "bilka"},  # samme id i to butikker er ok
]


def test_plan_med_to_butikker():
    p = madplan.gem("begge", ["Man"], DELT, gebyrer=114)
    assert set(p.linjer_pr_butik()) == {"nemlig", "bilka"}
    assert "nemlig.com + BilkaToGo" in madplan.sms_tekst(p)
    with pytest.raises(madplan.LokalFejl, match="Skriv butik"):
        madplan.gem("begge", ["Man"], [{"vare_id": "9", "navn": "x", "antal": 1, "pris": 1.0}])
    with pytest.raises(madplan.LokalFejl, match="kun for nemlig"):
        madplan.gem("nemlig", ["Man"], [DELT[1]])


async def test_bestil_fylder_begge_kurve(monkeypatch):
    butikker = {"nemlig": FalskButik("nemlig"), "bilka": FalskButik("bilka")}
    monkeypatch.setattr(server, "hent_butik", lambda n: butikker[n])
    kode = (await server.gem_madplan("begge", ["Man"], DELT, gebyrer=114))["kode"]
    svar = await server.fyld_kurv_fra_madplan(kode, "bestil")
    assert butikker["nemlig"].kald == [{"1": 1}] and butikker["bilka"].kald == [{"1": 2}]
    assert svar["butik"] == "nemlig + bilka" and "begge butikker" in svar["naeste_skridt"]


async def test_ingen_kurv_roeres_hvis_den_ene_ikke_er_tom(monkeypatch):
    """Enten fyldes begge kurve, eller ingen."""
    butikker = {"nemlig": FalskButik("nemlig"), "bilka": FalskButik("bilka", kurv={"99": 1})}
    monkeypatch.setattr(server, "hent_butik", lambda n: butikker[n])
    kode = (await server.gem_madplan("begge", ["Man"], DELT))["kode"]
    with pytest.raises(ToolError, match="bilka er ikke tom"):
        await server.fyld_kurv_fra_madplan(kode, "bestil")
    assert butikker["nemlig"].kald == [] and butikker["bilka"].kald == []


async def test_sammenlign_liste_giver_anbefaling(monkeypatch):
    class Butik:
        def __init__(self, navn, pris, gebyr):
            self.navn, self.pris, self.gebyr = navn, pris, gebyr

        async def kurv(self):
            return Kurv(self.navn, [], Decimal(0), {"levering": Decimal(self.gebyr)}, None, Decimal(100))

        async def tilbud(self, antal=25):
            return []

        async def soeg(self, tekst, antal=10):
            return [Vare(self.navn, "1", "Mælk", Decimal(self.pris), maengde="1 l", enhed="l")]

    butikker = {"nemlig": Butik("nemlig", 120, 36), "bilka": Butik("bilka", 118, 78)}
    monkeypatch.setattr(server, "hent_butik", lambda n: butikker[n])
    svar = await server.sammenlign_liste(["1 l mælk"])
    assert svar["anbefaling"]["anbefaling"] == "nemlig"  # 2 kr billigere varer i Bilka, men 42 kr dyrere gebyr
    assert {b["butik"] for b in svar["butikker"]} == {"nemlig", "bilka"}


async def test_ugeplan_forloeb():
    f = (await server.ugeplan_forloeb())["forloeb"]
    assert "hvor mange aftener" in f and "anbefaling" in f and "KØLESKABET" in f
    assert "5 aftener" in server._ugeplan_tekst("5") and "hvor mange aftener" not in server._ugeplan_tekst("5")
    assert "hvor mange aftener" in server.ugeplan()  # prompten har ingen felter
