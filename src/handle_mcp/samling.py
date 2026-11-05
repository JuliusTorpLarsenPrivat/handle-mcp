"""Jeres egen opskriftssamling: retter I kender og stoler på.

AI-opfundne opskrifter kan være elendige. Derfor kan I gemme jeres faste retter her (fra nemlig,
andre sider eller jeres egne), og Claude vælger blandt dem, når der skal laves madplan.

Gemmes i opskrifter.json på din egen maskine (se lokal.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from handle_mcp.lokal import LokalFejl, laes, noegle, skriv, tekst, tekster

MAKS_OPSKRIFTER = 500


@dataclass
class GemtOpskrift:
    navn: str
    link: str | None = None
    ingredienser: list[str] = field(default_factory=list)  # fri tekst: "500 g hakket oksekød"
    personer: int | None = None
    tags: list[str] = field(default_factory=list)  # fx hverdag, vegetar, under 30 min, børnevenlig
    note: str | None = None  # fx "dobbelt hvidløg", "Julius' favorit"
    tilfoejet: str = ""


def _link(vaerdi: str | None) -> str | None:
    link = tekst(vaerdi, "link", 300)
    if link and not link.startswith(("https://", "http://")):
        raise LokalFejl("Linket skal starte med https://")
    return link


def hent(tag: str | None = None, soeg: str | None = None) -> list[GemtOpskrift]:
    """Alle opskrifter, evt. kun dem med et tag eller et ord i navn/ingredienser/note."""
    alle = [GemtOpskrift(**o) for o in laes("opskrifter").get("opskrifter", [])]
    if tag:
        alle = [o for o in alle if noegle(tag) in (noegle(t) for t in o.tags)]
    if soeg:
        ord_ = noegle(soeg)
        alle = [o for o in alle if ord_ in noegle(" ".join([o.navn, o.note or "", *o.ingredienser]))]
    return alle


def gem(
    navn: str,
    link: str | None = None,
    ingredienser: list[str] | None = None,
    personer: int | None = None,
    tags: list[str] | None = None,
    note: str | None = None,
) -> GemtOpskrift:
    """Gem en opskrift. Findes navnet i forvejen, erstattes den (tilføjet-datoen beholdes)."""
    rent_navn = tekst(navn, "navn")
    if not rent_navn:
        raise LokalFejl("Opskriften skal have et navn.")
    if personer is not None and not 1 <= personer <= 20:
        raise LokalFejl("Personer skal være mellem 1 og 20.")
    alle = {noegle(o.navn): o for o in hent()}
    gammel = alle.get(noegle(rent_navn))
    ny = GemtOpskrift(
        navn=rent_navn,
        link=_link(link),
        ingredienser=tekster(ingredienser, "ingredienser", 60, 100),
        personer=personer,
        tags=tekster(tags, "tags", 15, 30),
        note=tekst(note, "note", 500),
        tilfoejet=gammel.tilfoejet if gammel else date.today().isoformat(),
    )
    alle[noegle(rent_navn)] = ny
    if len(alle) > MAKS_OPSKRIFTER:
        raise LokalFejl(f"Højst {MAKS_OPSKRIFTER} opskrifter.")
    _gem_alle(alle.values())
    return ny


def fjern(navn: str) -> bool:
    alle = {noegle(o.navn): o for o in hent()}
    if alle.pop(noegle(navn), None) is None:
        return False
    _gem_alle(alle.values())
    return True


def _gem_alle(opskrifter) -> None:
    raekke = sorted(opskrifter, key=lambda o: o.navn.lower())
    skriv("opskrifter", {"opskrifter": [asdict(o) for o in raekke]})
