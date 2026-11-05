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
  3. Vælg det bedste match på lager (ikke gratisvarer). Står der en mængde på listen
     ("500 g hakket oksekød"), vinder de pakker, der dækker den billigst. Ellers vinder den
     laveste enhedspris (kr. pr. kg/l) i den mest almindelige enhed. Med oeko=True må
     økovarer koste lidt mere.
Til sidst lægges butikkens gebyrer oven i, og minimumskøbet tjekkes.

Det er en enkel metode. Modellen skal se alternativerne og de usikre linjer igennem og
vælge selv ud fra kvalitet, før kurven fyldes.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from handle_mcp.butikker import Butik, ButikFejl
from handle_mcp.data import Vare
from handle_mcp.maengde import Maengde, behov, pakker, pakkestoerrelse

ERSTAT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e"})
SOEGEDYBDE = 50  # hits pr. vare og butik. 10 var for lidt: de billigste lå tit under top 10.
TILBUDSDYBDE = 200  # tilbud pr. butik (nemlig: pr. tilbudsliste), hentet én gang pr. sammenligning
ALTERNATIVER = 4  # så Claude kan se efter og ikke bare tager første bedste
OEKO_TILLAEG = 1.5  # med oeko=True må en økovare koste op til 50 % mere og stadig vinde
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
    if any(lang[: len(lang) - len(b)].endswith(kort) for b in BOEJNINGER if len(lang) - len(b) > len(kort)):
        if lang == oensket:  # 'salat' er ikke 'feldsalat': den generelle vare er ikke den ønskede
            return 0.5
        return 1.0 if sammensat else 0.0
    return 0.0


# Det der står efter "m.", "med", "i", "til", "tilsat", en parentes eller et komma, er smag
# eller tilbehør: "Urtete m. fennikel" er te, "Boller i karry" er boller.
TILBEHOER = re.compile(r"\s(?:m\.|med|i|til|tilsat)\s|[(,]")


def point(soegning: str, vare: Vare, sammensat: bool = True) -> float:
    """Andelen af søgeordene, der passer med varen. Ord i varens hoveddel (og mærket) giver
    fuldt point, ord i tilbehør eller mængde højst halvt."""
    oensket = ord_i(soegning)
    if not oensket:
        return 0.0
    hoved, _, tilbehoer = _del(vare.navn)
    fuld = ord_i(hoved) | ord_i(vare.maerke or "")
    halv = ord_i(tilbehoer) | ord_i(vare.maengde or "")

    def bedste(o: str) -> float:
        f = max((ord_point(o, h, sammensat) for h in fuld), default=0.0)
        h = max((ord_point(o, x, sammensat) for x in halv), default=0.0)
        return max(f, min(h, 0.5))

    return sum(bedste(o) for o in oensket) / len(oensket)


def _del(navn: str) -> tuple[str, str, str]:
    """(hoveddel, skille, tilbehør) af et varenavn."""
    m = TILBEHOER.search(navn.lower())
    return (navn, "", "") if not m else (navn[: m.start()], m.group(), navn[m.end():])


def rangeret(
    soegning: str, varer: list[Vare], behov_: Maengde | None = None, oeko: bool = False
) -> list[tuple[float, Vare]]:
    """Relevante varer på lager: flest point først, derefter det billigste køb.

    Med et behov (fx 500 g) er det billigste køb de pakker, der dækker behovet billigst:
    2 x 500 g kan slå en 4 kg-pose med lavere kg-pris. Uden behov vinder laveste enhedspris.
    Med oeko=True må en økovare koste op til OEKO_TILLAEG gange mere og stadig vinde.

    Gratisvarer (0 kr, fx 'Undgå madspild' med kort holdbarhed) springes over: de er få og
    forsvinder hurtigt. Enhedspriser sammenlignes kun i samme enhed som de fleste af de bedste
    match (kr/l for mælk), så fx små portionspakker i kr/stk ikke snyder sig foran."""
    vurderet = [(p, v) for v in varer if v.paa_lager and v.pris > 0 and (p := point(soegning, v)) > 0]
    if not vurderet:
        return []
    top = max(p for p, _ in vurderet)
    enheder = Counter(v.enhed for p, v in vurderet if p == top)
    hovedenhed = enheder.most_common(1)[0][0]

    oensket = ord_i(soegning)

    def noegle(pv: tuple[float, Vare]):
        p, v = pv
        rabat = OEKO_TILLAEG if oeko and er_oeko(v) else 1  # øko må koste lidt mere
        kost = pris_for_behov(v, behov_)
        enhedspris = v.enhedspris if v.enhedspris is not None else v.pris
        return (
            -p,
            er_forarbejdet(v, oensket),  # rå vare frem for pålæg, færdigret og indbagt
            har_tilbehoer(v),  # "Kartofler" frem for "Kartofler m. krydderurter"
            kost is None,  # varer, hvor vi kan regne pakkerne ud, først
            float(kost or 0) / rabat,
            v.enhed != hovedenhed,
            float(enhedspris) / rabat,
            v.pris,
        )

    return sorted(vurderet, key=noegle)


