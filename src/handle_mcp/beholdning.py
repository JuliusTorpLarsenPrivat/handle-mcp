"""Det I har hjemme: køleskab, fryser og skab.

I fortæller Claude, hvad der er tilbage ("to løg, en halv pakke smør"), og Claude gemmer det
her. Bagefter kan Claude lave madplan og indkøbsliste ud fra det, der mangler.

Listen ligger i en lille JSON-fil på din egen maskine, uden for OneDrive:
  Windows:  %LOCALAPPDATA%\\handle-mcp\\beholdning.json
  ellers:   ~/.local/share/handle-mcp/beholdning.json
Miljøvariablen HANDLE_MCP_DATA kan pege et andet sted hen. Intet sendes til butikkerne.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo

TIDSZONE = ZoneInfo("Europe/Copenhagen")
MAKS_VARER = 300  # en grænse, så en fejl ikke kan fylde filen op
MAKS_TEGN = 80


class BeholdningFejl(Exception):
    pass


@dataclass
class Beholdningsvare:
    navn: str
    maengde: str | None  # fri tekst: "2 stk", "en halv pakke", "ca. 300 g"
    sted: str | None  # fx køleskab, fryser, skab
    noteret: str  # hvornår den sidst blev opdateret (ISO-tid)


class NyVare(TypedDict):
    navn: str
    maengde: NotRequired[str]
    sted: NotRequired[str]


def mappe() -> Path:
    if sti := os.environ.get("HANDLE_MCP_DATA"):
        return Path(sti)
    if lokal := os.environ.get("LOCALAPPDATA"):
        return Path(lokal) / "handle-mcp"
    return Path.home() / ".local" / "share" / "handle-mcp"


def _fil() -> Path:
    return mappe() / "beholdning.json"


def _noegle(navn: str) -> str:
    return " ".join(navn.lower().split())


def _tekst(vaerdi: object, felt: str) -> str | None:
    if vaerdi is None:
        return None
    tekst = " ".join(str(vaerdi).split())
    if len(tekst) > MAKS_TEGN:
        raise BeholdningFejl(f"{felt} må højst være {MAKS_TEGN} tegn: {tekst[:30]}…")
    return tekst or None


def hent() -> list[Beholdningsvare]:
    try:
        data = json.loads(_fil().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as fejl:
        raise BeholdningFejl(f"Kunne ikke læse beholdningen: {fejl}") from None
    return [Beholdningsvare(**v) for v in data.get("varer", [])]


def _gem(varer: list[Beholdningsvare]) -> None:
    """Skriv til en midlertidig fil og byt den ind, så filen aldrig står halvt skrevet."""
    fil = _fil()
    fil.parent.mkdir(parents=True, exist_ok=True)
    indhold = json.dumps({"varer": [asdict(v) for v in varer]}, ensure_ascii=False, indent=2)
    fd, midlertidig = tempfile.mkstemp(dir=fil.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(indhold)
        os.replace(midlertidig, fil)
    except BaseException:
        Path(midlertidig).unlink(missing_ok=True)
        raise


def opdater(
    tilfoej: list[NyVare] | None = None, fjern: list[str] | None = None, erstat_alt: bool = False
) -> tuple[list[Beholdningsvare], list[str]]:
    """Tilføj/ret varer og fjern brugte. Giver (ny beholdning, navne der ikke fandtes)."""
    nu = datetime.now(TIDSZONE).isoformat(timespec="minutes")
    varer = {} if erstat_alt else {_noegle(v.navn): v for v in hent()}
    ukendte = []
    for navn in fjern or []:
        if varer.pop(_noegle(str(navn)), None) is None:
            ukendte.append(str(navn))
    for ny in tilfoej or []:
        navn = _tekst(ny.get("navn"), "navn")
        if not navn:
            raise BeholdningFejl("Hver vare skal have et navn.")
        varer[_noegle(navn)] = Beholdningsvare(
            navn, _tekst(ny.get("maengde"), "maengde"), _tekst(ny.get("sted"), "sted"), nu
        )
    if len(varer) > MAKS_VARER:
        raise BeholdningFejl(f"Højst {MAKS_VARER} varer i beholdningen.")
    resultat = sorted(varer.values(), key=lambda v: ((v.sted or "~").lower(), v.navn.lower()))
    _gem(resultat)
    return resultat, ukendte
