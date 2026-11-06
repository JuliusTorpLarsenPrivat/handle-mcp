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
    assert "køleskab" in tg[0][1] and "aftener" in tg[0][1]
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
