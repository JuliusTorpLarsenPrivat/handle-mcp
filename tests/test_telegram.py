"""Telegram-botten testet mod en falsk Telegram (ingen rigtige beskeder sendes)."""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from handle_mcp import server, telegram
from handle_mcp.data import AntalResultat, Kurv, Kurvlinje
from handle_mcp.lokal import laes, skriv

TOKEN = "123456789:" + "A" * 35


class FalskTelegram:
    """Svarer som api.telegram.org og husker, hvad botten sendte."""

    def __init__(self):
        self.sendt: list[tuple[int, str]] = []
        self.ventende: list[dict] = []
        self.naeste_id = 1

    def besked(self, chat_id: int, tekst: str, navn: str = "Maria"):
        self.ventende.append(
            {"update_id": self.naeste_id, "message": {"text": tekst, "chat": {"id": chat_id}, "from": {"first_name": navn}}}
        )
        self.naeste_id += 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.telegram.org"
        metode = request.url.path.rsplit("/", 1)[-1]
        data = json.loads(request.content or b"{}")
        if metode == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "vores_handlebot"}})
        if metode == "sendMessage":
            self.sendt.append((data["chat_id"], data["text"]))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if metode == "getUpdates":
            nye = [u for u in self.ventende if u["update_id"] >= data.get("offset", 0)]
            return httpx.Response(200, json={"ok": True, "result": nye})
        return httpx.Response(404, json={"ok": False, "description": "ukendt"})


@pytest.fixture
def tg(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    falsk = FalskTelegram()
    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(falsk))
    monkeypatch.setattr(telegram, "hent_token", lambda: TOKEN)
    return falsk


def par(tg, *chat_ids):
    kode = telegram.ny_parringskode()
    for c in chat_ids:
        tg.besked(c, f"/start {kode}", navn=f"person{c}")


# --- nøgle og sikkerhed ---------------------------------------------------------


@pytest.mark.parametrize("token", ["", "abc", "123:kort", "123456789:" + "A" * 35 + " ; rm"])
def test_forkert_botnoegle_afvises(token, monkeypatch):
    monkeypatch.setattr(telegram.keyring, "set_password", lambda *a: pytest.fail("må ikke gemmes"))
    with pytest.raises(telegram.TelegramFejl):
        telegram.gem_token(token)


async def test_botnoeglen_vises_aldrig_i_fejl(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))

    def nede(request):
        raise httpx.ConnectError(f"kunne ikke forbinde til {request.url}")

    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(nede))
    with pytest.raises(telegram.TelegramFejl) as fejl:
        await telegram.api("getMe", token=TOKEN)
    assert TOKEN not in str(fejl.value) and "<botnøgle>" in str(fejl.value)


def test_telegram_er_eneste_nye_adresse():
    import httpx as h

    from handle_mcp.vagt import VagtFejl, tjek_url

    tjek_url(h.URL(f"https://api.telegram.org/bot{TOKEN}/sendMessage"))
    with pytest.raises(VagtFejl):
        tjek_url(h.URL("https://api.telegram.org.evil.dk/bot1/sendMessage"))


# --- parring --------------------------------------------------------------------


async def test_parring_og_fremmede_ignoreres(tg):
    tg.besked(999, "hej, hvem er du?", navn="Fremmed")
    par(tg, 1, 2)
    tg.besked(3, "123456", navn="Gætter")  # forkert kode
    haendelser = await telegram.hent_og_haandter(0)
    assert haendelser == ["besked fra ukendt chat ignoreret", "person1 koblet på", "person2 koblet på", "besked fra ukendt chat ignoreret"]
    assert telegram.chats() == {"1": "person1", "2": "person2"}
    assert {c for c, _ in tg.sendt} == {1, 2}  # fremmede får intet svar


async def test_udloebet_parringskode_virker_ikke(tg):
    kode = telegram.ny_parringskode()
    d = laes("telegram")
    d["parring"]["udloeber"] = (datetime.now(telegram.TIDSZONE) - timedelta(minutes=1)).isoformat()
    skriv("telegram", d)
    tg.besked(1, kode)
    await telegram.hent_og_haandter(0)
    assert telegram.chats() == {}


async def test_samme_besked_haandteres_kun_en_gang(tg):
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, "Vi har 2 løg")
    await telegram.hent_og_haandter(0)
    await telegram.hent_og_haandter(0)  # Telegram ville sende den igen uden offset
    assert [b["tekst"] for b in telegram.beskeder(kun_nye=False)] == ["Vi har 2 løg"]


# --- beskeder og send ------------------------------------------------------------


async def test_send_kraever_tilkoblede(tg):
    with pytest.raises(ToolError, match="Ingen telefoner"):
        await server.send_besked("hej")


async def test_svar_gemmes_og_laeses_af_claude(tg):
    par(tg, 1, 2)
    await telegram.hent_og_haandter(0)
    assert (await server.send_besked("Hvad har I i køleskabet?")) == {"sendt_til": 2}
    tg.besked(1, "2 løg og en halv pakke smør", navn="person1")
    await telegram.hent_og_haandter(0)
    nye = await server.vis_beskeder()
    assert [(b["fra"], b["tekst"]) for b in nye] == [("person1", "2 løg og en halv pakke smør")]
    # fundet live: et felt kaldet "tid" blev fjernet af persondatafilteret
    assert nye[0]["modtaget"].startswith("20") and "[fjernet]" not in str(nye)
    assert await server.vis_beskeder() == []  # nu læst


# --- godkendelse fra telefonen ----------------------------------------------------


class FalskButik:
    def __init__(self):
        self.kald = []

    async def kurv(self):
        return Kurv("nemlig", [Kurvlinje(i, i, n, Decimal(10) * n) for c in self.kald for i, n in c.items()], Decimal(0), {}, None)

    async def saet_antal(self, antal):
        self.kald.append(dict(antal))
        return [AntalResultat(i, n, n, True) for i, n in antal.items()]


async def test_ok_fra_telefonen_fylder_kurven(tg, monkeypatch):
    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    par(tg, 1, 2)
    await telegram.hent_og_haandter(0)
    plan = await server.gem_madplan("nemlig", ["Man: Laks"], [{"vare_id": "111", "navn": "Laks", "antal": 1, "pris": 10.0}])
    tg.besked(2, f"ok {plan['kode']}", navn="person2")
    haendelser = await telegram.hent_og_haandter(0)
    assert haendelser == ["godkendelse fra person2 behandlet"]
    assert butik.kald == [{"111": 1}]
    beskeder_om_kurv = [t for _, t in tg.sendt if "fyldt" in t]
    assert len(beskeder_om_kurv) == 2 and "betal i appen" in beskeder_om_kurv[0]  # begge får besked
    tg.besked(1, f"OK {plan['kode']}")  # anden godkendelse: planen er brugt
    await telegram.hent_og_haandter(0)
    assert butik.kald == [{"111": 1}] and "IKKE fyldt" in tg.sendt[-1][1]


async def test_ok_med_forkert_kode_fylder_intet(tg, monkeypatch):
    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, "OK UGE-0000")
    await telegram.hent_og_haandter(0)
    assert butik.kald == [] and "IKKE fyldt" in tg.sendt[-1][1]


async def test_fremmed_kan_ikke_godkende(tg, monkeypatch):
    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    plan = await server.gem_madplan("nemlig", ["Man"], [{"vare_id": "1", "navn": "x", "antal": 1, "pris": 1.0}])
    tg.besked(666, f"OK {plan['kode']}", navn="Fremmed")
    await telegram.hent_og_haandter(0)
    assert butik.kald == [] and tg.sendt == []
