"""Mængder: hvor meget der skal bruges ("500 g hakket oksekød"), og hvor meget en pakke
indeholder ("12 x 400 g", "1,50 kg / tilsat vand"). Så kan vi vælge den pakke, der dækker
behovet billigst, i stedet for bare den laveste kg-pris (som kan være en 4 kg-pose).

Alt regnes om til tre grundenheder: g, ml og stk.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal

# tal, evt. "N x" foran, og en enhed. "12 x 400 g", "1,50 kg", "0.5 l", "2 stk."
MAENGDE = re.compile(r"(?:(\d+)\s*x\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|gr|l|ltr|dl|cl|ml|stk|bdt|bundt|pk|pakke|pakker|bakke|ps)\.?(?![a-zæøå])", re.I)
GRUND = {"kg": ("g", 1000), "g": ("g", 1), "gr": ("g", 1), "l": ("ml", 1000), "ltr": ("ml", 1000),
         "dl": ("ml", 100), "cl": ("ml", 10), "ml": ("ml", 1), "stk": ("stk", 1), "bdt": ("stk", 1),
         "bundt": ("stk", 1), "pk": ("stk", 1), "pakke": ("stk", 1), "pakker": ("stk", 1),
         "bakke": ("stk", 1), "ps": ("stk", 1)}  # fmt: skip
ANTAL_X = re.compile(r"(\d+)\s*x\s*\d")  # "8 x 40 g" er 8 stk
MAKS_PAKKER = 20


@dataclass(frozen=True)
class Maengde:
    tal: float
    enhed: str  # g, ml eller stk

    def __str__(self) -> str:
        return f"{self.tal:g} {self.enhed}"


def _laes(m: re.Match) -> Maengde:
    gange = int(m.group(1)) if m.group(1) else 1
    grund, faktor = GRUND[m.group(3).lower()]
    return Maengde(gange * float(m.group(2).replace(",", ".")) * faktor, grund)


def behov(soegning: str) -> tuple[str, Maengde | None]:
    """Del "500 g hakket oksekød" i ("hakket oksekød", 500 g). Mængden står helst først eller
    sidst, men en enkelt mængde midt i teksten tæller også: "Nakkekoteletter 400 g øko" (fundet
    live: uden mængden vandt laveste kilopris, fx 12 dåser hakkede tomater til 156 kr)."""
    tekst = soegning.strip()
    fundne = list(MAENGDE.finditer(tekst))
    for m in fundne:
        if m.start() == 0 or m.end() == len(tekst):
            rest = (tekst[: m.start()] + tekst[m.end():]).strip(" ,")
            if rest:
                return rest, _laes(m)
    if len(fundne) == 1:
        m = fundne[0]
        rest = " ".join((tekst[: m.start()] + " " + tekst[m.end():]).split()).strip(" ,")
        if rest:
            return rest, _laes(m)
    return tekst, None


def pakkestoerrelse(*tekster: str | None, enhed: str) -> float | None:
    """Første mængde i varens tekster, der har samme grundenhed som behovet.
    "2 stk. / 250 g" giver 250 for g og 2 for stk."""
    for t in tekster:
        for m in MAENGDE.finditer(t or ""):
            if (mg := _laes(m)).enhed == enhed and mg.tal > 0:
                return mg.tal
    if enhed == "stk":
        for t in tekster:
            if m := ANTAL_X.search(t or ""):
                return float(m.group(1))
    return None


# Mængdetilbud, som butikkerne skriver dem: "3 for 85 kr", "Mix 2 stk. 35,-", "Mix 2 for 40,00".
# Varens pris er normalprisen pr. stk; rabatten gælder kun, når man køber hele "pakker" af tilbuddet.
MAENGDETILBUD = re.compile(r"^(?:mix\s+)?(\d+)\s*(?:stk\.?\s*)?(?:for\s*)?(\d+(?:[.,]\d+)?)\s*(?:kr|,-|$)", re.I)


def maengdetilbud(tilbud: str | None) -> tuple[int, Decimal] | None:
    """(antal, samlet pris) for et mængdetilbud, ellers None. "3 for 85 kr" → (3, 85)."""
    if not tilbud or not (m := MAENGDETILBUD.match(tilbud.strip())):
        return None
    antal, pris = int(m.group(1)), Decimal(m.group(2).replace(",", "."))
    return (antal, pris) if antal >= 2 and pris > 0 else None


def pris_for_antal(pris: Decimal, tilbud: str | None, n: int) -> Decimal:
    """Prisen for n stk, med mængderabat for hver hel gruppe: 3 stk af "2 for 150" (79,95/stk)
    koster 150 + 79,95. Rabatten bruges kun, hvis den faktisk er billigere."""
    mt = maengdetilbud(tilbud)
    if not mt or mt[1] >= pris * mt[0]:
        return pris * n
    grupper, rest = divmod(n, mt[0])
    return mt[1] * grupper + pris * rest


def pakker(behov_: Maengde, stoerrelse: float) -> int | None:
    """Antal pakker, der skal til for at dække behovet. None hvis det er urimeligt mange."""
    n = max(1, math.ceil(behov_.tal / stoerrelse - 1e-9))
    return n if n <= MAKS_PAKKER else None