# Ord der afslører en forarbejdet vare (også sidst i sammensatte ord: aspargessuppe,
# baconleverpostej). Fundet i madplantest: kyllingebryst blev til pålæg, laks til indbagt laks.
FORARBEJDET = (
    "indbagt", "paneret", "panerede", "marineret", "faerdigret", "suppe", "sauce", "dressing",
    "snack", "nudler", "tops", "postej", "paalaeg", "skiver", "sandwich", "pizza", "burger",
    "nuggets", "pesto", "marmelade", "dip", "salat", "spread", "smoothie", "drik",
)  # fmt: skip


def er_forarbejdet(v: Vare, oensket: set[str]) -> bool:
    """Har varen et 'forarbejdet'-ord, som brugeren ikke selv søgte efter?"""
    for o in ord_i(v.navn):
        for f in FORARBEJDET:
            if o.endswith(f) and not any(f in s for s in oensket):
                return True
    return False


# Ord efter "m."/"i" der beskriver varen selv, ikke tilbehør: "Laksefilet m. skind",
# "Grønne asparges i bundt", "Hvidløg i net".
NEUTRALE = {"skind", "ben", "uden", "bundt", "bakke", "net", "pose", "glas", "daase", "oeko", "dansk", "frost", "stk"}


def har_tilbehoer(v: Vare) -> bool:
    _, skille, rest = _del(v.navn)
    return bool(skille) and not {o for o in ord_i(rest) if not o.isdigit()} <= NEUTRALE


def er_oeko(v: Vare) -> bool:
    return any(o.startswith("oeko") for o in ord_i(" ".join(x for x in (v.navn, v.maengde, v.maerke) if x)))


def antal_pakker(v: Vare, behov_: Maengde | None) -> int | None:
    """Pakker af varen, der skal til for at dække behovet. None hvis det ikke kan regnes ud."""
    if behov_ is None:
        return None
    stoerrelse = pakkestoerrelse(v.maengde, v.navn, enhed=behov_.enhed)
    return pakker(behov_, stoerrelse) if stoerrelse else None


def pris_for_behov(v: Vare, behov_: Maengde | None) -> Decimal | None:
    n = antal_pakker(v, behov_)
    return v.pris * n if n else None


def bedste(soegning: str, varer: list[Vare], behov_: Maengde | None = None, oeko: bool = False) -> tuple[list[Vare], float]:
    """Den bedste vare og ALTERNATIVER alternativer, plus pointene for den bedste."""
    vurderet = rangeret(soegning, varer, behov_, oeko)
    return [v for _, v in vurderet[: 1 + ALTERNATIVER]], (vurderet[0][0] if vurderet else 0.0)


@dataclass
class Linje:
    soegning: str
    antal: int
    valgt: Vare | None
    alternativer: list[Vare]
    sikker: bool
    behov: Maengde | None = None

    @property
    def pakker(self) -> int:
        return (antal_pakker(self.valgt, self.behov) if self.valgt else None) or 1

    @property
    def beloeb(self) -> Decimal:
        return self.valgt.pris * self.pakker * self.antal if self.valgt else Decimal(0)


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


async def prissaet(butik: Butik, liste: list[tuple[str, int]], oeko: bool = False) -> Butiksresultat:
    res = Butiksresultat(butik.navn)
    try:
        kurv = await butik.kurv()
        res.gebyrer = {k: v for k, v in kurv.gebyrer.items() if k != "pant"}
        res.minimumskoeb = kurv.minimumskoeb
        tilbud = await hent_tilbud(butik)  # én gang pr. butik, ikke pr. vare
        for soegning, antal in liste:  # én ad gangen: vær flink ved butikken
            tekst, behov_ = behov(soegning)  # "500 g hakket oksekød" → søg "hakket oksekød"
            fundne, score = bedste(tekst, await kandidater(butik, tekst, tilbud), behov_, oeko)
            valgt = fundne[0] if fundne else None
            # En forarbejdet vare er kun valgt, fordi der ikke var andet: den skal tjekkes
            sikker = score >= 1 and not (valgt and er_forarbejdet(valgt, ord_i(tekst)))
            res.linjer.append(Linje(soegning, antal, valgt, fundne[1:], sikker, behov_))
    except ButikFejl as fejl:
        res.fejl = str(fejl)
    return res


async def sammenlign(butikker: list[Butik], liste: list[tuple[str, int]], oeko: bool = False) -> list[Butiksresultat]:
    """Butikkerne prissættes samtidig. Den billigste med flest fundne varer står først."""
    resultater = await asyncio.gather(*(prissaet(b, liste, oeko) for b in butikker))
    return sorted(resultater, key=lambda r: (r.fejl is not None, len(r.mangler), r.i_alt))
