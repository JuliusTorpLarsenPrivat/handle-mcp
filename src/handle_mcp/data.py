"""De få typer, som begge butikker oversættes til.

Butikkerne svarer i hver sit format (nemlig i kroner med store forbogstaver, Bilka i øre).
Butiksmodulerne oversætter til disse klasser, så resten af koden kun kender ét format.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass
class Vare:
    butik: str
    id: str  # butikkens varenummer, bruges til kurven
    navn: str
    pris: Decimal  # det du betaler nu, tilbud medregnet
    maengde: str | None = None  # fx "1 l" eller "500 g / Øko"
    maerke: str | None = None
    enhedspris: Decimal | None = None
    enhed: str | None = None  # kg, l eller stk
    tilbud: str | None = None  # fx "-20 %", "3 for 50 kr", "Gratis"
    foer_pris: Decimal | None = None
    max_antal: int | None = None
    paa_lager: bool = True
    ean: str | None = None


@dataclass
class Kurvlinje:
    id: str
    navn: str
    antal: int
    beloeb: Decimal  # hele linjen


@dataclass
class Kurv:
    butik: str
    linjer: list[Kurvlinje]
    varer_i_alt: Decimal
    gebyrer: dict[str, Decimal]
    i_alt: Decimal | None
    minimumskoeb: Decimal | None = None
    minimum_naaet: bool | None = None
    levering: str | None = None
    advarsler: list[str] = field(default_factory=list)


@dataclass
class AntalResultat:
    id: str
    oensket: int
    faktisk: int | None
    ok: bool
    besked: str | None = None


@dataclass
class Leveringstid:
    butik: str
    start: datetime
    slut: datetime
    pris: Decimal | None
    ledig: bool
    type: str | None = None
    note: str | None = None


@dataclass
class Opskrift:
    butik: str
    id: str
    navn: str
    link: str


@dataclass
class OpskriftVare:
    ingrediens: str
    vare_id: str
    navn: str
    antal: int
    pris: Decimal  # pr. stk.
    har_hjemme: bool = False  # nemlig regner salt, olie osv. som noget man har


@dataclass
class OpskriftDetaljer:
    butik: str
    id: str
    navn: str
    link: str
    personer: int | None
    tid: str | None
    varer: list[OpskriftVare]

    @property
    def pris(self) -> Decimal:
        return sum((v.pris * v.antal for v in self.varer if not v.har_hjemme), Decimal(0))


@dataclass
class Ordre:
    butik: str
    id: str
    status: str
    i_alt: Decimal | None
    dato: str | None
    linjer: list[Kurvlinje] = field(default_factory=list)


def til_json(obj: Any) -> Any:
    """Dataklasser, Decimal og datoer → noget MCP kan sende som JSON."""
    if hasattr(obj, "__dataclass_fields__"):
        d = asdict(obj)
        if isinstance(obj, OpskriftDetaljer):
            d["pris"] = obj.pris
        return til_json(d)
    if isinstance(obj, dict):
        return {k: til_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [til_json(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(round(obj, 2))
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj
