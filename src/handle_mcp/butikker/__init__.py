"""Butikkerne. Hver butik er en klasse med de samme metoder (se `Butik` nedenfor), så serveren
og sammenligningen kan behandle nemlig og Bilka ens."""

from __future__ import annotations

from typing import Protocol

from handle_mcp.data import AntalResultat, Kurv, Leveringstid, Opskrift, OpskriftDetaljer, Ordre, Vare


class ButikFejl(Exception):
    """En fejl, der giver mening at vise til brugeren."""


class Butik(Protocol):
    navn: str

    async def log_ind(self) -> None: ...
    async def soeg(self, tekst: str, antal: int = 10) -> list[Vare]: ...
    async def tilbud(self, antal: int = 25) -> list[Vare]: ...
    async def kurv(self) -> Kurv: ...
    async def saet_antal(self, antal: dict[str, int]) -> list[AntalResultat]: ...
    async def leveringstider(self, dage: int = 7) -> list[Leveringstid]: ...
    async def ordrer(self, antal: int = 10) -> list[Ordre]: ...
    async def ordre(self, ordre_id: str) -> Ordre: ...


class HarOpskrifter(Protocol):
    async def soeg_opskrifter(self, tekst: str, antal: int = 5) -> list[Opskrift]: ...
    async def opskrift(self, opskrift: str, personer: int | None = None) -> OpskriftDetaljer: ...


_butikker: dict[str, Butik] = {}


def hent_butik(navn: str) -> Butik:
    """Én forekomst pr. butik, så login genbruges mellem kald."""
    navn = navn.lower().replace("bilkatogo", "bilka")
    if navn not in _butikker:
        if navn == "nemlig":
            from handle_mcp.butikker.nemlig import Nemlig

            _butikker[navn] = Nemlig()
        elif navn == "bilka":
            from handle_mcp.butikker.bilka import Bilka

            _butikker[navn] = Bilka()
        else:
            raise ButikFejl(f"Kender ikke butikken {navn!r}. Brug nemlig eller bilka.")
    return _butikker[navn]
