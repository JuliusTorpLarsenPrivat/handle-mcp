"""Telegram: jeres egen bot, så I kan svare fra telefonen (gratis, ingen AI-udgifter).

Claude Desktop laver stadig al tænkningen. Botten er bare postbudet:
  - Claude sender spørgsmål og ugeplan til jer med send_besked.
  - Jeres svar gemmes lokalt, og Claude læser dem med vis_beskeder.
  - Lytteren (`handle-mcp telegram`, kører i en terminal) svarer selv på "OK UGE-xxxx": den
    fylder kurven med præcis den godkendte plan og siger til, når I kan betale.

Sikkerhed:
  - Botnøglen ligger i Windows' nøglering. Den står i Telegrams adresser, så fejlbeskeder
    renses for den, før de vises eller logges.
  - Kun telefoner, der er koblet på med en parringskode, kan skrive med botten. Alle andre
    ignoreres.
  - Botten bestiller og betaler aldrig.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import keyring
import keyring.errors

from handle_mcp import madplan
from handle_mcp.lokal import laes, skriv
from handle_mcp.login import TJENESTE
from handle_mcp.vagt import VagtFejl, ny_klient, send

log = logging.getLogger(__name__)
TIDSZONE = ZoneInfo("Europe/Copenhagen")
NOEGLE_NAVN = "telegram-bot"
TOKEN_FORMAT = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")
MAKS_CHATS = 4  # jer to, plus lidt luft
MAKS_BESKEDER = 200
PARRING_GYLDIG = timedelta(minutes=30)
MAKS_TEGN = 4000  # Telegrams grænse er 4096

_transport: httpx.AsyncBaseTransport | None = None  # kun til test


class TelegramFejl(Exception):
    pass


# --- nøgle -------------------------------------------------------------------


def hent_token() -> str:
    try:
        token = keyring.get_password(TJENESTE, NOEGLE_NAVN)
    except keyring.errors.KeyringError:
        token = None
    if not token:
        raise TelegramFejl("Telegram er ikke sat op. Kør i en terminal: handle-mcp telegram-opsaet")
    return token


def gem_token(token: str) -> None:
    token = token.strip()
    if not TOKEN_FORMAT.match(token):
        raise TelegramFejl("Det ligner ikke en botnøgle fra @BotFather (fx 123456789:AA…).")
    keyring.set_password(TJENESTE, NOEGLE_NAVN, token)


def _rens(tekst: str, token: str | None) -> str:
    """Fjern botnøglen fra en tekst (fejlbeskeder fra httpx indeholder adressen)."""
    return tekst.replace(token, "<botnøgle>") if token else tekst


# --- Telegrams API ---------------------------------------------------------------


async def api(metode: str, token: str | None = None, vent: float = 0, **params) -> object:
    """Kald Telegram. `vent` er hvor længe Telegram må holde forbindelsen åben (getUpdates)."""
    token = token or hent_token()
    url = f"https://api.telegram.org/bot{token}/{metode}"
    try:
        async with ny_klient(timeout=vent + 20, transport=_transport) as klient:
            svar = await send(klient, "POST", url, json=params)
        data = svar.json()
    except (httpx.HTTPError, ValueError, VagtFejl) as fejl:
        raise TelegramFejl(f"Telegram svarede ikke: {_rens(str(fejl), token)}") from None
    if not data.get("ok"):
        raise TelegramFejl(f"Telegram afviste {metode}: {_rens(str(data.get('description')), token)}")
    return data.get("result")


async def bot_navn(token: str | None = None) -> str:
    mig = await api("getMe", token=token)
    return "@" + str(mig.get("username"))


# --- tilkoblede telefoner (lokalt i telegram.json) ------------------------------


def _tilstand() -> dict:
    d = laes("telegram")
    d.setdefault("chats", {})
    d.setdefault("offset", 0)
    return d


def chats() -> dict[str, str]:
    """Tilkoblede chats: {chat_id: navn}."""
    return dict(_tilstand()["chats"])


def ny_parringskode() -> str:
    """En kode, som en telefon sender til botten for at blive koblet på. Gyldig i 30 minutter."""
    d = _tilstand()
    kode = f"{secrets.randbelow(900000) + 100000}"
    d["parring"] = {"kode": kode, "udloeber": (datetime.now(TIDSZONE) + PARRING_GYLDIG).isoformat()}
    skriv("telegram", d)
    return kode


def _proev_parring(d: dict, chat_id: str, navn: str, tekst: str) -> bool:
    p = d.get("parring")
    if not p or datetime.fromisoformat(p["udloeber"]) < datetime.now(TIDSZONE):
        return False
    if p["kode"] not in re.findall(r"\d{6}", tekst) or len(d["chats"]) >= MAKS_CHATS:
        return False
    d["chats"][chat_id] = navn
    return True


def frakobl_alle() -> int:
    d = _tilstand()
    n = len(d["chats"])
    d["chats"] = {}
    d.pop("parring", None)
    skriv("telegram", d)
    return n


# --- beskeder (lokalt i beskeder.json) ------------------------------------------


SENDT_AF = "handle (sendt)"  # bottens egne beskeder: gemt som historik, aldrig som "nye svar"


def _gem_besked(fra: str, tekst: str, laest: bool = False) -> None:
    d = laes("beskeder")
    alle = d.get("beskeder", [])
    alle.append({"tid": datetime.now(TIDSZONE).isoformat(timespec="minutes"), "fra": fra, "tekst": tekst[:MAKS_TEGN], "laest": laest})
    skriv("beskeder", {"beskeder": alle[-MAKS_BESKEDER:]})


def antal_ulaeste() -> int:
    return sum(1 for b in laes("beskeder").get("beskeder", []) if not b.get("laest"))


async def vent_paa_svar(sekunder: float = 50) -> list[dict]:
    """Vent på nye svar (højst `sekunder`). Lytteren henter dem fra Telegram i baggrunden."""
    import asyncio

    slut = asyncio.get_running_loop().time() + max(1.0, min(sekunder, 55))
    while asyncio.get_running_loop().time() < slut:
        if antal_ulaeste():
            return beskeder(kun_nye=True)
        await asyncio.sleep(2)
    return []


def beskeder(kun_nye: bool = True, marker_laest: bool = True) -> list[dict]:
    d = laes("beskeder")
    alle = d.get("beskeder", [])
    # "modtaget", ikke "tid": persondatafilteret fjerner felter, der hedder "tid" (Bilkas bruger-id)
    ud = [{"modtaget": b["tid"], "fra": b["fra"], "tekst": b["tekst"]} for b in alle if not (kun_nye and b.get("laest"))]
    if marker_laest and any(not b.get("laest") for b in alle):
        for b in alle:
            b["laest"] = True
        skriv("beskeder", {"beskeder": alle})
    return ud


# --- sende og lytte --------------------------------------------------------------


# --- lytteren starter selv ---------------------------------------------------------

AUTOSTART = True  # slås fra i testene
LAAS = "telegram-lytter.lock"
# Holdes åben, så længe denne proces er lytteren. globals().get: en genindlæsning af modulet
# (genindlaes.py) må ikke glemme låsen, ellers kan en lytter nr. to starte.
_laas_fil = globals().get("_laas_fil")


def tag_laas() -> bool:
    """Bliv den eneste lytter. False hvis en anden lytter allerede kører."""
    global _laas_fil
    from handle_mcp.lokal import mappe

    mappe().mkdir(parents=True, exist_ok=True)
    f = open(mappe() / LAAS, "a+")  # noqa: SIM115  skal holdes åben
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _laas_fil = f
    return True


def slip_laas() -> None:
    global _laas_fil
    if _laas_fil:
        _laas_fil.close()
        _laas_fil = None


def lytter_koerer() -> bool:
    if tag_laas():
        slip_laas()
        return False
    return True


def start_lytter_i_baggrunden(timer: float = 24) -> bool:
    """Start lytteren som et selvstændigt program uden vindue, hvis den ikke kører.
    Den skriver til telegram-lytter.log og stopper selv, når ugen er afsluttet."""
    if not AUTOSTART or lytter_koerer():
        return False
    import subprocess

    from handle_mcp.lokal import mappe

    log_fil = open(mappe() / "telegram-lytter.log", "a", encoding="utf-8")  # noqa: SIM115
    kommando = [sys.executable, "-m", "handle_mcp.server", "telegram", f"{timer:g}"]
    flag = 0
    if sys.platform == "win32":
        # Selvstændig proces uden vindue. "Breakaway" lader den leve videre, hvis Claude Desktop lukkes.
        flag = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | 0x01000000
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        subprocess.Popen(kommando, stdout=log_fil, stderr=log_fil, stdin=subprocess.DEVNULL, creationflags=flag, env=env, close_fds=True)
    except OSError:  # Claude Desktop tillader ikke "breakaway": lytteren lever så, så længe appen kører
        subprocess.Popen(kommando, stdout=log_fil, stderr=log_fil, stdin=subprocess.DEVNULL, creationflags=flag & ~0x01000000, env=env, close_fds=True)
    log.warning("Telegram-lytteren er startet i baggrunden.")
    return True


def ugen_er_afsluttet(siden: str) -> bool:
    """Er der lavet en plan, efter lytteren startede, og er den ikke længere afventende?"""
    try:
        planer = madplan._alle()
    except Exception:  # noqa: BLE001
        return False
    nye = [p for p in planer if p.oprettet >= siden]
    return bool(nye) and all(p.status != "afventer" for p in nye)


async def send_til_alle(tekst: str) -> int:
    tekst = tekst.strip()
    if not tekst:
        raise TelegramFejl("Beskeden er tom.")
    if len(tekst) > MAKS_TEGN:
        raise TelegramFejl(f"Beskeden er for lang (højst {MAKS_TEGN} tegn).")
    modtagere = chats()
    if not modtagere:
        raise TelegramFejl("Ingen telefoner er koblet på botten endnu. Kør: handle-mcp telegram-opsaet")
    for chat_id in modtagere:
        await api("sendMessage", chat_id=int(chat_id), text=tekst)
    _gem_besked(SENDT_AF, tekst, laest=True)  # så hele samtalen kan læses bagefter
    try:
        start_lytter_i_baggrunden()  # så jeres svar bliver hentet, uden at nogen skal huske det
    except OSError as fejl:
        log.warning("Kunne ikke starte lytteren: %s", fejl)
    return len(modtagere)


async def _svar(chat_id: str, tekst: str) -> None:
    try:
        await api("sendMessage", chat_id=int(chat_id), text=tekst[:MAKS_TEGN])
    except TelegramFejl as fejl:
        log.warning("Kunne ikke svare: %s", fejl)


async def _godkendelse(chat_id: str, navn: str, tekst: str) -> bool:
    """Er beskeden et OK til en ventende plan? Så fyld kurven og svar. Ellers False."""
    koder = {f"UGE-{k}" for k in madplan.KODE_I_TEKST.findall(tekst.upper())}
    if len(koder) == 1:
        kode = next(iter(koder))
    elif not koder and madplan.er_kort_ja(tekst) and (ventende := madplan.eneste_ventende()):
        kode = ventende.kode  # "bestil" gælder den eneste plan, der venter
    else:
        return False
    if not madplan.er_godkendt(kode, tekst):
        return False
    from mcp.server.mcpserver.exceptions import ToolError

    from handle_mcp import server  # først her: serveren importerer selv dette modul

    try:
        svar = await server.fyld_kurv_fra_madplan(kode, tekst)
    except ToolError as fejl:
        await _svar(chat_id, f"Kurven blev IKKE fyldt: {fejl}")
        _gem_besked("handle-botten", f"{navn} prøvede at godkende {kode}, men kurven blev IKKE fyldt: {fejl}")
        return True
    antal = len(svar.get("lagt_i_kurven") or [])
    flere = " + " in svar["butik"]
    besked = f"{navn} godkendte {kode}. Kurven{'e' if flere else ''} hos {svar['butik']} er fyldt med {antal} varer."
    if svar.get("advarsler"):
        besked += "\nObs:\n- " + "\n- ".join(svar["advarsler"])
    besked += "\nVælg leveringstid og betal i appen."
    for anden in chats():
        await _svar(anden, besked)
    _gem_besked("handle-botten", besked)  # så Claude kan se, at kurven er fyldt
    return True


async def haandter(opdatering: dict) -> str | None:
    """Én besked fra Telegram. Giver en kort logtekst (uden beskedens indhold)."""
    besked = opdatering.get("message") or opdatering.get("edited_message") or {}
    tekst = (besked.get("text") or "").strip()
    chat_id = str((besked.get("chat") or {}).get("id", ""))
    navn = (besked.get("from") or {}).get("first_name") or "ukendt"
    if not tekst or not chat_id:
        return None
    d = _tilstand()
    if chat_id not in d["chats"]:
        if _proev_parring(d, chat_id, navn, tekst):
            skriv("telegram", d)
            await _svar(chat_id, f"Hej {navn}. Du er koblet på handle-botten. Du får ugeplanen her og kan svare med OK + koden.")
            return f"{navn} koblet på"
        return "besked fra ukendt chat ignoreret"
    if await _godkendelse(chat_id, navn, tekst):
        return f"godkendelse fra {navn} behandlet"
    _gem_besked(navn, tekst)
    await _svar(chat_id, "Modtaget.")  # kort: Claude arbejder typisk på det lige nu
    return f"besked fra {navn} gemt"


async def hent_og_haandter(vent_sekunder: int = 25) -> list[str]:
    """Hent nye beskeder (venter op til `vent_sekunder` på nye) og håndter dem."""
    d = _tilstand()
    opdateringer = await api(
        "getUpdates", vent=vent_sekunder, timeout=vent_sekunder, offset=d["offset"], allowed_updates=["message", "edited_message"]
    )
    haendelser = []
    for o in opdateringer or []:
        d = _tilstand()
        d["offset"] = max(d["offset"], int(o["update_id"]) + 1)
        skriv("telegram", d)  # gemmes før håndtering: en besked håndteres aldrig to gange
        if h := await haandter(o):
            haendelser.append(h)
    return haendelser
