"""Ny kode uden at genstarte Claude Desktop.

Claude Desktop starter serveren én gang og holder den kørende. Før hvert værktøjskald tjekker
vi derfor, om nogen af kodefilerne er ændret siden sidst. Er de det, indlæses de igen i den
rækkefølge, de afhænger af hinanden, så rettelser i fx varevalget virker med det samme.

Det dækker ændringer i logikken. Nye værktøjer, nye felter og ændringer i server.py selv
kræver stadig en genstart: Claude Desktop læser listen over værktøjer, når serveren starter.

Går indlæsningen af ny kode galt (fx en tastefejl), kører den gamle kode videre, og fejlen
vises én gang. Næste kald prøver igen.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Rækkefølgen betyder noget: et modul skal indlæses efter dem, det importerer fra.
# server.py, cli.py og netvaerk.py er ikke med: de kører selve serveren.
RAEKKEFOLGE = (
    "handle_mcp.lokal",
    "handle_mcp.data",
    "handle_mcp.persondata",
    "handle_mcp.vagt",
    "handle_mcp.login",
    "handle_mcp.maengde",
    "handle_mcp.opskrifter_web",
    "handle_mcp.butikker",
    "handle_mcp.butikker.nemlig",
    "handle_mcp.butikker.bilka",
    "handle_mcp.sammenlign",
    "handle_mcp.historik",
    "handle_mcp.beholdning",
    "handle_mcp.samling",
    "handle_mcp.profil",
    "handle_mcp.madplan",
    "handle_mcp.telegram",
)
MAPPE = Path(__file__).parent
SERVER_FIL = MAPPE / "server.py"

_kendt: dict[str, float] = {}
_server_start: float | None = None


def _fil(modul: str) -> Path | None:
    m = sys.modules.get(modul)
    f = getattr(m, "__file__", None)
    return Path(f) if f else None


def _tider() -> dict[str, float]:
    tider = {}
    for modul in RAEKKEFOLGE:
        if (f := _fil(modul)) and f.exists():
            tider[modul] = f.stat().st_mtime
    return tider


def start() -> None:
    """Husk filernes tidsstempler, når serveren starter."""
    global _server_start
    _kendt.clear()
    _kendt.update(_tider())
    _server_start = SERVER_FIL.stat().st_mtime if SERVER_FIL.exists() else None


def tjek() -> list[str]:
    """Indlæs ændrede moduler igen. Giver navnene på de genindlæste moduler ([] hvis intet er ændret).

    Er ét modul ændret, indlæses det og alle moduler efter det i RAEKKEFOLGE, så de får fat i
    de nye klasser og funktioner. Fejler indlæsningen, hæves fejlen, og tidsstemplerne
    opdateres ikke, så næste kald prøver igen."""
    if not _kendt:
        start()
        return []
    nu = _tider()
    aendret = [m for m in RAEKKEFOLGE if m in nu and nu[m] != _kendt.get(m)]
    if not aendret:
        return []
    # Tjek først, at alle ændrede filer kan oversættes. Ved en tastefejl skiftes intet ud.
    for modul in aendret:
        kilde = _fil(modul).read_text(encoding="utf-8")
        compile(kilde, str(_fil(modul)), "exec")
    foerste = min(RAEKKEFOLGE.index(m) for m in aendret)
    genindlaest = []
    for modul in RAEKKEFOLGE[foerste:]:
        if modul in sys.modules:
            importlib.reload(sys.modules[modul])
            genindlaest.append(modul)
    _kendt.clear()
    _kendt.update(_tider())
    log.warning("Ny kode indlæst uden genstart: %s", ", ".join(m.removeprefix("handle_mcp.") for m in aendret))
    return genindlaest


def server_aendret() -> bool:
    """Er server.py ændret, siden serveren startede? Så skal Claude Desktop genstartes."""
    return _server_start is not None and SERVER_FIL.exists() and SERVER_FIL.stat().st_mtime != _server_start
