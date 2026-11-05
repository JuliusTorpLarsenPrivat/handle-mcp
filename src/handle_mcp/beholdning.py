"""Det I har hjemme: køleskab, fryser og skab.

I fortæller Claude, hvad der er tilbage ("to løg, en halv pakke smør"), og Claude gemmer det
her. Bagefter kan Claude lave madplan og indkøbsliste ud fra det, der mangler.

Listen ligger i beholdning.json på din egen maskine (se lokal.py). Intet sendes til butikkerne.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo

from handle_mcp.lokal import LokalFejl, laes, noegle, skriv, tekst

TIDSZONE = ZoneInfo("Europe/Copenhagen")
MAKS_VARER = 300  # en grænse, så en fejl ikke kan fylde filen op

BeholdningFejl = LokalFejl  # samme fejltype som resten af den lokale lagring


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


def hent() -> list[Beholdningsvare]:
    return [Beholdningsvare(**v) for v in laes("beholdning").get("varer", [])]


def opdater(
    tilfoej: list[NyVare] | None = None, fjern: list[str] | None = None, erstat_alt: bool = False
) -> tuple[list[Beholdningsvare], list[str]]:
    """Tilføj/ret varer og fjern brugte. Giver (ny beholdning, navne der ikke fandtes)."""
    nu = datetime.now(TIDSZONE).isoformat(timespec="minutes")
    varer = {} if erstat_alt else {noegle(v.navn): v for v in hent()}
    ukendte = []
    for navn in fjern or []:
        if varer.pop(noegle(str(navn)), None) is None:
            ukendte.append(str(navn))
    for ny in tilfoej or []:
        navn = tekst(ny.get("navn"), "navn")
        if not navn:
            raise BeholdningFejl("Hver vare skal have et navn.")
        varer[noegle(navn)] = Beholdningsvare(navn, tekst(ny.get("maengde"), "maengde"), tekst(ny.get("sted"), "sted"), nu)
    if len(varer) > MAKS_VARER:
        raise BeholdningFejl(f"Højst {MAKS_VARER} varer i beholdningen.")
    resultat = sorted(varer.values(), key=lambda v: ((v.sted or "~").lower(), v.navn.lower()))
    skriv("beholdning", {"varer": [asdict(v) for v in resultat]})
    return resultat, ukendte
