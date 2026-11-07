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
        self.html: list[str] = []  # beskeder sendt med parse_mode=HTML
        self.fastgjort: dict[int, int] = {}  # chat -> fastgjort message_id
        self.naeste_besked = 100
        self.loesnet = 0
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
            if data.get("parse_mode") == "HTML":
                self.html.append(data["text"])
            self.naeste_besked += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.naeste_besked}})
        if metode == "pinChatMessage":
            self.fastgjort[data["chat_id"]] = data["message_id"]
            return httpx.Response(200, json={"ok": True, "result": True})
        if metode == "unpinChatMessage":
            self.fastgjort.pop(data["chat_id"], None)
            self.loesnet += 1
            return httpx.Response(200, json={"ok": True, "result": True})
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
    monkeypatch.setattr(telegram, "AUTOSTART", False)  # ingen rigtige lyttere i testene
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
    # bottens egen besked gemmes som historik, så hele samtalen kan læses bagefter
    alle = telegram.beskeder(kun_nye=False)
    assert [(b["fra"], b["tekst"]) for b in alle][0] == (telegram.SENDT_AF, "Hvad har I i køleskabet?")
    # kvitteringen er kort (fundet i brug: den lange tekst var forvirrende)
    assert (1, "Modtaget.") in tg.sendt


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
    tg.besked(666, "bestil", navn="Fremmed")
    await telegram.hent_og_haandter(0)
    assert butik.kald == [] and tg.sendt == []


async def test_bestil_fra_telefonen_og_claude_ser_det(tg, monkeypatch):
    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    await server.gem_madplan("nemlig", ["Man"], [{"vare_id": "7", "navn": "x", "antal": 1, "pris": 1.0}])
    tg.besked(1, "Bestil det bare")
    await telegram.hent_og_haandter(0)
    assert butik.kald == [{"7": 1}]
    svar = await server.vent_paa_svar(1)
    assert svar[0]["fra"] == "handle-botten" and "fyldt" in svar[0]["tekst"]


async def test_rettelse_er_ikke_bestil(tg, monkeypatch):
    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    await server.gem_madplan("nemlig", ["Man"], [{"vare_id": "7", "navn": "x", "antal": 1, "pris": 1.0}])
    tg.besked(1, "ja, men uden laks")
    await telegram.hent_og_haandter(0)
    assert butik.kald == []
    assert [b["tekst"] for b in await server.vent_paa_svar(1)] == ["ja, men uden laks"]


async def test_vent_paa_svar_uden_svar(tg):
    assert await server.vent_paa_svar(1) == {"ingen_svar_endnu": True}


# --- lytteren starter selv ----------------------------------------------------------


def test_kun_en_lytter_ad_gangen(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    assert telegram.tag_laas()
    try:
        assert telegram.lytter_koerer()  # låsen er taget: en ny lytter må ikke starte
    finally:
        telegram.slip_laas()
    assert not telegram.lytter_koerer()


def test_lytteren_startes_ved_foerste_besked(tg, monkeypatch):
    startet = []
    monkeypatch.setattr(telegram, "AUTOSTART", True)
    monkeypatch.setattr("subprocess.Popen", lambda kommando, **kw: startet.append(kommando))
    assert telegram.start_lytter_i_baggrunden()
    assert startet and startet[0][-3:] == ["handle_mcp.server", "telegram", "24"]
    assert telegram.tag_laas()  # en kørende lytter ville holde låsen; her er den bare startet som falsk
    try:
        assert not telegram.start_lytter_i_baggrunden()  # kører allerede: ingen nr. to
    finally:
        telegram.slip_laas()
    assert len(startet) == 1


async def test_send_starter_lytteren(tg, monkeypatch):
    kald = []
    monkeypatch.setattr(telegram, "start_lytter_i_baggrunden", lambda: kald.append(1) or True)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    await server.send_besked("Hvad har I i køleskabet?")
    assert kald == [1]


async def test_ugen_er_afsluttet_naar_planen_er_brugt(tg, monkeypatch):
    from handle_mcp import madplan

    start = (datetime.now(telegram.TIDSZONE) - timedelta(minutes=1)).isoformat(timespec="minutes")
    assert not telegram.ugen_er_afsluttet(start)  # ingen plan endnu: bliv ved med at lytte
    p = madplan.gem("nemlig", ["Man"], [{"vare_id": "1", "navn": "x", "antal": 1, "pris": 1.0}])
    assert not telegram.ugen_er_afsluttet(start)  # planen venter på OK
    madplan.saet_status(p.kode, "kurv fyldt", "test")
    assert telegram.ugen_er_afsluttet(start)
    fremtid = (datetime.now(telegram.TIDSZONE) + timedelta(hours=1)).isoformat(timespec="minutes")
    assert not telegram.ugen_er_afsluttet(fremtid)  # gamle planer tæller ikke


# --- ønsker til fremover (fundet i brug, uge 47) -----------------------------------


@pytest.mark.parametrize(
    "tekst",
    [
        "Husk at chokoladen skal være mindst 85 %",
        "Fra nu af vil vi gerne have mere fisk",
        "vi kan ikke lide rosenkål",
        "Næste gang må kyllingen gerne være økologisk",
        "Gem det til fremover: ingen koriander",
        "Vi køber aldrig light-produkter",
    ],
)
async def test_oensker_gemmes_i_profilen_af_botten_selv(tg, tekst):
    """Uden Claude: ønsket ligger i profilen, også hvis Claude overser beskeden."""
    from handle_mcp import profil

    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, tekst, navn="Maja")
    assert (await telegram.hent_og_haandter(0))[-1] == "besked fra Maja gemt (også i profilen)"
    (linje,) = profil.hent().fra_husstanden
    assert linje.endswith(f"Maja: {tekst}")
    (b,) = await server.vis_beskeder()
    assert b["gemt_i_profilen"] is True and b["tekst"] == tekst
    assert (1, telegram.IKKE_LYTTER) in tg.sendt  # ingen snak om profilen (Claude var ikke i gang)


