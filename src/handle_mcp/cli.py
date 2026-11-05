"""Kommandoer i terminalen:

    handle-mcp login nemlig    spørger om email og kode, tjekker dem hos butikken og gemmer dem
    handle-mcp logout nemlig   sletter det gemte login
    handle-mcp status          viser hvilke butikker der har et gemt login (aldrig selve koden)
    handle-mcp http-noegle     laver en ny adgangsnøgle til HTTP-tilstand (vises kun én gang)
    handle-mcp http [host] [port]
                               kører serveren over HTTP i stedet for til Claude Desktop.
                               Standard: 127.0.0.1 8765 (kun denne maskine)
"""

from __future__ import annotations

import asyncio
import getpass

from handle_mcp import login
from handle_mcp.butikker import hent_butik

HJAELP = __doc__


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
