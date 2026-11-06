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
from handle_mcp.maengde import Maengde, behov, maengdetilbud, pakker, pakkestoerrelse, pris_for_antal

ERSTAT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e"})
SOEGEDYBDE = 50  # hits pr. vare og butik. 10 var for lidt: de billigste lå tit under top 10.
TILBUDSDYBDE = 200  # tilbud pr. butik (nemlig: pr. tilbudsliste), hentet én gang pr. sammenligning
ALTERNATIVER = 4  # så Claude kan se efter og ikke bare tager første bedste
SENE_GEBYRER = {"nemlig": {"poser": Decimal("17.85")}}  # står ikke på en tom kurv
OEKO_TILLAEG = 2.0  # med oeko=True må en økovare koste op til dobbelt så meget og stadig vinde (Julius: mere vægt på øko)
# Endelser der bare er bøjning af samme ord (efter æ→ae osv.): tomat/tomater, æble/æbler, løg/løgene.
BOEJNINGER = {"", "e", "r", "er", "n", "en", "et", "ne", "ene", "erne", "s", "es"}
# Endelser der kun siger noget om udskæring/form, så varen stadig er den samme: kyllingebrystfilet.
FORMER = {"filet", "fileter", "koed", "tern", "strimler", "stykker", "ost"}  # feta/fetaost
# Ord der betyder det samme i butikkernes navne: "svinekød" hedder tit "grisekød".
SYNONYMER = {"svine": "grise", "grise": "svine", "frosne": "frost", "frossen": "frost", "frosset": "frost"}
# Egenskaber der tæller fuldt, uanset om de står i navnet eller i mængden ("500 g / frost").
# "fersk" er en egenskab, ikke en vare: "Rødspættefilet fersk" blev til "Soltørrede fersken"
EGENSKABER = {"frost", "oeko", "oekologisk", "okologisk", "fersk", "frisk", "friske"}
EMBALLAGE = re.compile(r"\s(?:i|på|paa)\s+(?:net|bakke|pose|potte|glas|dåse|daase|tube|spand|karton)\b|\b(?:potte|bdt\.?|bundt)\b", re.I)
ENHEDSORD = {"g", "gr", "kg", "ml", "cl", "dl", "l", "ltr", "stk", "x", "pk", "bdt"}
PROCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def ord_i(tekst: str) -> set[str]:
    return set(_ordliste(tekst))