@pytest.mark.parametrize(
    "tekst",
    ["2 løg og en halv pakke smør", "Ikke fisk tirsdag", "Byt torsdag og fredag", "5 aftener fra søndag, gæster lørdag"],
)
async def test_almindelige_svar_gemmes_ikke_i_profilen(tg, tekst):
    from handle_mcp import profil

    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, tekst)
    await telegram.hent_og_haandter(0)
    assert profil.hent().fra_husstanden == []
    assert "gemt_i_profilen" not in (await server.vis_beskeder())[0]


async def test_samme_oenske_gemmes_kun_en_gang_og_kan_ryddes(tg):
    from handle_mcp import profil

    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, "husk mørk chokolade", navn="Maja")
    await telegram.hent_og_haandter(0)
    tg.besked(1, "husk mørk chokolade", navn="Maja")
    await telegram.hent_og_haandter(0)
    assert len(profil.hent().fra_husstanden) == 1
    # Claude flytter ønsket til det rette felt og rydder op i samme kald
    await server.opdater_profil({"varepraeferencer": ["Mørk chokolade: mindst 85 %"], "fra_husstanden": []})
    p = profil.hent()
    assert p.fra_husstanden == [] and p.varepraeferencer == ["Mørk chokolade: mindst 85 %"]


async def test_fejl_i_profilen_taber_ikke_beskeden(tg, monkeypatch):
    from handle_mcp import profil

    def gaar_galt(*a):
        raise OSError("disken er fuld")

    monkeypatch.setattr(profil, "husk_fra_husstanden", gaar_galt)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, "husk ingen koriander")
    await telegram.hent_og_haandter(0)
    assert [b["tekst"] for b in await server.vis_beskeder()] == ["husk ingen koriander"]



# --- siger botten til, når Claude ikke lytter? (fundet i demoen, uge 48) -----------------


async def test_svar_mens_claude_ikke_lytter_faar_besked_om_det(tg):
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, "vi har agurk og mel")
    await telegram.hent_og_haandter(0)
    assert tg.sendt[-1] == (1, telegram.IKKE_LYTTER) and "fortsæt" in telegram.IKKE_LYTTER


async def test_svar_mens_claude_venter_faar_kort_modtaget(tg):
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    assert await telegram.vent_paa_svar(1) == []  # Claude venter (og markerer sig som i gang)
    tg.besked(1, "vi har agurk og mel")
    await telegram.hent_og_haandter(0)
    assert tg.sendt[-1] == (1, "Modtaget.")


async def test_claude_der_er_gaaet_i_staa_regnes_ikke_som_lyttende(tg):
    import time

    par(tg, 1)
    await telegram.hent_og_haandter(0)
    skriv("claude-aktiv", {"sidst": time.time() - telegram.CLAUDE_LYTTER_SEK - 1})
    tg.besked(1, "vi har agurk og mel")
    await telegram.hent_og_haandter(0)
    assert tg.sendt[-1] == (1, telegram.IKKE_LYTTER)


