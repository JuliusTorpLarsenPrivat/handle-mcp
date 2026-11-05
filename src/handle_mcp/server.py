"""MCP-serveren: de værktøjer (knapper), som Claude kan trykke på.

Hver funktion med @mcp.tool bliver et værktøj. Funktionens navn er værktøjets navn,
docstringen er beskrivelsen, som Claude læser, og parametrene bliver værktøjets felter.

Serveren taler med Claude Desktop over stdin/stdout (det hedder 'stdio'). Derfor må intet
andet skrives til stdout: log går til stderr.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from handle_mcp import beholdning, login
from handle_mcp.butikker import ButikFejl, hent_butik
from handle_mcp.data import til_json
from handle_mcp.persondata import rens
from handle_mcp.sammenlign import hent_tilbud, kandidater, rangeret, sammenlign
from handle_mcp.vagt import VagtFejl

mcp = MCPServer(
    name="handle",
    instructions=(
        "Dagligvarer fra nemlig.com og BilkaToGo. Du kan søge, se tilbud og opskrifter, "
        "sammenligne priser og ændre antal i kurven. Du kan også huske, hvad brugerne har "
        "hjemme (beholdning), og tage højde for det i madplaner. Du kan ALDRIG bestille, betale eller vælge "
        "leveringstid: det gør brugeren selv på butikkens hjemmeside. Spørg altid brugeren, "
        "før du ændrer kurven, og vis bagefter hvad der ligger i den."
    ),
)

LAESER = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
SKRIVER = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
LOKAL_LAESER = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LOKAL_SKRIVER = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
ALLE = ["nemlig", "bilka"]


async def _koer(kald) -> Any:
    """Kør et butikskald, rens persondata og gør fejl forståelige for Claude."""
    try:
        return rens(til_json(await kald))
    except (ButikFejl, VagtFejl, login.IntetLogin) as fejl:
        raise ToolError(str(fejl)) from None


@mcp.tool(annotations=LAESER)
async def soeg_varer(tekst: str, butik: str = "nemlig", antal: int = 10, billigst_foerst: bool = False) -> Any:
    """Søg efter varer i én butik (nemlig eller bilka). Giver pris, enhedspris, tilbud og lager.
    Butikken sorterer efter popularitet, så de billigste varer er tit ikke i toppen. Sæt
    `billigst_foerst=True` for at gennemsøge flere hits og ugens tilbud og få de relevante varer
    på lager sorteret efter enhedspris (kr. pr. kg/l). Brug det, når prisen betyder noget, fx til madplaner."""
    antal = max(1, min(antal, 30))
    if not billigst_foerst:
        return await _koer(hent_butik(butik).soeg(tekst, antal))

    async def billigst() -> list:
        b = hent_butik(butik)
        varer = await kandidater(b, tekst, await hent_tilbud(b))
        return [v for _, v in rangeret(tekst, varer)[:antal]]

    return await _koer(billigst())


@mcp.tool(annotations=LAESER)
async def ugens_tilbud(butik: str = "nemlig", antal: int = 25) -> Any:
    """Ugens tilbud i én butik. nemlig: Skarp pris, Priskup, Dagstilbud og Undgå madspild."""
    return await _koer(hent_butik(butik).tilbud(min(antal, 50)))


@mcp.tool(annotations=LAESER)
async def soeg_opskrifter(tekst: str, antal: int = 5) -> Any:
    """Søg i nemligs opskrifter. Giver navn og link. Brug hent_opskrift for varer og pris."""
    return await _koer(hent_butik("nemlig").soeg_opskrifter(tekst, min(antal, 20)))


@mcp.tool(annotations=LAESER)
async def hent_opskrift(opskrift: str, personer: int | None = None) -> Any:
    """Varerne til en nemlig-opskrift (link eller id) for et antal personer, med pris.
    Varer markeret har_hjemme (salt, olie osv.) er ikke med i prisen."""
    return await _koer(hent_butik("nemlig").opskrift(opskrift, personer))


@mcp.tool(annotations=LAESER)
async def sammenlign_liste(varer: list[str], antal: list[int] | None = None, butikker: list[str] | None = None) -> Any:
    """Prissæt en indkøbsliste i flere butikker, med gebyrer, og find den billigste.
    `varer` er fx ["letmælk 1 l", "hakkede tomater", "pasta"], `antal` samme rækkefølge (standard 1).
    Usikre match er markeret og bør tjekkes, før kurven fyldes."""
    antal = antal or []
    liste = [(v, antal[i] if i < len(antal) else 1) for i, v in enumerate(varer)]
    try:
        resultater = await sammenlign([hent_butik(b) for b in butikker or ALLE], liste)
    except ButikFejl as fejl:
        raise ToolError(str(fejl)) from None
    ud = []
    for r in resultater:
        ud.append(
            {
                "butik": r.butik,
                "fejl": r.fejl,
                "i_alt": r.i_alt,
                "varer_i_alt": r.varer_i_alt,
                "gebyrer": r.gebyrer,
                "minimumskoeb": r.minimumskoeb,
                "under_minimum": bool(r.minimumskoeb and r.varer_i_alt < r.minimumskoeb),
                "mangler": r.mangler,
                "usikre": r.usikre,
                "linjer": [
                    {"soegning": ln.soegning, "antal": ln.antal, "valgt": ln.valgt, "beloeb": ln.beloeb, "sikker": ln.sikker, "alternativer": ln.alternativer}
                    for ln in r.linjer
                ],
            }
        )
    return rens(til_json(ud))


@mcp.tool(annotations=LAESER)
async def vis_kurv(butik: str = "nemlig") -> Any:
    """Kurvens linjer, gebyrer, total, minimumskøb og valgt leveringstid."""
    return await _koer(hent_butik(butik).kurv())


@mcp.tool(annotations=SKRIVER)
async def saet_antal_i_kurv(butik: str, antal: dict[str, int]) -> Any:
    """Sæt antal af varer i kurven: {"varenummer": antal}. Antallet er det samlede antal
    (ikke et tillæg), og 0 fjerner varen. Ændrer KUN kurven. Spørg brugeren først."""
    if any(n < 0 or n > 50 for n in antal.values()):
        raise ToolError("Antal skal være mellem 0 og 50.")
    return await _koer(hent_butik(butik).saet_antal(antal))


@mcp.tool(annotations=LOKAL_LAESER)
async def vis_beholdning() -> Any:
    """Det I har hjemme (køleskab, fryser, skab), som I sidst har fortalt det.
    `noteret` er hvornår hver vare sidst blev opdateret: gamle varer kan være brugt eller for gamle.
    Brug den til madplaner og indkøbslister, så I ikke køber det, I allerede har."""
    try:
        return til_json(beholdning.hent())
    except beholdning.BeholdningFejl as fejl:
        raise ToolError(str(fejl)) from None


@mcp.tool(annotations=LOKAL_SKRIVER)
async def opdater_beholdning(
    tilfoej: list[beholdning.NyVare] | None = None, fjern: list[str] | None = None, erstat_alt: bool = False
) -> Any:
    """Gem hvad I har hjemme. Ændrer kun en lokal liste, aldrig butikkernes kurv.
    `tilfoej`: [{"navn": "løg", "maengde": "2 stk", "sted": "køleskab"}]. Findes navnet, rettes det.
    `fjern`: navne på varer der er brugt op.
    `erstat_alt=True`: listen er hele beholdningen (fx efter et kig i køleskabet), og alt andet slettes.
    Spørg, hvis det er uklart om noget er brugt op, og vis bagefter den nye beholdning."""
    try:
        varer, ukendte = beholdning.opdater(tilfoej, fjern, erstat_alt)
    except beholdning.BeholdningFejl as fejl:
        raise ToolError(str(fejl)) from None
    return til_json({"beholdning": varer, "ikke_fundet": ukendte})


@mcp.tool(annotations=LAESER)
async def leveringstider(butik: str = "nemlig", dage: int = 5) -> Any:
    """Ledige leveringstider med pris. Kun til orientering: brugeren vælger selv tiden."""
    return await _koer(hent_butik(butik).leveringstider(min(dage, 14)))


@mcp.tool(annotations=LAESER)
async def tidligere_ordrer(butik: str = "nemlig", antal: int = 10, ordre_id: str | None = None) -> Any:
    """Tidligere ordrer. Med ordre_id: varerne i den ordre (godt til 'det vi plejer at købe')."""
    b = hent_butik(butik)
    return await _koer(b.ordre(ordre_id) if ordre_id else b.ordrer(min(antal, 30)))


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logger ellers hele URL'er
    if len(sys.argv) > 1:
        from handle_mcp.cli import kommando

        sys.exit(kommando(sys.argv[1:]))
    mcp.run("stdio")


if __name__ == "__main__":
    main()