def _ordliste(tekst: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", tekst.lower().translate(ERSTAT))


def _varianter(o: str) -> set[str]:
    """Ordet selv og dets synonymer ("svinekoed" → også "grisekoed", "frosne" → "frost")."""
    ud = {o}
    for fra, til in SYNONYMER.items():
        if fra in o:
            ud.add(o.replace(fra, til))
    return ud


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
# eller tilbehør: "Urtete m. fennikel" er te, "Boller i karry" er boller. Et komma uden
# mellemrum er et decimalkomma ("Letmælk 1,5%") og skiller ikke.
TILBEHOER = re.compile(r"\s(?:m\.?|med|i|til|tilsat)\s|\(|,\s")  # også "m" uden punktum
FYLDORD = {"i", "m", "med", "til", "tilsat", "og", "af", "a", "paa", "uden", "u"}  # "makrel i tomat"
DYREORD = ("okse", "grise", "gris", "svine", "kalv", "lamme", "lam", "kylling", "kalkun", "and", "hjorte", "vildsvin")


def point(soegning: str, vare: Vare, sammensat: bool = True) -> float:
    """Andelen af søgeordene, der passer med varen. Ord i varens hoveddel (og mærket) giver
    fuldt point, ord i tilbehør eller mængde højst halvt. Står der selv tilbehør i søgningen
    ("makrel i tomat", "tun i vand"), tæller tilbehøret fuldt."""
    # Emballage er ikke tilbehør: "Hvidløg i net" blev til spegepølse m. hvidløg (fundet live)
    soegning = EMBALLAGE.sub(" ", soegning)
    # Tal og enheder er mængde, ikke vare: "400 g" må ikke gøre majsmel til nakkekoteletter
    oensket = {o for o in ord_i(soegning) - FYLDORD - ENHEDSORD if not o.isdigit()}
    if not oensket:
        return 0.0
    hoved, _, tilbehoer = _del(vare.navn)
    hovedord = _ordliste(hoved)
    # Sammenskrevne nabo-ord: "Iceberg salat" passer på "icebergsalat"
    fuld = set(hovedord) | {a + b for a, b in zip(hovedord, hovedord[1:])} | ord_i(vare.maerke or "")
    halv = ord_i(tilbehoer) | ord_i(vare.maengde or "")
    loft = 1.0 if TILBEHOER.search(soegning.lower()) else 0.5
    alle = fuld | halv

    def bedste(o: str) -> float:
        f = h = 0.0
        for v in _varianter(o):
            if v in EGENSKABER and v in alle:
                return 1.0
            f = max(f, max((ord_point(v, x, sammensat) for x in fuld), default=0.0))
            h = max(h, max((ord_point(v, x, sammensat) for x in halv), default=0.0))
            f = max(f, _dyr_og_stykke(v, alle, fuld))
        return max(f, min(h, loft))

    pr_ord = {o: bedste(o) for o in oensket}
    if oensket - EGENSKABER and not any(pr_ord[o] for o in oensket - EGENSKABER):
        return 0.0  # kun "øko"/"frost" passer, selve varen gør ikke (fundet live: majsmel øko)
    resultat = sum(pr_ord.values()) / len(oensket)
    # Procent er en egenskab ved varen: 85 % chokolade er ikke 50 % (fundet live)
    if (pct := set(PROCENT.findall(soegning))) and not pct <= set(PROCENT.findall(f"{vare.navn} {vare.maengde or ''}")):
        return min(resultat, 0.5)
    if EFTERLIGNING.search(vare.navn) and not EFTERLIGNING.search(soegning):
        return min(resultat, 0.5)  # "Græskinspireret yoghurt" er ikke græsk yoghurt (fundet i test 3)
    return resultat


# Varer, der efterligner noget: "græskinspireret", "græsk-stil", "skyr-type", "à la".
EFTERLIGNING = re.compile(r"inspireret|[-\s]stil\b|[-\s]type\b|\b(?:a|à) la\b|\bimitation", re.I)


def _dyr_og_stykke(o: str, alle: set[str], fuld: set[str]) -> float:
    """"svinemørbrad" er "Mørbrad af gris": dyret foran, stykket som eget ord."""
    for d in DYREORD:
        rest = o[len(d):]
        if o.startswith(d) and len(rest) >= 4 and rest in fuld:
            dyr = {d, d.rstrip("e"), SYNONYMER.get(d, d), SYNONYMER.get(d, d).rstrip("e")}
            if any(x in dyr or any(x.startswith(y) for y in dyr) for x in alle if x != rest):
                return 1.0
    return 0.0


def _del(navn: str) -> tuple[str, str, str]:
    """(hoveddel, skille, tilbehør) af et varenavn."""
    m = TILBEHOER.search(navn.lower())
    return (navn, "", "") if not m else (navn[: m.start()], m.group(), navn[m.end():])


MULTIPAK = re.compile(r"^\s*(\d+)\s*x\s*\d", re.I)


def er_multipak(v: Vare) -> bool:
    """"12 x 400 g" er en kasse. Fundet live: 12 dåser hakkede tomater til 156 kr til én ret."""
    return bool((m := MULTIPAK.match(v.maengde or "")) and int(m.group(1)) >= 3)


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
    oensket = ord_i(soegning)
    mad = not _ikke_mad_ord(oensket)  # søger man selv efter ikke-mad, må den gerne komme med
    vurderet = [
        (p, v)
        for v in varer
        if v.paa_lager and v.pris > 0 and not (mad and er_ikke_mad(v)) and (p := point(soegning, v)) > 0
    ]
    if not vurderet:
        return []
    top = max(p for p, _ in vurderet)
    enheder = Counter(v.enhed for p, v in vurderet if p == top)
    hovedenhed = enheder.most_common(1)[0][0]

    def noegle(pv: tuple[float, Vare]):
        p, v = pv
        rabat = OEKO_TILLAEG if oeko and er_oeko(v) else 1  # øko må koste lidt mere
        kost = skoennet_pris(v, behov_)
        enhedspris = v.enhedspris if v.enhedspris is not None else v.pris
        return (
            -p,
            er_forarbejdet(v, oensket),  # rå vare frem for pålæg, færdigret og indbagt
            er_blanding(v, oensket),  # "Ærter" frem for "Ærter og gulerødder"
            har_tilbehoer(v),  # "Kartofler" frem for "Kartofler m. krydderurter"
            behov_ is None and er_multipak(v),  # uden mængde: én dåse, ikke en kasse med 12
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
    "snack", "nudler", "tops", "postej", "paalaeg", "sandwich", "pizza", "burger",
    "nuggets", "pesto", "marmelade", "dip", "salat", "spread", "smoothie", "drik",
    "roeget", "stegt", "stegte", "kogt", "kogte", "grillet", "grillede", "rullepoelse", "snittet",
    "flaaede", "hakkede", "pulled", "knust", "crispy", "skiveskaaret", "frugtstaenger",
    "urtete", "bites", "slik", "kiks", "kage", "juice",
)  # fmt: skip  Bøjninger tæller også: indbagt(e), snack(s)
FORARBEJDET_HELE_ORD = {"te", "is"}  # korte ord, der kun må tælle som hele ord ("Grøn te")  ("skiver" er ikke med: stegeflæsk er altid i skiver)
# Ord der også afslører forarbejdning først i et sammensat ord: "Kyllingebryst sandwichskiver"
FORARBEJDET_FORLED = ("sandwich", "paalaeg")
# Kødtyper: søger man efter én, er en vare med en anden kødtype i navnet en anden vare
# (fundet live: "hakket oksekød" blev til "Hakket okse- og grisekød"). Gris og svin er det samme.
DYR = {"okse": "okse", "grise": "svin", "gris": "svin", "svine": "svin", "kalv": "kalv", "lam": "lam",
       "kylling": "kylling", "kalkun": "kalkun", "and": "and", "hjort": "hjort", "vildsvin": "vildsvin"}  # fmt: skip


def _dyr(ord_: set[str]) -> set[str]:
    return {k for o in ord_ for d, k in DYR.items() if o.startswith(d) and not (d == "and" and o != "and" and not o.startswith("ande"))}


# Varer der ikke er mad, men dukker op i søgninger efter mad (fundet live: prydløg og
# blomsterløg for "løg", buketter i "bdt." for persille, en gulvmoppe for basilikum).
IKKE_MAD = ("prydloeg", "blomsterloeg", "moppe", "refill", "opvask", "toiletrens", "rengoering",
            "vaskemiddel", "skyllemiddel", "shampoo", "saebe", "potteplante", "liljer", "clematis",
            "tulipaner", "hyacint", "stilke", "gulvklud", "karklud", "strepsils", "pastil",
            "tabletter", "amylmetacresol", "plaster", "vitamin")  # fmt: skip


def _ikke_mad_ord(ord_: set[str]) -> bool:
    return any(i in o for o in ord_ for i in IKKE_MAD)


def er_ikke_mad(v: Vare) -> bool:
    return _ikke_mad_ord(ord_i(" ".join(x for x in (v.navn, v.maengde) if x)))


def er_blanding(v: Vare, oensket: set[str]) -> bool:
    """"Ærter og gulerødder" er en blanding, når man søgte efter ærter: søgeordet står kun før "og"."""
    hoved = _del(v.navn)[0].lower()
    if " og " not in hoved or "og" in oensket:
        return False
    foer, efter = (ord_i(x) for x in hoved.split(" og ", 1))
    ramt = lambda ord_: any(ord_point(o, x) >= 1 for o in oensket for x in ord_)  # noqa: E731
    return ramt(foer) and not ramt(efter)


def er_forarbejdet(v: Vare, oensket: set[str]) -> bool:
    """Er varen forarbejdet eller blandet på en måde, brugeren ikke selv søgte efter?"""
    navn = ord_i(v.navn)
    for o in navn:
        for f in FORARBEJDET:
            if any(o.endswith(f + b) for b in BOEJNINGER) and not any(f in s for s in oensket):
                return True
        for f in FORARBEJDET_FORLED:
            if f in o and not any(f in s for s in oensket):
                return True
        if o in FORARBEJDET_HELE_ORD and o not in oensket:
            return True
    soegt = _dyr(oensket)
    return bool(soegt and _dyr(navn) - soegt)


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
    if stoerrelse:
        return pakker(behov_, stoerrelse)
    if behov_.enhed == "stk" and behov_.tal == 1:
        return 1  # "1 stk hel kylling" / "1 stk broccoli": én pakke, uanset om den sælges efter vægt
    return None


def pris_for_behov(v: Vare, behov_: Maengde | None) -> Decimal | None:
    n = antal_pakker(v, behov_)
    return pris_for_antal(v.pris, v.tilbud, n) if n else None


def tilbud_tip(v: Vare, n: int) -> str | None:
    """Et tip, når et mængdetilbud kræver flere, end der skal bruges: "Køb 2 for 150 kr (75 kr/stk)"."""
    mt = maengdetilbud(v.tilbud)
    if not mt or n % mt[0] == 0 or mt[1] >= v.pris * mt[0]:
        return None
    k = -(-n // mt[0]) * mt[0]  # næste hele antal i tilbuddet
    return f"Mængdetilbud: køb {k} for {pris_for_antal(v.pris, v.tilbud, k):.2f} kr ({mt[1] / mt[0]:.2f} kr/stk) i stedet for {n}"


# Kun vægt og rumfang: "kr/stk" kan være pr. tebrev i en æske, ikke pr. vare (fundet live).
ENHED_TIL_GRUND = {"kg": ("g", 1000), "l": ("ml", 1000)}


def skoennet_pris(v: Vare, behov_: Maengde | None) -> Decimal | None:
    """Prisen for behovet, eller et skøn ud fra enhedsprisen, når pakkestørrelsen ikke står
    der (fundet live: bananer sælges pr. stk uden vægt, så dyre Saba-bananer vandt)."""
    if (kost := pris_for_behov(v, behov_)) is not None:
        return kost
    if behov_ and v.enhedspris is not None and (g := ENHED_TIL_GRUND.get(v.enhed or "")) and g[0] == behov_.enhed:
        return v.enhedspris * Decimal(str(behov_.tal / g[1]))
    return None


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
    def behov_pakker(self) -> int:
        return (antal_pakker(self.valgt, self.behov) if self.valgt else None) or 1

    @property
    def pakker(self) -> int:
        """Pakker at købe. For holdbare varer rundes op til et mængdetilbud, når de ekstra
        koster mindre end én pakke til normalpris (Julius: "3 stk. 78,-" på mandler)."""
        n = self.behov_pakker
        if self.valgt and self.antal == 1 and (k := tilbud_til_lager(self.valgt, n)):
            return k
        return n

    @property
    def beloeb(self) -> Decimal:
        """Med mængderabat: 2 pakker af "2 for 150 kr" koster 150, ikke 2 x normalprisen."""
        return pris_for_antal(self.valgt.pris, self.valgt.tilbud, self.pakker * self.antal) if self.valgt else Decimal(0)

    @property
    def tip(self) -> str | None:
        if not self.valgt:
            return None
        if self.pakker != self.behov_pakker:
            mt = maengdetilbud(self.valgt.tilbud)
            return f"Mængdetilbud på holdbar vare: købt {self.pakker} i stedet for {self.behov_pakker} ({mt[1] / mt[0]:.2f} kr/stk), resten holder til senere"
        return tilbud_tip(self.valgt, self.pakker * self.antal)


HOLDBARE_KATEGORIER = ("kolonial", "snacks og søde sager", "drikkevarer")
MAKS_EKSTRA_TIL_LAGER = 2


def er_holdbar(v: Vare) -> bool:
    """Holder i skabet i måneder: mandler, chokolade, pasta, dåser … (ikke grønt og køl)."""
    from handle_mcp import historik

    navn = v.navn.upper()
    if typ := historik.basistype(navn):
        return historik.BASISTYPER[typ][0] == "skab" and typ not in ("kartofler", "løg", "hvidløg")
    return historik.kategori(navn) in HOLDBARE_KATEGORIER


def tilbud_til_lager(v: Vare, n: int) -> int | None:
    """Antal at købe, hvis et mængdetilbud på en holdbar vare kan betale sig: højst 2 ekstra,
    og de ekstra skal samlet koste mindre end én pakke til normalpris. Ellers None."""
    mt = maengdetilbud(v.tilbud)
    if not mt or n % mt[0] == 0 or mt[1] >= v.pris * mt[0] or not er_holdbar(v):
        return None
    k = -(-n // mt[0]) * mt[0]
    if k - n > MAKS_EKSTRA_TIL_LAGER or pris_for_antal(v.pris, v.tilbud, k) - pris_for_antal(v.pris, v.tilbud, n) >= v.pris:
        return None
    return k


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
    from handle_mcp.lokal import marker_claude_aktiv

    marker_claude_aktiv()
    hits = await butik.soeg(soegning, SOEGEDYBDE)
    kendte = {v.id for v in hits}
    return hits + [v for v in tilbud if v.id not in kendte and point(soegning, v, sammensat=False) >= 1]


async def prissaet(butik: Butik, liste: list[tuple[str, int]], oeko: bool = False) -> Butiksresultat:
    res = Butiksresultat(butik.navn)
    try:
        kurv = await butik.kurv()
        res.gebyrer = {k: v for k, v in kurv.gebyrer.items() if k != "pant"}
        # Gebyrer, der først kommer på, når der er varer i kurven (fundet i første rigtige uge:
        # nemlig tog 17,85 kr for poser, som ikke stod på den tomme kurv)
        for navn, beloeb in SENE_GEBYRER.get(butik.navn, {}).items():
            res.gebyrer.setdefault(navn, beloeb)
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
    from handle_mcp.lokal import marker_claude_aktiv

    marker_claude_aktiv()  # prissætning tager minutter: botten skal vide, at Claude er i gang
    """Butikkerne prissættes samtidig. Den billigste med flest fundne varer står først."""
    resultater = await asyncio.gather(*(prissaet(b, liste, oeko) for b in butikker))
    return sorted(resultater, key=lambda r: (r.fejl is not None, len(r.mangler), r.i_alt))
