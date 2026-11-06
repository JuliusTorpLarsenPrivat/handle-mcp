"""Ugeplanen: gem, godkend med kode, og fyld kurven med præcis de godkendte varer."""

from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from handle_mcp import madplan, server
from handle_mcp.data import AntalResultat, Kurv, Kurvlinje
from handle_mcp.lokal import LokalFejl

LINJER = [
    {"vare_id": "111", "navn": "Laksefilet m. skind øko.", "antal": 1, "pris": 117.57},
    {"vare_id": "222", "navn": "Hakket grisekød 8-12%", "antal": 2, "pris": 70.0},
]


@pytest.fixture(autouse=True)
def egen_mappe(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))


class FalskButik:
    """Husker kurven og hvad der blev lagt i den. Kan aldrig bestille: den metode findes ikke."""

    def __init__(self, kurv=None, priser=None):
        self.indhold = dict(kurv or {})  # id -> antal
        self.priser = priser or {"111": Decimal("117.57"), "222": Decimal(35), "999": Decimal(10)}
        self.kald = []

    async def kurv(self):
        linjer = [Kurvlinje(i, f"vare {i}", n, self.priser.get(i, Decimal(1)) * n) for i, n in self.indhold.items()]
        total = sum((ln.beloeb for ln in linjer), Decimal(0))
        return Kurv("nemlig", linjer, total, {"levering": Decimal(36)}, total + 36)

    async def saet_antal(self, antal):
        self.kald.append(dict(antal))
        self.indhold.update({i: n for i, n in antal.items() if n})
        return [AntalResultat(i, n, n, True) for i, n in antal.items()]


@pytest.fixture
def butik(monkeypatch):
    b = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: b)
    return b


# --- madplan.py -----------------------------------------------------------------


def test_gem_og_hent():
    p = madplan.gem("nemlig", ["Man: Laks", "Tir: Chili"], LINJER, gebyrer=36)
    assert p.kode.startswith("UGE-") and len(p.kode) == 8 and p.status == "afventer"
    assert p.varer_i_alt == 187.57 and p.i_alt == 223.57
    assert madplan.hent(p.kode.lower()).kode == p.kode and madplan.hent().kode == p.kode
    assert p.kode in madplan.sms_tekst(p) and "224 kr" in madplan.sms_tekst(p)


@pytest.mark.parametrize(
    "linjer, fejl",
    [
        ([], "ingen varer"),
        ([{**LINJER[0], "antal": 0}], "heltal"),
        ([{**LINJER[0], "antal": 21}], "heltal"),
        ([{**LINJER[0], "pris": -1}], "negativ"),
        ([{**LINJER[0], "vare_id": ""}], "vare_id"),
        ([LINJER[0], LINJER[0]], "to gange"),
    ],
)
def test_ugyldige_planer_afvises(linjer, fejl):
    with pytest.raises(LokalFejl, match=fejl):
        madplan.gem("nemlig", ["Man: Laks"], linjer)


def test_ukendt_butik():
    with pytest.raises(LokalFejl, match="nemlig eller bilka"):
        madplan.gem("coop", [], LINJER)


@pytest.mark.parametrize(
    "svar, ok",
    [
        ("OK UGE-4821", True),
        ("ok uge 4821", True),
        ("Ja, godkend UGE4821 :)", True),
        ("Godkendt UGE-4821", True),
        ("UGE-4821", False),  # intet ja
        ("OK", False),  # ingen kode
        ("Nej UGE-4821", False),
        ("Hvad koster UGE-4821?", False),
        ("OK UGE-4821 og UGE-1234", False),  # to koder: tvetydigt
        ("OK UGE-1234", False),  # forkert plan
        ("BOOK UGE-4821", False),  # "OK" skal være et helt ord
    ],
)
def test_er_godkendt(svar, ok):
    assert madplan.er_godkendt("UGE-4821", svar) is ok


# --- værktøjerne ----------------------------------------------------------------


async def test_hele_forloebet(butik):
    gemt = await server.gem_madplan("nemlig", ["Man: Laks", "Tir: Chili"], LINJER, gebyrer=36)
    kode = gemt["kode"]
    assert gemt["sms_tekst"].startswith(f"Ugeplan {kode}") and gemt["advarsel"] is None
    assert butik.kald == []  # at gemme planen rører ikke kurven
    svar = await server.fyld_kurv_fra_madplan(kode, f"ok {kode}")
    assert butik.kald == [{"111": 1, "222": 2}]  # præcis de godkendte varer
    assert svar["advarsler"] == [] and "betal" in svar["naeste_skridt"]
    assert (await server.vis_madplan(kode))["status"] == "kurv fyldt"


async def test_uden_godkendelse_sker_intet(butik):
    kode = (await server.gem_madplan("nemlig", ["Man: Laks"], LINJER))["kode"]
    for svar in ("lav planen om", kode, "ja, men uden laks", "OK UGE-0000", "hvad koster det?"):
        with pytest.raises(ToolError, match="ikke en godkendelse"):
            await server.fyld_kurv_fra_madplan(kode, svar)
    assert butik.kald == []


