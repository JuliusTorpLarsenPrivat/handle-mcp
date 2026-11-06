"""Madplanens start. Fundet live: Claude stillede spørgsmålene i chatten i stedet for på
Telegram. Nu sender handle selv det første spørgsmål og siger klart, at snakken foregår der."""

import json

import httpx
import pytest

from handle_mcp import server, telegram, ugeplan


@pytest.fixture
def tg(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    sendt = []

    def falsk(req):
        d = json.loads(req.content or b"{}")
        if req.url.path.endswith("/sendMessage"):
            sendt.append((d["chat_id"], d["text"]))
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(falsk))
    monkeypatch.setattr(telegram, "hent_token", lambda: "123456789:" + "A" * 35)
    monkeypatch.setattr(telegram, "AUTOSTART", False)
    return sendt


def kobl_paa(*chats):
    d = telegram._tilstand()
    d["chats"] = {str(c): f"person{c}" for c in chats}
    telegram.skriv("telegram", d)


async def test_madplan_sender_selv_spoergsmaalet_paa_telegram(tg):
    kobl_paa(1, 2)
    svar = await server.ugeplan_forloeb()
    assert [c for c, _ in tg] == [1, 2] and all(t == ugeplan.FOERSTE_SPOERGSMAAL for _, t in tg)
    assert "Rester" in tg[0][1] and "fryseren" in tg[0][1] and "aftener" in tg[0][1]
    assert "Basisvarer" in tg[0][1] and "behøver I ikke nævne" in tg[0][1]  # tjekkes via listerne
    f = svar["forloeb"]
    assert "ER ALLEREDE SENDT til 2 telefon(er)" in f
    assert "Stil det IKKE igen" in f and "ingen spørgsmål til husstanden her i chatten" in f
    assert "send_besked" in f and "vent_paa_svar" in f
    assert f.index("KØLESKABET") < f.index("MENUEN") < f.index("PRISEN") < f.index("BESTIL")


async def test_uden_telegram_foregaar_det_i_chatten(tg, monkeypatch):
    # ingen tilkoblede telefoner
    svar = await server.ugeplan_forloeb()
    assert tg == [] and svar["telegram"] == "ikke sat op"
    assert "her i chatten" in svar["forloeb"] and "hvor mange aftener" in svar["forloeb"]


async def test_uden_botnoegle(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))

    def ingen():
        raise telegram.TelegramFejl("ikke sat op")

    monkeypatch.setattr(telegram, "hent_token", ingen)
    assert (await server.ugeplan_forloeb())["telegram"] == "ikke sat op"


async def test_telegram_nede_falder_tilbage_til_chatten(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    monkeypatch.setattr(telegram, "hent_token", lambda: "123456789:" + "A" * 35)
    monkeypatch.setattr(telegram, "AUTOSTART", False)
    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(lambda r: httpx.Response(500, json={"ok": False, "description": "nede"})))
    kobl_paa(1)
    svar = await server.ugeplan_forloeb()
    assert "kunne ikke sende" in svar["telegram"] and "her i chatten" in svar["forloeb"]


def test_prompten_peger_paa_vaerktoejet():
    assert "ugeplan_forloeb" in server.ugeplan()


async def test_serverinstruktionen_siger_telegram():
    assert "foregår al snak med husstanden der, ikke i chatten" in server.mcp.instructions


async def test_beskeder_fra_mellem_madplanerne_kommer_med_foer_spoergsmaalet(tg):
    """Ellers blev en besked fra tirsdag blandet sammen med svaret om køleskabet."""
    kobl_paa(1)
    telegram._gem_besked("Maja", "husk vi vil have mere fisk", husket=True)
    svar = await server.ugeplan_forloeb()
    (b,) = svar["beskeder_siden_sidst"]
    assert b["tekst"] == "husk vi vil have mere fisk" and b["gemt_i_profilen"] is True
    assert telegram.antal_ulaeste() == 0  # vent_paa_svar giver dem ikke igen som svar
    f = svar["forloeb"]
    assert "beskeder_siden_sidst" in f and "fra_husstanden" in f and "ØNSKER TIL FREMOVER" in f
    assert "Højst ét sådant spørgsmål" in f and "Nævn ikke for husstanden" in f


async def test_forloebet_dækker_ret_der_ikke_kan_koebes(tg):
    """Fundet i en test: ingen laks i Bilka passede til profilen. Retten skal skiftes ud."""
    kobl_paa(1)
    f = (await server.ugeplan_forloeb())["forloeb"]
    assert "NÅR EN RET IKKE KAN KØBES" in f and "også efter at menuen er godkendt" in f
    assert "Ændret:" in f and "EFTER \"bestil\"" in f and "ikke lade" in f


async def test_forloebet_fra_anden_test(tg):
    """Fundet i test 2: kun 6 morgener og ingen søndagsfrokost; rådyr også tirsdag."""
    kobl_paa(1)
    f = (await server.ugeplan_forloeb())["forloeb"]
    assert "HELE ugen frem til næste levering (7 dage)" in f
    assert "præcis som vi sagde" in f


async def test_forloebet_fra_tredje_test(tg):
    kobl_paa(1)
    f = (await server.ugeplan_forloeb())["forloeb"]
    assert "ERSTATTER køleskabsdelen" in f and "MEDREGNET retter af det, vi har hjemme" in f
    assert "Kød er ALTID øko eller vores eget vildt" in f


async def test_forloebet_har_basisvarelister(tg):
    """Julius: "jeg regner med, I har XYZ" er ikke godt nok. Køber/Tjek-lister i stedet."""
    kobl_paa(1)
    f = (await server.ugeplan_forloeb())["forloeb"]
    assert "Køber til ugen:" in f and "Tjek, at I har:" in f and "basisvarer_pr_type" in f
    assert "ALDRIG med kølevarer" in f and "regner med, vi HAR hjemme" not in f
