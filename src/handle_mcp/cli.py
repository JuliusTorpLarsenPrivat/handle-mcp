"""Kommandoer i terminalen:

    handle-mcp login nemlig    spørger om email og kode, tjekker dem hos butikken og gemmer dem
    handle-mcp logout nemlig   sletter det gemte login
    handle-mcp status          viser hvilke butikker der har et gemt login (aldrig selve koden)
    handle-mcp telegram-opsaet gemmer jeres Telegram-bot og giver en kode til at koble telefoner på
    handle-mcp telegram [timer] lytter efter jeres svar (standard 24 timer). "OK UGE-xxxx" fylder kurven
    handle-mcp telegram-par    ny kode til at koble en telefon på
    handle-mcp telegram-frakobl kobler alle telefoner fra
    handle-mcp http-noegle     laver en ny adgangsnøgle til HTTP-tilstand (vises kun én gang)
    handle-mcp http [host] [port]
                               kører serveren over HTTP i stedet for til Claude Desktop.
                               Standard: 127.0.0.1 8765 (kun denne maskine)
"""

from __future__ import annotations

import asyncio
import getpass
import re

from handle_mcp import login
from handle_mcp.butikker import hent_butik

HJAELP = __doc__


def _udklipsholder() -> str:
    """Teksten i Windows' udklipsholder (uden at vise den)."""
    import subprocess

    svar = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, encoding="utf-8")
    return svar.stdout or ""


async def _telegram_opsaet(fra_udklipsholder: bool = False) -> int:
    from handle_mcp import telegram

    if fra_udklipsholder:
        raa = _udklipsholder()
    else:
        print("1. Åbn Telegram, skriv til @BotFather, send /newbot og følg trinene.")
        print("2. Indsæt den botnøgle, du får (den vises ikke, mens du skriver), og tryk Enter.")
        print("   Driller det? Kopiér nøglen og kør i stedet: handle-mcp telegram-opsaet udklipsholder")
        raa = getpass.getpass("Botnøgle: ")
    # Find selve nøglen, så ekstra tegn fra indsættelsen ikke ødelægger den
    fund = re.findall(r"\d{5,15}:[A-Za-z0-9_-]{30,50}", raa)
    if len(fund) != 1:
        hvad = "ingen nøgle" if not fund else f"{len(fund)} nøgler"
        print(f"Det, der blev indsat ({len(raa.strip())} tegn), indeholder {hvad}. Intet er gemt. Prøv igen.")
        return 1
    token = fund[0]
    print(f"Modtog nøgle til bot nr. {token.split(':')[0]} ({len(token)} tegn). Tjekker den hos Telegram …")
    try:
        navn = await telegram.bot_navn(token)
        telegram.gem_token(token)
    except telegram.TelegramFejl as fejl:
        print(f"Det virkede ikke, og intet er gemt: {fejl}")
        return 1
    kode = telegram.ny_parringskode()
    print(f"\nBotten {navn} er gemt i Windows' nøglering.")
    print(f"3. Start lytteren: handle-mcp telegram")
    print(f"4. Søg {navn} frem i Telegram på begge telefoner og send koden {kode} (gyldig i 30 minutter).")
    return 0


async def _telegram_lyt(timer: float) -> int:
    """Lyt efter beskeder i `timer` timer. Stop med Ctrl+C."""
    import time

    from handle_mcp import telegram

    try:
        navn = await telegram.bot_navn()
    except telegram.TelegramFejl as fejl:
        print(fejl)
        return 1
    print(f"Lytter efter beskeder til {navn} i {timer:g} timer. Stop med Ctrl+C.")
    print(f"Tilkoblede: {', '.join(telegram.chats().values()) or 'ingen endnu'}")
    slut = time.monotonic() + timer * 3600
    fejl_i_traek = 0
    while time.monotonic() < slut:
        try:
            for h in await telegram.hent_og_haandter(vent_sekunder=25):
                print(f"{time.strftime('%H:%M')} {h}", flush=True)
            fejl_i_traek = 0
        except telegram.TelegramFejl as fejl:
            fejl_i_traek += 1
            print(f"{time.strftime('%H:%M')} {fejl}", flush=True)
            await asyncio.sleep(min(300, 10 * fejl_i_traek))  # nettet er væk: vent længere og længere
    print("Tiden er gået. Lytteren stopper.")
    return 0


def kommando(args: list[str]) -> int:
    match args:
        case ["login", butik] if butik in login.BUTIKKER:
            email = input(f"Email til {butik}: ").strip()
            kode = getpass.getpass("Kode (vises ikke): ")
            if not email or not kode:
                print("Både email og kode skal udfyldes.")
                return 1
            login.gem(butik, email, kode)
            try:
                asyncio.run(hent_butik(butik).log_ind())
            except Exception as fejl:  # noqa: BLE001  forkert login skal ikke blive liggende
                login.slet(butik)
                print(f"Login virkede ikke, så intet er gemt: {fejl}")
                return 1
            print(f"Login til {butik} virker og er gemt i styresystemets nøglering.")
            return 0
        case ["logout", butik] if butik in login.BUTIKKER:
            print("Slettet." if login.slet(butik) else "Der var intet gemt login.")
            return 0
        case ["status"]:
            for butik in login.BUTIKKER:
                try:
                    email, _ = login.hent(butik)
                    print(f"{butik}: gemt ({email})")
                except login.IntetLogin:
                    print(f"{butik}: intet login")
            return 0
        case ["telegram-opsaet"]:
            return asyncio.run(_telegram_opsaet())
        case ["telegram-opsaet", "udklipsholder"]:
            return asyncio.run(_telegram_opsaet(fra_udklipsholder=True))
        case ["telegram-par"]:
            from handle_mcp import telegram

            print(f"Ny parringskode (gyldig i 30 minutter): {telegram.ny_parringskode()}")
            print("Send koden til botten fra den telefon, der skal kobles på.")
            return 0
        case ["telegram-frakobl"]:
            from handle_mcp import telegram

            print(f"{telegram.frakobl_alle()} telefoner er koblet fra. Kør telegram-par for at koble på igen.")
            return 0
        case ["telegram", *rest] if len(rest) <= 1:
            try:
                timer = float(rest[0]) if rest else 24.0
            except ValueError:
                print("Antal timer skal være et tal, fx: handle-mcp telegram 12")
                return 1
            return asyncio.run(_telegram_lyt(timer))
        case ["http-noegle"]:
            from handle_mcp import netvaerk

            print("Ny adgangsnøgle (den gamle virker ikke længere). Den vises kun nu:")
            print(netvaerk.ny_noegle())
            return 0
        case ["http", *rest] if len(rest) <= 2:
            from handle_mcp import netvaerk
            from handle_mcp.server import mcp

            host = rest[0] if rest else "127.0.0.1"
            try:
                port = int(rest[1]) if len(rest) == 2 else 8765
            except ValueError:
                print("Porten skal være et tal, fx: handle-mcp http 127.0.0.1 8765")
                return 1
            return netvaerk.koer(mcp, host, port)
        case _:
            print(HJAELP)
            return 1
