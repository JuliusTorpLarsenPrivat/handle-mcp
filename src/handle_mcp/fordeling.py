"""Hvor skal der købes? Den billigste samlede regning, med levering og pakning regnet med.

Reglen (Julius): det må gerne være to butikker, men kun når det samlet er billigst. Det nytter
ikke at købe det hele ét sted og én agurk et andet, hvis besparelsen ædes af gebyrerne.

Fremgangsmåde, ud fra prissætningen af hele listen i hver butik (sammenlign.py):
  1. Alt i én butik, for hver butik.
  2. Delt: hver vare der, hvor den er billigst. Når en butik under minimumskøbet, flyttes de
     varer, der koster mindst ekstra, derover, indtil den gør. Kan det ikke lade sig gøre,
     droppes delingen.
  Et usikkert match (måske forkert vare) tæller med den sikre pris fra den anden butik, så et
  forkert billigt match ikke gør en butik billig. Det står i tjek_foerst.
  3. Den billigste mulighed, hvor alle varer findes, og alle brugte butikker når minimumskøbet,
     vinder. To butikker vælges kun, når det samlet er billigere end den bedste enkelte butik.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from handle_mcp.sammenlign import Butiksresultat


@dataclass
class Mulighed:
    navn: str  # "nemlig", "bilka" eller "nemlig + bilka"
    fordeling: dict[str, list[int]]  # butik -> linjenumre i indkøbslisten
    varer: dict[str, Decimal]  # butik -> varer i alt
    gebyrer: dict[str, Decimal]  # butik -> gebyrer i alt
    mangler: list[str] = field(default_factory=list)
    under_minimum: list[str] = field(default_factory=list)
    usikre: list[str] = field(default_factory=list)  # tæller med, men skal tjekkes af Claude

    @property
    def i_alt(self) -> Decimal:
        return sum(self.varer.values(), Decimal(0)) + sum(self.gebyrer.values(), Decimal(0))

    @property
    def gyldig(self) -> bool:
        return not self.mangler and not self.under_minimum


def _gebyr(r: Butiksresultat) -> Decimal:
    return sum(r.gebyrer.values(), Decimal(0))


def _linje(r: Butiksresultat, i: int):
    return r.linjer[i] if i < len(r.linjer) else None


def _beloeb(r: Butiksresultat, i: int, alle: dict[str, Butiksresultat] | None = None) -> Decimal | None:
    """Prisen for linjen, eller None hvis varen ikke findes.

    Et USIKKERT match tæller med den sikre pris fra en anden butik, hvis der er en. Fundet live:
    usikre match (majsmel for nakkekoteletter, ketchup for salt) talte med deres lave pris, så
    butikken med flest forkerte match så billigst ud. Nu kan et forkert match hverken gøre en
    butik billig eller smide den ud; linjen står som usikker, og Claude tjekker den."""
    ln = _linje(r, i)
    if not ln or not ln.valgt:
        return None
    if ln.sikker:
        return ln.beloeb
    sikre = [o.beloeb for b in (alle or {}).values() if b is not r and (o := _linje(b, i)) and o.valgt and o.sikker]
    return min(sikre) if sikre else ln.beloeb


def _mulighed(navn: str, fordeling: dict[str, list[int]], res: dict[str, Butiksresultat], liste: list[str]) -> Mulighed:
    varer, gebyrer, mangler, under, usikre = {}, {}, [], [], []
    for butik, linjer in fordeling.items():
        if not linjer:
            continue
        r = res[butik]
        sum_ = Decimal(0)
        for i in linjer:
            b = _beloeb(r, i, res)
            if b is None:
                mangler.append(liste[i])
                continue
            sum_ += b
            if not _linje(r, i).sikker:
                usikre.append(f"{butik}: {liste[i]} → {_linje(r, i).valgt.navn}")
        varer[butik], gebyrer[butik] = sum_, _gebyr(r)
        if r.minimumskoeb and sum_ < r.minimumskoeb:
            under.append(butik)
    return Mulighed(navn, {b: l for b, l in fordeling.items() if l}, varer, gebyrer, mangler, under, usikre)


OVERVEJ_GRAENSE = Decimal(50)  # mindre besparelser er ikke besværet med erstatninger værd
MAKS_HULLER = 3


def _billigere_med_huller(muligheder: list[Mulighed], bedst: Mulighed, res: dict[str, Butiksresultat], liste: list[str]) -> dict | None:
    """En enkelt butik, der kun er droppet, fordi den mangler få varer, men ellers er klart
    billigst. Fundet live: Bilka var ca. 300 kr billigere, men blev droppet for én manglende laks.
    De manglende varer skønnes til prisen i den anbefalede løsning."""
    bedste = None
    for m in muligheder:
        if len(m.fordeling) != 1 or m is bedst or not m.mangler or m.under_minimum or len(m.mangler) > MAKS_HULLER:
            continue
        (butik,) = m.fordeling
        huller = [i for i, s in enumerate(liste) if s in m.mangler]
        andre = [r for b, r in res.items() if b != butik]
        skoen = m.i_alt + sum((min((p for r in andre if (p := _beloeb(r, i, res)) is not None), default=Decimal(0)) for i in huller), Decimal(0))
        besparelse = bedst.i_alt - skoen
        if besparelse >= OVERVEJ_GRAENSE and (bedste is None or besparelse > bedste["besparelse"]):
            bedste = {"butik": butik, "mangler": m.mangler, "skoennet_i_alt": skoen, "besparelse": besparelse}
    return bedste


def find_billigste(resultater: list[Butiksresultat]) -> dict:
    """Sammenlign mulighederne og giv en anbefaling, som Claude og husstanden kan forstå."""
    res = {r.butik: r for r in resultater if not r.fejl}
    if not res:
        return {"anbefaling": None, "forklaring": "Ingen butik kunne prissætte listen."}
    liste = [ln.soegning for ln in next(iter(res.values())).linjer]
    n = len(liste)
    muligheder = [_mulighed(b, {b: list(range(n))}, res, liste) for b in res]
    ikke_delt = ""

    if len(res) == 2:
        a, b = res
        delt: dict[str, list[int]] = {a: [], b: []}
        for i in range(n):
            pa, pb = _beloeb(res[a], i, res), _beloeb(res[b], i, res)
            if pa is None and pb is None:
                delt[a].append(i)  # findes ingen af stederne: tæller som mangler
            elif pb is None or (pa is not None and pa <= pb):
                delt[a].append(i)
            else:
                delt[b].append(i)
        # Under minimumskøb? Flyt de varer, der koster mindst ekstra, over til butikken.
        for lav, hoej in ((a, b), (b, a)):
            minimum = res[lav].minimumskoeb or Decimal(0)
            while delt[lav] and delt[hoej]:
                i_lav = sum((_beloeb(res[lav], i, res) or Decimal(0) for i in delt[lav]), Decimal(0))
                if i_lav >= minimum:
                    break
                kandidater = [(_beloeb(res[lav], i, res) - _beloeb(res[hoej], i, res), i) for i in delt[hoej] if _beloeb(res[lav], i, res) is not None]
                if not kandidater:
                    break
                _, flyt = min(kandidater)
                delt[hoej].remove(flyt)
                delt[lav].append(flyt)
        if delt[a] and delt[b]:
            muligheder.append(_mulighed(f"{a} + {b}", delt, res, liste))
        else:
            ikke_delt = (
                f"At dele kan ikke lade sig gøre: begge kurve skal nå minimumskøbet "
                f"({a} {res[a].minimumskoeb or 0:.0f} kr, {b} {res[b].minimumskoeb or 0:.0f} kr), og listen er for lille til to kurve."
            )

    gyldige = [m for m in muligheder if m.gyldig]
    kandidater = gyldige or sorted(muligheder, key=lambda m: (len(m.mangler), len(m.under_minimum), m.i_alt))[:1]
    bedst = min(kandidater, key=lambda m: m.i_alt)
    enkelte = [m for m in gyldige if len(m.fordeling) == 1]
    bedste_enkelt = min(enkelte, key=lambda m: m.i_alt) if enkelte else None

    if len(bedst.fordeling) == 2 and bedste_enkelt:
        besparelse = bedste_enkelt.i_alt - bedst.i_alt
        forklaring = (
            f"Del indkøbet: {bedst.navn} koster {bedst.i_alt:.2f} kr i alt med begge butikkers gebyrer, "
            f"{besparelse:.2f} kr mindre end alt i {bedste_enkelt.navn}."
        )
    else:
        besparelse = Decimal(0)
        forklaring = f"Køb alt i {bedst.navn}: {bedst.i_alt:.2f} kr i alt med gebyrer."
        delt_m = next((m for m in muligheder if len(m.fordeling) == 2), None)
        if delt_m and delt_m.gyldig and delt_m.i_alt >= bedst.i_alt:
            forklaring += f" At dele mellem butikkerne ville koste {delt_m.i_alt - bedst.i_alt:.2f} kr mere, fordi gebyrerne æder besparelsen."
        elif delt_m and delt_m.under_minimum:
            forklaring += " At dele kan ikke betale sig: den ene kurv ville være under minimumskøbet."
        elif ikke_delt:
            forklaring += " " + ikke_delt
        andre = [m for m in gyldige if m is not bedst and len(m.fordeling) == 1]
        if andre:
            forklaring += f" Alt i {andre[0].navn} ville koste {andre[0].i_alt - bedst.i_alt:.2f} kr mere."
    if not bedst.gyldig:
        forklaring += f" OBS: mangler {', '.join(bedst.mangler) or 'intet'}; under minimum i {', '.join(bedst.under_minimum) or 'ingen'}."
    overvej = _billigere_med_huller(muligheder, bedst, res, liste)
    if overvej:
        forklaring += (
            f" MEN: alt i {overvej['butik']} ville koste ca. {overvej['skoennet_i_alt']:.0f} kr "
            f"({overvej['besparelse']:.0f} kr mindre), hvis I finder erstatninger for: {', '.join(overvej['mangler'])}. "
            f"Søg efter erstatninger i {overvej['butik']}, før du vælger {bedst.navn}."
        )
    return {
        "anbefaling": bedst.navn,
        "i_alt": bedst.i_alt,
        "besparelse_ved_deling": besparelse,
        "fordeling": {butik: [liste[i] for i in sorted(linjer)] for butik, linjer in bedst.fordeling.items()},
        "varer_pr_butik": bedst.varer,
        "gebyrer_pr_butik": bedst.gebyrer,
        "muligheder": {m.navn: {"i_alt": m.i_alt, "mangler": m.mangler, "under_minimum": m.under_minimum, "usikre": m.usikre} for m in muligheder},
        "tjek_foerst": bedst.usikre,
        "overvej": overvej,  # usikre match i den anbefalede løsning: søg igen, før kurven fyldes
        "forklaring": forklaring,
    }
