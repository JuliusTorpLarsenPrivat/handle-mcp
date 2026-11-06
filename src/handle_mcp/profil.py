"""Husstandens profil: hvor mange I er, ugebudget og hvad I kan lide og ikke kan lide.

Claude læser den, før der laves madplan, så I ikke skal gentage jer hver uge.
Gemmes i profil.json på din egen maskine (se lokal.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from handle_mcp.lokal import LokalFejl, laes, skriv, tekst, tekster


@dataclass
class Profil:
    personer: int | None = None
    ugebudget_kr: float | None = None  # mad for en uge, inkl. levering
    praeferencer: list[str] = field(default_factory=list)  # fx "vegetar om mandagen", "max 30 min på hverdage"
    undgaa: list[str] = field(default_factory=list)  # allergier og det I ikke kan lide, fx "svampe"
    note: str | None = None
    # Måltider, der er de samme hver uge og skal med på indkøbslisten og i prisen,
    # fx "Morgenmad hver dag, 2 pers.: græsk yoghurt med vindruer, mandler og mørk chokolade"
    faste_maaltider: list[str] = field(default_factory=list)
    # Krav til bestemte varer, fx "Mørk chokolade: meget mørk, mindst 85 %"
    varepraeferencer: list[str] = field(default_factory=list)


def hent() -> Profil:
    data = laes("profil")
    kendte = Profil.__dataclass_fields__
    return Profil(**{k: v for k, v in data.items() if k in kendte})


def opdater(
    personer: int | None = None,
    ugebudget_kr: float | None = None,
    praeferencer: list[str] | None = None,
    undgaa: list[str] | None = None,
    note: str | None = None,
    faste_maaltider: list[str] | None = None,
    varepraeferencer: list[str] | None = None,
) -> Profil:
    """Ret de felter, der er givet. Felter der ikke er givet, bliver som de var.
    Lister erstattes helt, så Claude skal sende hele den nye liste."""
    p = hent()
    if personer is not None:
        if not 1 <= personer <= 20:
            raise LokalFejl("Personer skal være mellem 1 og 20.")
        p.personer = personer
    if ugebudget_kr is not None:
        if not 0 <= ugebudget_kr <= 20000:
            raise LokalFejl("Ugebudgettet skal være mellem 0 og 20.000 kr.")
        p.ugebudget_kr = round(float(ugebudget_kr), 2) or None  # 0 fjerner budgettet
    if praeferencer is not None:
        p.praeferencer = tekster(praeferencer, "præferencer", 30, 120)
    if undgaa is not None:
        p.undgaa = tekster(undgaa, "ting at undgå", 30, 80)
    if note is not None:
        p.note = tekst(note, "note", 500)
    if faste_maaltider is not None:
        p.faste_maaltider = tekster(faste_maaltider, "faste måltider", 14, 200)
    if varepraeferencer is not None:
        p.varepraeferencer = tekster(varepraeferencer, "vareønsker", 30, 150)
    skriv("profil", asdict(p))
    return p
