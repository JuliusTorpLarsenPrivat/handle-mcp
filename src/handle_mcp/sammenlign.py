"""Prissæt en indkøbsliste i flere butikker og find den billigste.

Fremgangsmåde for hver vare på listen og hver butik:
  1. Søg i butikken.
  2. Giv hvert resultat point efter, hvor mange af ordene fra listen der står i varens navn,
     mærke eller mængde. Alle ord fundet = sikkert match, ellers markeres det 'usikkert'.
  3. Vælg det bedste match på lager. Ved lige mange point vinder den laveste enhedspris
     (kr. pr. kg/l), ellers den laveste pris.
Til sidst lægges butikkens gebyrer oven i, og minimumskøbet tjekkes.

Det er en enkel metode, så modellen bør se de usikre linjer igennem, før kurven fyldes.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from decimal import Decimal

from handle_mcp.butikker import Butik, ButikFejl
from handle_mcp.data import Vare

ERSTAT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e"})


def ord_i(tekst: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", tekst.lower().translate(ERSTAT)))


def point(soegning: str, vare: Vare) -> float:
    oensket = ord_i(soegning)
    if not oensket:
        return 0.0
    har = ord_i(" ".join(x for x in (vare.navn, vare.maerke, vare.maengde) if x))
    # "tomat" skal også ramme "tomater" og "hakkede tomater": sammenlign på ordets start.
    fundet = sum(1 for o in oensket if any(h.startswith(o) or o.startswith(h) for h in har if len(h) > 2))
    return fundet / len(oensket)


def bedste(soegning: str, varer: list[Vare]) -> tuple[list[Vare], float]:
    paa_lager = [v for v in varer if v.paa_lager]
    vurderet = sorted(
        ((point(soegning, v), v) for v in paa_lager),
        key=lambda pv: (-pv[0], pv[1].enhedspris if pv[1].enhedspris is not None else pv[1].pris, pv[1].pris),
    )
    vurderet = [(p, v) for p, v in vurderet if p > 0]
    return [v for _, v in vurderet[:3]], (vurderet[0][0] if vurderet else 0.0)


@dataclass
class Linje:
    soegning: str
    antal: int
    valgt: Vare | None
    alternativer: list[Vare]
    sikker: bool

    @property
    def beloeb(self) -> Decimal:
        return self.valgt.pris * self.antal if self.valgt else Decimal(0)


@dataclass
class Butiksresultat:
    butik: str
    linjer: list[Linje] = field(default_factory=list)
    gebyrer: dict[str, Decimal] = field(default_factory=dict)
    minimumskoeb: Decimal | None = None
    fejl: str | None = None

    @property
    def varer_i_alt(self) -> Decimal:
        return sum((ln.beloeb for ln in self.linjer), Decimal(0))

    @property
    def i_alt(self) -> Decimal:
        return self.varer_i_alt + sum(self.gebyrer.values(), Decimal(0))

    @property
    def mangler(self) -> list[str]:
        return [ln.soegning for ln in self.linjer if ln.valgt is None]

    @property
    def usikre(self) -> list[str]:
        return [ln.soegning for ln in self.linjer if ln.valgt and not ln.sikker]


async def prissaet(butik: Butik, liste: list[tuple[str, int]]) -> Butiksresultat:
    res = Butiksresultat(butik.navn)
    try:
        kurv = await butik.kurv()
        res.gebyrer = {k: v for k, v in kurv.gebyrer.items() if k != "pant"}
        res.minimumskoeb = kurv.minimumskoeb
        for soegning, antal in liste:  # én ad gangen: vær flink ved butikken
            fundne, score = bedste(soegning, await butik.soeg(soegning, 10))
            res.linjer.append(Linje(soegning, antal, fundne[0] if fundne else None, fundne[1:], score >= 1))
    except ButikFejl as fejl:
        res.fejl = str(fejl)
    return res


async def sammenlign(butikker: list[Butik], liste: list[tuple[str, int]]) -> list[Butiksresultat]:
    """Butikkerne prissættes samtidig. Den billigste med flest fundne varer står først."""
    resultater = await asyncio.gather(*(prissaet(b, liste) for b in butikker))
    return sorted(resultater, key=lambda r: (r.fejl is not None, len(r.mangler), r.i_alt))