async def test_planen_kan_kun_bruges_en_gang(butik):
    kode = (await server.gem_madplan("nemlig", ["Man: Laks"], LINJER))["kode"]
    await server.fyld_kurv_fra_madplan(kode, f"OK {kode}")
    with pytest.raises(ToolError, match="allerede brugt"):
        await server.fyld_kurv_fra_madplan(kode, f"OK {kode}")
    assert len(butik.kald) == 1


async def test_kurven_skal_vaere_tom(monkeypatch):
    b = FalskButik(kurv={"999": 3})
    monkeypatch.setattr(server, "hent_butik", lambda _n: b)
    kode = (await server.gem_madplan("nemlig", ["Man: Laks"], LINJER))["kode"]
    with pytest.raises(ToolError, match="ikke tom"):
        await server.fyld_kurv_fra_madplan(kode, f"OK {kode}")
    assert b.kald == [] and (await server.vis_madplan(kode))["status"] == "afventer"
    await server.fyld_kurv_fra_madplan(kode, f"OK {kode}", kurven_maa_have_andet=True)
    assert b.kald == [{"111": 1, "222": 2}] and b.indhold["999"] == 3  # andre varer bliver


async def test_advarsel_ved_prisstigning(monkeypatch):
    b = FalskButik(priser={"111": Decimal(150), "222": Decimal(40)})  # 230 kr mod 187,57 i planen
    monkeypatch.setattr(server, "hent_butik", lambda _n: b)
    kode = (await server.gem_madplan("nemlig", ["Man: Laks"], LINJER))["kode"]
    svar = await server.fyld_kurv_fra_madplan(kode, f"OK {kode}")
    assert any("koster nu 230.00 kr" in a for a in svar["advarsler"])


async def test_advarsel_over_budget(butik):
    await server.opdater_profil(ugebudget_kr=100)
    gemt = await server.gem_madplan("nemlig", ["Man: Laks"], LINJER, gebyrer=36)
    assert "over ugebudgettet på 100 kr" in gemt["advarsel"]


async def test_vaerktoejerne_kan_ikke_bestille():
    """Ingen af de nye værktøjer må kunne bestille, betale eller vælge leveringstid."""
    navne = {t.name for t in await server.mcp.list_tools()}
    assert {"gem_madplan", "vis_madplan", "fyld_kurv_fra_madplan"} <= navne
    assert not any(o in n for n in navne for o in ("bestil", "betal", "order", "pay", "checkout"))


async def test_prompten_ugeplan():
    prompts = {p.name: p for p in await server.mcp.list_prompts()}
    assert "ugeplan" in prompts
    tekst = server.ugeplan(5, "nemlig")
    assert "5 aftener" in tekst and "vent_paa_svar" in tekst and "hent_opskrift_fra_link" in tekst
    assert "bestil" in tekst and "Bestil og betal aldrig selv" in tekst
    # rækkefølgen: køleskab, menu, pris, bestil
    assert tekst.index("KØLESKABET") < tekst.index("MENUEN") < tekst.index("PRISEN") < tekst.index("BESTIL")


# --- ny plan erstatter gammel, og et kort "bestil" ---------------------------------


def test_ny_plan_annullerer_den_ventende():
    a = madplan.gem("nemlig", ["Man: Laks"], LINJER)
    b = madplan.gem("nemlig", ["Man: Torsk"], LINJER)
    assert madplan.hent(a.kode).status == "annulleret" and "erstattet af " + b.kode in madplan.hent(a.kode).historik[-1]
    assert madplan.eneste_ventende().kode == b.kode


@pytest.mark.parametrize(
    "svar, ok",
    [
        ("bestil", True),
        ("Bestil det bare", True),
        ("ja tak", True),
        ("OK!", True),
        ("køb lortet", True),
        ("ja, men uden laks", False),
        ("bestil, men drop vinen", False),
        ("ok jeg kigger på det", False),
        ("tak", False),  # intet ja-ord
        ("hvad koster det", False),
    ],
)
def test_kort_ja(svar, ok):
    assert madplan.er_kort_ja(svar) is ok


def test_kort_ja_gaelder_kun_den_ventende_plan():
    gammel = madplan.gem("nemlig", ["Man"], LINJER)
    ny = madplan.gem("nemlig", ["Man"], LINJER)
    assert madplan.er_godkendt(ny.kode, "bestil")
    assert not madplan.er_godkendt(gammel.kode, "bestil")  # annulleret
    assert madplan.er_godkendt(ny.kode, f"OK {ny.kode}")


def test_kort_ja_virker_ikke_for_gamle_planer(monkeypatch):
    p = madplan.gem("nemlig", ["Man"], LINJER)
    monkeypatch.setattr(madplan, "MAKS_ALDER_TIMER", -1)  # som om den er mere end 48 timer gammel
    assert not madplan.er_godkendt(p.kode, "bestil")
    assert madplan.er_godkendt(p.kode, f"OK {p.kode}")  # med koden går det stadig


async def test_bestil_fylder_kurven(butik):
    gemt = await server.gem_madplan("nemlig", ["Man: Laks"], LINJER)
    assert "bestil" in gemt["sms_tekst"]
    await server.fyld_kurv_fra_madplan(gemt["kode"], "bestil")
    assert butik.kald == [{"111": 1, "222": 2}]