@pytest.mark.parametrize(
    "tekst",
    [
        "Vi vil ikke spise fisk fra frost. Desuden vil vi ikke spise opdrættet fisk - gem i profilindstillinger",
        "Vi vil kun have rådyr om lørdagen",
        "gem i profilen at vi kun vil have vildfanget fisk",
    ],
)
def test_oensker_fra_demoen_fanges(tekst):
    from handle_mcp import profil

    assert profil.er_huskeoenske(tekst)


class ButikDerAfviserLaks(FalskButik):
    async def saet_antal(self, antal):
        self.kald.append(dict(antal))
        return [
            AntalResultat(i, n, 0 if i == "laks" else n, i != "laks", "Du kan desværre ikke bestille dette produkt til HD Odense" if i == "laks" else None)
            for i, n in antal.items()
        ]


@pytest.mark.parametrize("claude_i_gang", [True, False])
async def test_vare_der_ikke_kom_i_kurven_giver_besked_om_erstatning(tg, monkeypatch, claude_i_gang):
    """Fundet i en test: laksen kunne ikke leveres, og der stod bare "betal i appen"."""
    monkeypatch.setattr(server, "hent_butik", lambda _n: ButikDerAfviserLaks())
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    linjer = [{"vare_id": "laks", "navn": "Laks", "antal": 1, "pris": 50.0}, {"vare_id": "kart", "navn": "Kartofler", "antal": 1, "pris": 15.0}]
    await server.gem_madplan("bilka", ["Tor: Laks"], linjer)
    import time

    skriv("claude-aktiv", {"sidst": time.time() if claude_i_gang else 0})
    tg.besked(1, "bestil")
    await telegram.hent_og_haandter(0)
    sidste = next(t for _, t in reversed(tg.sendt) if "godkendte" in t)  # ugemenuen kommer efter
    assert "HD Odense" in sidste and "betal i appen" not in sidste and "Vent med at betale" in sidste
    assert (telegram.ERSTATNING_LYTTER in sidste) is claude_i_gang
    assert (telegram.ERSTATNING_IKKE_LYTTER in sidste) is not claude_i_gang


async def test_bestil_lige_efter_en_rettelse_fylder_ikke_den_gamle_plan(tg, monkeypatch):
    """Fundet i test 3: "Jeg køber selv kødet" + "Bestil". Den gamle plan (med kødet) må ikke
    fyldes; bestil gælder den rettede plan, når Claude gemmer den, hvis den ikke er dyrere."""
    import time

    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    linjer = [{"vare_id": "kod", "navn": "Hakket kød", "antal": 1, "pris": 60.0}, {"vare_id": "kart", "navn": "Kartofler", "antal": 1, "pris": 15.0}]
    gammel = await server.gem_madplan("nemlig", ["Ons: Frikadeller"], linjer)
    skriv("claude-aktiv", {"sidst": time.time()})
    tg.besked(1, "Jeg handler selv øko kød til frikadeller i Brugsen")
    tg.besked(1, "Bestil")
    await telegram.hent_og_haandter(0)
    assert butik.kald == []  # intet i kurven endnu
    assert tg.sendt[-1] == (1, telegram.BESTIL_EFTER_RETTELSE)
    assert [b["tekst"] for b in await server.vis_beskeder()][-1] == "Bestil"  # Claude ser det
    ny = await server.gem_madplan("nemlig", ["Ons: Frikadeller (eget kød)"], linjer[1:])
    assert ny["kode"] != gammel["kode"] and f'fyld_kurv_fra_madplan("{ny["kode"]}", "bestil")' in ny["bestil_venter"]
    svar = await server.fyld_kurv_fra_madplan(ny["kode"], "bestil")
    assert butik.kald == [{"kart": 1}] and svar["lagt_i_kurven"]
    # og det gælder kun én gang
    assert "bestil_venter" not in await server.gem_madplan("nemlig", ["x"], linjer[1:])


