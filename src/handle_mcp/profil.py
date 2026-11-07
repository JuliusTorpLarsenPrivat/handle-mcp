"""Husstandens profil: hvor mange I er, ugebudget og hvad I kan lide og ikke kan lide.

Claude læser den, før der laves madplan, så I ikke skal gentage jer hver uge.
Gemmes i profil.json på din egen maskine (se lokal.py).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

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
    # Ønsker, som Telegram-botten selv har gemt ("husk …", "fremover …", "vi kan ikke lide …").
    # Står her, til Claude har flyttet dem til det rette felt, så de aldrig bliver glemt.
    fra_husstanden: list[str] = field(default_factory=list)


# Ord, der betyder "gem det til fremover". Bevidst bred: hellere et ønske for meget i profilen
# (Claude rydder op) end at Maja skal sige det igen. Fundet i brug, uge 47.
HUSKEORD = re.compile(
    r"\b(husk\w*|gem|profil\w*|indstilling\w*|fremover|fremadrettet|i fremtiden|for fremtiden|eftertiden|"
    r"fra nu af|altid|aldrig|næste gang|hver gang|en anden gang|generelt|permanent|"
    r"favorit\w*|(?:have|lave|laves|få) (?:\w+ ){0,2}igen|ikke igen|var (?:rigtig |meget |virkelig )?god|"
    r"vi (?:kan ikke lide|kan godt lide|elsker|hader|foretrækker|gider ikke|vil ikke|vil kun|spiser ikke|spiser kun))\b",
    re.I,
)
MAKS_FRA_HUSSTANDEN = 30


def er_huskeoenske(tekst: str) -> bool:
    return bool(HUSKEORD.search(tekst or ""))


def husk_fra_husstanden(fra: str, tekst: str) -> None:
    """Gem et ønske direkte i profilen (bruges af Telegram-botten, uden Claude)."""
    p = hent()
    linje = f"{datetime.now():%d/%m} {fra}: {' '.join(tekst.split())}"[:300]
    if linje not in p.fra_husstanden:
        p.fra_husstanden = (p.fra_husstanden + [linje])[-MAKS_FRA_HUSSTANDEN:]
        skriv("profil", asdict(p))


# Felterne, som Claude kan rette med opdater_profil({"felt": værdi}). Står her og ikke i
# værktøjets felter i server.py, så nye felter ikke kræver, at Claude Desktop genstartes.
FELTER = {
    "personer": "antal personer i husstanden (1-20)",
    "ugebudget_kr": "budget for en hel uge (7 dage) inkl. levering og poser; 0 fjerner det",
    "praeferencer": "liste: madstil, fx 'proteinrigt', 'hurtigt på hverdage'",
    "undgaa": "liste: allergier og det, I ikke kan lide",
    "note": "fri tekst",
    "faste_maaltider": "liste: måltider, der er de samme hver uge og skal med i indkøb og pris",
    "varepraeferencer": "liste: krav til bestemte varer, fx 'Mørk chokolade: mindst 85 %'",
    "fra_husstanden": "liste: ønsker, botten har gemt fra Telegram; send [] når de er flyttet",
}


def vis() -> dict:
    return {**asdict(hent()), "felter": FELTER}


def opdater_fra_dict(aendringer: dict) -> Profil:
    if not isinstance(aendringer, dict) or not aendringer:
        raise LokalFejl('Send de felter, der skal ændres, fx {"ugebudget_kr": 1500}.')
    if ukendte := sorted(set(aendringer) - set(FELTER)):
        raise LokalFejl(f"Ukendte felter: {', '.join(ukendte)}. Mulige felter: {', '.join(FELTER)}.")
    return opdater(**aendringer)


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
    fra_husstanden: list[str] | None = None,
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
    if fra_husstanden is not None:  # typisk [] efter at ønskerne er flyttet til de rette felter
        p.fra_husstanden = tekster(fra_husstanden, "ønsker fra husstanden", MAKS_FRA_HUSSTANDEN, 300)
    skriv("profil", asdict(p))
    return p
