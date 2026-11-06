"""Ugeplanen: madplan + præcis indkøbsliste, gemt med en kode, som I godkender.

Forløbet (startes manuelt, indtil mini-pc'en kan gøre det automatisk):
  1. Claude laver madplan og vælger varer, og gemmer det med gem_madplan → fx "UGE-4821".
  2. Planen sendes til godkendelse (SMS-tekst, som I selv sender, indtil videre).
  3. Ved "OK UGE-4821" fylder fyld_kurv_fra_madplan kurven med præcis de godkendte varer.
  4. I betaler selv i butikkens app. Automatisk betaling kommer tidligst, når vi har set en
     rigtig betaling og ved, at den kan laves sikkert (se IDEER.md).

Planerne ligger i madplaner.json på din egen maskine (se lokal.py).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo

from handle_mcp.lokal import LokalFejl, laes, skriv, tekst, tekster

TIDSZONE = ZoneInfo("Europe/Copenhagen")
GEMTE = 20  # de seneste planer gemmes
MAKS_LINJER = 80
MAKS_ANTAL = 20
PRISSTIGNING = Decimal("0.05")  # mere end 5 % dyrere i kurven end i planen = stop og spørg
STATUSSER = ("afventer", "kurv fyldt", "annulleret")


BUTIKKER = ("nemlig", "bilka")


class NyLinje(TypedDict):
    vare_id: str
    navn: str
    antal: int
    pris: float  # samlet pris for linjen i planen (med mængderabat)
    butik: NotRequired[str]  # kun nødvendig, når planen deler indkøbet på begge butikker


@dataclass
class Linje:
    vare_id: str
    navn: str
    antal: int
    pris: float
    butik: str | None = None  # None: planens butik (ældre planer)


@dataclass
class Madplan:
    kode: str
    butik: str
    oprettet: str
    retter: list[str]
    linjer: list[Linje]
    gebyrer: float = 0.0
    note: str | None = None
    status: str = "afventer"
    historik: list[str] = field(default_factory=list)

    @property
    def varer_i_alt(self) -> float:
        return round(sum(ln.pris for ln in self.linjer), 2)

    @property
    def i_alt(self) -> float:
        return round(self.varer_i_alt + self.gebyrer, 2)

    def linjer_pr_butik(self) -> dict[str, list[Linje]]:
        ud: dict[str, list[Linje]] = {}
        for ln in self.linjer:
            ud.setdefault(ln.butik or self.butik, []).append(ln)
        return ud


def _nu() -> str:
    return datetime.now(TIDSZONE).isoformat(timespec="minutes")


def _alle() -> list[Madplan]:
    ud = []
    for d in laes("madplaner").get("planer", []):
        d = {**d, "linjer": [Linje(**ln) for ln in d["linjer"]]}
        ud.append(Madplan(**d))
    return ud


def _gem_alle(planer: list[Madplan]) -> None:
    skriv("madplaner", {"planer": [asdict(p) for p in planer[-GEMTE:]]})


def gem(butik: str, retter: list[str], linjer: list[NyLinje], gebyrer: float = 0.0, note: str | None = None) -> Madplan:
    butik = (tekst(butik, "butik") or "").lower().replace(" + ", "+").replace("nemlig+bilka", "begge").replace("bilka+nemlig", "begge")
    if butik not in (*BUTIKKER, "begge"):
        raise LokalFejl("Butikken skal være nemlig, bilka eller begge.")
    if not linjer:
        raise LokalFejl("Planen har ingen varer.")
    if len(linjer) > MAKS_LINJER:
        raise LokalFejl(f"Højst {MAKS_LINJER} varer i en plan.")
    rene = []
    set_ids = set()
    for ln in linjer:
        vare_id = tekst(ln.get("vare_id"), "vare_id", 40)
        navn = tekst(ln.get("navn"), "navn", 120)
        antal, pris = ln.get("antal"), ln.get("pris")
        if not vare_id or not navn:
            raise LokalFejl("Hver vare skal have vare_id og navn (fra søgningen).")
        if not isinstance(antal, int) or not 1 <= antal <= MAKS_ANTAL:
            raise LokalFejl(f"Antal for {navn} skal være et heltal mellem 1 og {MAKS_ANTAL}.")
        if not isinstance(pris, (int, float)) or pris < 0:
            raise LokalFejl(f"Prisen for {navn} mangler eller er negativ.")
        linjens_butik = (ln.get("butik") or "").lower() or (butik if butik != "begge" else "")
        if linjens_butik not in BUTIKKER:
            raise LokalFejl(f"Skriv butik (nemlig eller bilka) på {navn}, når planen bruger begge butikker.")
        if butik != "begge" and linjens_butik != butik:
            raise LokalFejl(f"{navn} er fra {linjens_butik}, men planen er kun for {butik}. Brug butik='begge'.")
        if (linjens_butik, vare_id) in set_ids:
            raise LokalFejl(f"{navn} står to gange: læg antallene sammen i én linje.")
        set_ids.add((linjens_butik, vare_id))
        rene.append(Linje(vare_id, navn, antal, round(float(pris), 2), linjens_butik))
    if butik == "begge" and len({ln.butik for ln in rene}) == 1:
        butik = rene[0].butik  # alle varer endte ét sted
    planer = _alle()
    brugte = {p.kode for p in planer}
    while (kode := f"UGE-{secrets.randbelow(9000) + 1000}") in brugte:
        pass
    plan = Madplan(
        kode=kode,
        butik=butik,
        oprettet=_nu(),
        retter=tekster(retter, "retter", 14, 120),
        linjer=rene,
        gebyrer=round(float(gebyrer or 0), 2),
        note=tekst(note, "note", 500),
        historik=[f"{_nu()} oprettet"],
    )
    # En ny plan erstatter de ventende: så gælder "bestil" altid den seneste version
    for gammel in planer:
        if gammel.status == "afventer":
            gammel.status = "annulleret"
            gammel.historik.append(f"{_nu()} erstattet af {kode}")
    _gem_alle(planer + [plan])
    return plan


def hent(kode: str | None = None) -> Madplan:
    planer = _alle()
    if not planer:
        raise LokalFejl("Der er ingen gemte madplaner.")
    if kode is None:
        return planer[-1]
    kode = kode.strip().upper()
    for p in planer:
        if p.kode == kode:
            return p
    raise LokalFejl(f"Der er ingen madplan med koden {kode}.")


def saet_status(kode: str, status: str, besked: str) -> Madplan:
    if status not in STATUSSER:
        raise LokalFejl(f"Ukendt status: {status}")
    planer = _alle()
    for p in planer:
        if p.kode == kode:
            p.status = status
            p.historik.append(f"{_nu()} {besked}")
            _gem_alle(planer)
            return p
    raise LokalFejl(f"Der er ingen madplan med koden {kode}.")


KODE_I_TEKST = re.compile(r"UGE[\s-]*(\d{4})\b")
JA_ORD = re.compile(r"\b(OK|OKAY|JA|GODKEND|GODKENDT|GODKENDER|BESTIL|KØB)\b")
# Et kort svar må KUN bestå af disse ord: "bestil", "ja tak", "ok bestil det bare".
# "Ja, men uden laks" er ikke en godkendelse, for "men", "uden" og "laks" er ikke med.
KORT_JA = {"ok", "okay", "ja", "jep", "yes", "godkend", "godkendt", "godkender", "bestil", "bestilt",
           "køb", "kør", "go", "det", "den", "dem", "tak", "bare", "gerne", "lortet", "så", "skidtet", "planen"}  # fmt: skip
JA_KERNE = {"ok", "okay", "ja", "jep", "yes", "godkend", "godkendt", "godkender", "bestil", "køb", "kør", "go"}
MAKS_ALDER_TIMER = 48


def er_kort_ja(svar: str) -> bool:
    ord_ = re.findall(r"[a-zæøå]+", svar.lower())
    return bool(ord_) and len(ord_) <= 6 and set(ord_) <= KORT_JA and bool(set(ord_) & JA_KERNE)


def eneste_ventende() -> Madplan | None:
    """Den ene plan, der venter på godkendelse (højst 48 timer gammel), ellers None."""
    graense = datetime.now(TIDSZONE).timestamp() - MAKS_ALDER_TIMER * 3600
    ventende = [p for p in _alle() if p.status == "afventer" and datetime.fromisoformat(p.oprettet).timestamp() >= graense]
    return ventende[0] if len(ventende) == 1 else None


def er_godkendt(kode: str, svar: str) -> bool:
    """Har brugeren godkendt netop denne plan? Enten med koden ("OK UGE-4821") eller med et
    kort ja ("bestil", "ja tak"), når netop denne plan er den eneste, der venter."""
    s = svar.upper()
    koder = {f"UGE-{m}" for m in KODE_I_TEKST.findall(s)}
    if koder:
        return bool(JA_ORD.search(s)) and koder == {kode}
    ventende = eneste_ventende()
    return er_kort_ja(svar) and ventende is not None and ventende.kode == kode


def sms_tekst(p: Madplan) -> str:
    """Kort tekst til godkendelse, fx på SMS. Under 600 tegn, så den kan læses på en telefon."""
    navne = {"nemlig": "nemlig.com", "bilka": "BilkaToGo"}
    butik = " + ".join(navne[b] for b in p.linjer_pr_butik())
    retter = "\n".join(f"- {r}" for r in p.retter) or "- (ingen retter angivet)"
    return (
        f"Ugeplan {p.kode} ({butik})\n{retter}\n"
        f"{len(p.linjer)} {'vare' if len(p.linjer) == 1 else 'varer'}, i alt {p.i_alt:.0f} kr inkl. levering.\n"
        f"Svar \"bestil\" for at lægge varerne i kurven (betal selv i appen bagefter)."
    )