async def test_bestil_efter_rettelse_men_dyrere_plan_kraever_nyt_bestil(tg, monkeypatch):
    import time

    monkeypatch.setattr(server, "hent_butik", lambda _n: FalskButik())
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    await server.gem_madplan("nemlig", ["Ons"], [{"vare_id": "a", "navn": "a", "antal": 1, "pris": 50.0}])
    skriv("claude-aktiv", {"sidst": time.time()})
    tg.besked(1, "Tilføj laks")
    tg.besked(1, "bestil")
    await telegram.hent_og_haandter(0)
    ny = await server.gem_madplan("nemlig", ["Ons"], [{"vare_id": "a", "navn": "a", "antal": 1, "pris": 50.0}, {"vare_id": "l", "navn": "laks", "antal": 1, "pris": 90.0}])
    assert "bed om et nyt bestil" in ny["bestil_venter"]



# --- ugemenuen med opskrifterne (Julius: "nemt at få fat i opskriften om onsdagen") ----------


async def test_ugemenu_sendes_og_fastgoeres_naar_kurven_fyldes(tg, monkeypatch):
    butik = FalskButik()
    monkeypatch.setattr(server, "hent_butik", lambda _n: butik)
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    links = {"Tir: Nakkekoteletter": "https://www.valdemarsro.dk/nakkekoteletter/", "Ons: Frikadeller": "https://www.dr.dk/mad/opskrift/frikadeller"}
    await server.gem_madplan(
        "nemlig",
        ["Man: Rester af chili", "Tir: Nakkekoteletter med kartofler", "Ons: Frikadeller <med> rødbeder"],
        [{"vare_id": "1", "navn": "x", "antal": 1, "pris": 1.0}],
        valg={"opskrifter": links},
    )
    tg.besked(1, "bestil")
    await telegram.hent_og_haandter(0)
    (menu,) = tg.html
    # rettens navn er linket (kort besked), rester har intet link, og HTML-tegn er escapet
    assert '<a href="https://www.valdemarsro.dk/nakkekoteletter/">Tir: Nakkekoteletter med kartofler</a>' in menu
    assert '<a href="https://www.dr.dk/mad/opskrift/frikadeller">Ons: Frikadeller &lt;med&gt; rødbeder</a>' in menu
    assert "\nMan: Rester af chili\n" in menu and "https://" not in menu.split("Man:")[1].split("\n")[0]
    assert 1 in tg.fastgjort
    assert not await telegram.send_ugemenu_hvis_mangler()  # kun én gang pr. plan


async def test_ny_ugemenu_loesner_den_gamle(tg, monkeypatch):
    monkeypatch.setattr(server, "hent_butik", lambda _n: FalskButik())
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    for uge in ("Man: Lasagne", "Man: Tacos"):
        await server.gem_madplan("nemlig", [uge], [{"vare_id": "1", "navn": "x", "antal": 1, "pris": 1.0}])
        tg.besked(1, "bestil")
        await telegram.hent_og_haandter(0)
    assert len(tg.html) == 2 and "Tacos" in tg.html[-1]
    assert tg.loesnet == 1 and list(tg.fastgjort) == [1]  # den gamle menu blev løsnet, den nye fastgjort


@pytest.mark.parametrize("tekst", ["menu", "Menu?", "ugens menu", "opskrift"])
async def test_menu_sender_ugemenuen_igen(tg, monkeypatch, tekst):
    monkeypatch.setattr(server, "hent_butik", lambda _n: FalskButik())
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    await server.gem_madplan("nemlig", ["Ons: Frikadeller"], [{"vare_id": "1", "navn": "x", "antal": 1, "pris": 1.0}])
    tg.besked(1, "bestil")
    await telegram.hent_og_haandter(0)
    tg.besked(1, tekst)
    assert (await telegram.hent_og_haandter(0))[-1] == "ugemenu sendt til Maria"
    assert len(tg.html) == 2


async def test_menu_uden_madplan(tg):
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    tg.besked(1, "menu")
    await telegram.hent_og_haandter(0)
    assert tg.sendt[-1] == (1, "Der er ingen madplan i kurven lige nu.")


async def test_ugemenu_naar_claude_selv_fyldte_kurven(tg, monkeypatch):
    """Fx "bestil" lige efter en rettelse: Claude fylder kurven, og lytteren sender menuen."""
    monkeypatch.setattr(server, "hent_butik", lambda _n: FalskButik())
    par(tg, 1)
    await telegram.hent_og_haandter(0)
    kode = (await server.gem_madplan("nemlig", ["Ons: Frikadeller"], [{"vare_id": "1", "navn": "x", "antal": 1, "pris": 1.0}]))["kode"]
    await server.fyld_kurv_fra_madplan(kode, f"OK {kode}")
    assert await telegram.send_ugemenu_hvis_mangler() and len(tg.html) == 1
