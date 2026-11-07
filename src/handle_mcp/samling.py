"""Jeres egen opskriftssamling: retter I kender og stoler på. Det er jeres FAVORITTER.

En ret kommer herind, når I siger, at den var god eller skal være favorit (på Telegram eller i
chatten). Claude tager 1-2 favoritter med i ugens menu, helst dem, der har været brugt længst
siden (`sidst_brugt` sættes, når en madplan med retten lægges i kurven).

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
    sidst_brugt: str | None = None  # dato for seneste madplan med retten i kurven
    gange_brugt: int = 0


def _link(vaerdi: str | None) -> str | None:
    link = tekst(vaerdi, "link", 300)
    if link and not link.startswith(("https://", "http://")):
        raise LokalFejl("Linket skal starte med https://")
    return link


def hent(tag: str | None = None, soeg: str | None = None) -> list[GemtOpskrift]:
    """Alle opskrifter, evt. kun dem med et tag eller et ord i navn/ingredienser/note."""
    kendte = GemtOpskrift.__dataclass_fields__
    alle = [GemtOpskrift(**{k: v for k, v in o.items() if k in kendte}) for o in laes("opskrifter").get("opskrifter", [])]
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
        sidst_brugt=gammel.sidst_brugt if gammel else None,
        gange_brugt=gammel.gange_brugt if gammel else 0,
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


def marker_brugt(retter: list[str], dag: date | None = None) -> list[str]:
    """Sæt sidst_brugt på de favoritter, der står i ugens retter (fx "Tir: Lasagne med salat"
    rammer favoritten "Lasagne"). Giver navnene på dem, der blev markeret."""
    dag = dag or date.today()
    alle = {noegle(o.navn): o for o in hent()}
    tekst_ = noegle(" | ".join(retter))
    ramt = []
    for n, o in alle.items():
        if n and n in tekst_ and o.sidst_brugt != dag.isoformat():
            o.sidst_brugt, o.gange_brugt = dag.isoformat(), o.gange_brugt + 1
            ramt.append(o.navn)
    if ramt:
        _gem_alle(alle.values())
    return ramt
