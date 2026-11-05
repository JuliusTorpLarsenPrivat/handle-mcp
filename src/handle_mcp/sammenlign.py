"""Prissæt en indkøbsliste i flere butikker og find den billigste.

Fremgangsmåde for hver vare på listen og hver butik:
  1. Søg i butikken og hent de første SOEGEDYBDE hits. Butikkerne sorterer efter popularitet,
     så de billigste varer ligger tit længere nede end top 10 (fx nemligs egne discountvarer).
     Butikkens tilbud (hentet én gang) lægges oveni, så et tilbud kommer med, selvom søgningen
     ikke har det i toppen.
  2. Giv hvert resultat point efter, hvor mange af ordene fra listen der står i varens navn,
     mærke eller mængde (se ord_point). "letmælk" tæller fuldt for "mælk", mens
     "mælkechokolade" kun tæller halvt. Fuldt point for alle ord = sikkert match, ellers
     markeres det 'usikkert'.
  3. Vælg det bedste match på lager (ikke gratisvarer). Ved lige mange point vinder den
     laveste enhedspris (kr. pr. kg/l) i den mest almindelige enhed, ellers den laveste pris.
Til sidst lægges butikkens gebyrer oven i, og minimumskøbet tjekkes.

Det er en enkel metode, så modellen bør se de usikre linjer igennem, før kurven fyldes.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from handle_mcp.butikker import Butik, ButikFejl
from handle_mcp.data import Vare

ERSTAT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e"})
SOEGEDYBDE = 50  # hits pr. vare og butik. 10 var for lidt: de billigste lå tit under top 10.
TILBUDSDYBDE = 200  # tilbud pr. butik (nemlig: pr. tilbudsliste), hentet én gang pr. sammenligning
# Endelser der bare er bøjning af samme ord (efter æ→ae osv.): tomat/tomater, æble/æbler, løg/løgene.
BOEJNINGER = {"", "e", "r", "er", "n", "en", "et", "ne", "ene", "erne", "s", "es"}
# Endelser der kun siger noget om udskæring/form, så varen stadig er den samme: kyllingebrystfilet.
FORMER = {"filet", "fileter", "koed", "tern", "strimler", "stykker"}


def ord_i(tekst: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", tekst.lower().translate(ERSTAT)))


def ord_point(oensket: str, har: str, sammensat: bool = True) -> float:
    """Hvor godt ét ønsket ord passer med ét ord fra varen.

    På dansk står hovedordet sidst: "letmælk" ER mælk, men "mælkechokolade" er chokolade.
      1   samme ord, en bøjning (tomat/tomater), en udskæring (kyllingebryst/-filet)
          eller et sammensat ord, der ender på ordet (mælk/letmælk)
      0.5 et sammensat ord, der starter med ordet (mælk/mælkechokolade)
      0   ellers
    Med sammensat=False tæller sammensatte ord, der ender på ordet, ikke. Det bruges til tilbud,
    som butikkens søgning ikke har vurderet: ellers bliver 'æg' til trusseindlæg og 'ost' til frost."""
    kort, lang = sorted((oensket, har), key=len)
    if len(kort) <= 2:
        return 1.0 if kort == lang else 0.0
    if lang.startswith(kort):
        rest = lang[len(kort):]
        return 1.0 if rest in BOEJNINGER or rest.lstrip("e") in FORMER else 0.5
    if sammensat and any(lang[: len(lang) - len(b)].endswith(kort) for b in BOEJNINGER if len(lang) - len(b) > len(kort)):
        return 1.0
    return 0.0


def point(soegning: str, vare: Vare, sammensat: bool = True) -> float:
    oensket = ord_i(soegning)
    if not oensket:
        return 0.0
    har = ord_i(" ".join(x for x in (vare.navn, vare.maerke, vare.maengde) if x))
    fundet = sum(max((ord_point(o, h, sammensat) for h in har), default=0.0) for o in oensket)
    return fundet / len(oensket)


def rangeret(soegning: str, varer: list[Vare]) -> list[tuple[float, Vare]]:
    """Relevante varer på lager: flest point først, derefter laveste enhedspris og pris.

    Gratisvarer (0 kr, fx 'Undgå madspild' med kort holdbarhed) springes over: de er få og
    forsvinder hurtigt. Enhedspriser sammenlignes kun i samme enhed som de fleste af de bedste
    match (kr/l for mælk), så fx små portionspakker i kr/stk ikke snyder sig foran."""
    vurderet = [(p, v) for v in varer if v.paa_lager and v.pris > 0 and (p := point(soegning, v)) > 0]
    if not vurderet:
        return []
    top = max(p for p, _ in vurderet)
    enheder = Counter(v.enhed for p, v in vurderet if p == top)
    hovedenhed = enheder.most_common(1)[0][0]
    return sorted(
        vurderet,
        key=lambda pv: (
            -pv[0],
            pv[1].enhed != hovedenhed,
            pv[1].enhedspris if pv[1].enhedspris is not None else pv[1].pris,
            pv[1].pris,
        ),
    )


def bedste(soegning: str, varer: list[Vare]) -> tuple[list[Vare], float]:
    vurderet = rangeret(soegning, varer)
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


async def hent_tilbud(butik: Butik) -> list[Vare]:
    """Butikkens aktuelle tilbud. De er en bonus: fejler de, bruges søgningen alene."""
    try:
        return await butik.tilbud(TILBUDSDYBDE)
    except ButikFejl:
        return []


async def kandidater(butik: Butik, soegning: str, tilbud: list[Vare]) -> list[Vare]:
    """Søgeresultaterne plus tilbudsvarer, som søgningen ikke fandt.

    Butikkens søgning har allerede sorteret irrelevante varer fra, men tilbudslisten er
    usorteret. Derfor kommer et tilbud kun med, hvis alle ord passer direkte (ikke via et
    sammensat ord). Bagefter rangeres alt ens af rangeret()."""
    hits = await butik.soeg(soegning, SOEGEDYBDE)
    kendte = {v.id for v in hits}
    return hits + [v for v in tilbud if v.id not in kendte and point(soegning, v, sammensat=False) >= 1]


async def prissaet(butik: Butik, liste: list[tuple[str, int]]) -> Butiksresultat:
    res = Butiksresultat(butik.navn)
    try:
        kurv = await butik.kurv()
        res.gebyrer = {k: v for k, v in kurv.gebyrer.items() if k != "pant"}
        res.minimumskoeb = kurv.minimumskoeb
        tilbud = await hent_tilbud(butik)  # én gang pr. butik, ikke pr. vare
        for soegning, antal in liste:  # én ad gangen: vær flink ved butikken
            fundne, score = bedste(soegning, await kandidater(butik, soegning, tilbud))
            res.linjer.append(Linje(soegning, antal, fundne[0] if fundne else None, fundne[1:], score >= 1))
    except ButikFejl as fejl:
        res.fejl = str(fejl)
    return res


async def sammenlign(butikker: list[Butik], liste: list[tuple[str, int]]) -> list[Butiksresultat]:
    """Butikkerne prissættes samtidig. Den billigste med flest fundne varer står først."""
    resultater = await asyncio.gather(*(prissaet(b, liste) for b in butikker))
    return sorted(resultater, key=lambda r: (r.fejl is not None, len(r.mangler), r.i_alt))
