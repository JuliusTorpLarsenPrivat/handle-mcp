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

from handle_mcp import beholdning, historik, login, profil, samling
from handle_mcp.butikker import ButikFejl, hent_butik
from handle_mcp.data import til_json
from handle_mcp.lokal import LokalFejl
from handle_mcp.persondata import rens
from handle_mcp.maengde import behov
from handle_mcp.sammenlign import antal_pakker, er_oeko, hent_tilbud, kandidater, pris_for_behov, rangeret, sammenlign, tilbud_tip
from handle_mcp.vagt import VagtFejl

mcp = MCPServer(
    name="handle",
    instructions=(
        "Dagligvarer fra nemlig.com og BilkaToGo. Du kan søge, se tilbud og opskrifter, "
        "sammenligne priser og ændre antal i kurven. Du kan også huske, hvad brugerne har "
        "hjemme (beholdning), og tage højde for det i madplaner. Til madplaner: læs vis_profil "
        "(personer, ugebudget, præferencer, ting at undgå), vælg helst retter fra vis_opskrifter "
        "(retter brugerne stoler på), og sig til, hvis prisen går over ugebudgettet. "
        "Vær grundig, når du vælger varer: tag ikke bare første bedste eller opskriftens "
        "standardvare. Skriv mængden med i søgningen, prøv flere søgetekster, gå alternativerne "
        "igennem og vælg ud fra pris, mængde og kvalitet (rå varer, økologi hvis profilen siger "
        "det, ikke ultraforarbejdet). Fortæl kort, hvorfor du valgte som du gjorde. "
        "Du kan ALDRIG bestille, betale eller vælge "
        "leveringstid: det gør brugeren selv på butikkens hjemmeside. Spørg altid brugeren, "
        "før du ændrer kurven, og vis bagefter hvad der ligger i den."
    ),
)

LAESER = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
SKRIVER = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
LOKAL_LAESER = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LOKAL_SKRIVER = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
ALLE = ["nemlig", "bilka"]


def _butik(navn: str):
    """Butikken, eller en forståelig fejl til Claude ved et ukendt navn (fx "coop")."""
    try:
        return hent_butik(navn)
    except ButikFejl as fejl:
        raise ToolError(str(fejl)) from None


async def _koer(kald) -> Any:
    """Kør et butikskald, rens persondata og gør fejl forståelige for Claude."""
    try:
        return rens(til_json(await kald))
    except (ButikFejl, VagtFejl, login.IntetLogin) as fejl:
        raise ToolError(str(fejl)) from None


@mcp.tool(annotations=LAESER)
async def soeg_varer(
    tekst: str, butik: str = "nemlig", antal: int = 10, billigst_foerst: bool = False, oekologisk: bool = False
) -> Any:
    """Søg efter varer i én butik (nemlig eller bilka). Giver pris, enhedspris, tilbud og lager.
    Butikken sorterer efter popularitet, så de billigste varer er tit ikke i toppen. Sæt
    `billigst_foerst=True` for at gennemsøge flere hits og ugens tilbud og få de relevante varer
    på lager sorteret efter det billigste køb. Står der en mængde i teksten ("500 g hakket
    oksekød"), sorteres efter prisen for at dække den. Brug det, når prisen betyder noget, og
    prøv flere søgetekster (fx "kyllingebryst øko", "kyllingeinderfilet"), før du vælger."""
    antal = max(1, min(antal, 30))
    if not billigst_foerst:
        return await _koer(_butik(butik).soeg(tekst, antal))

    async def billigst() -> list:
        b = _butik(butik)
        rent, behov_ = behov(tekst)
        varer = await kandidater(b, rent, await hent_tilbud(b))
        return [
            {
                "vare": v,
                "pakker": antal_pakker(v, behov_),
                "pris_for_behov": pris_for_behov(v, behov_),
                "oeko": er_oeko(v),
                "tip": tilbud_tip(v, antal_pakker(v, behov_) or 1),
            }
            for _, v in rangeret(rent, varer, behov_, oekologisk)[:antal]
        ]

    return await _koer(billigst())


@mcp.tool(annotations=LAESER)
async def ugens_tilbud(butik: str = "nemlig", antal: int = 25) -> Any:
    """Ugens tilbud i én butik. nemlig: Skarp pris, Priskup, Dagstilbud og Undgå madspild."""
    antal = max(1, min(antal, 50))
    # nemlig har flere tilbudslister, og butikken giver `antal` pr. liste: skær ned til i alt `antal`
    return (await _koer(_butik(butik).tilbud(antal)))[:antal]


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
async def sammenlign_liste(
    varer: list[str], antal: list[int] | None = None, butikker: list[str] | None = None, oekologisk: bool = False
) -> Any:
    """Prissæt en indkøbsliste i flere butikker, med gebyrer, og find den billigste.
    `varer` er fx ["500 g hakket oksekød", "1 l letmælk", "2 stk løg", "pasta"]. Skriv mængden
    med, når du kender den: så vælges de pakker, der dækker behovet billigst, i stedet for den
    laveste kg-pris (som kan være en alt for stor pakke). `antal` ganger hele linjen (standard 1).
    `oekologisk=True` lader økovarer koste op til 50 % mere og stadig blive valgt.
    Hver linje har op til 4 alternativer. Valget er kun et forslag ud fra pris og ord: GÅ
    ALTERNATIVERNE IGENNEM og vælg selv ud fra kvalitet (rå vare frem for færdigret, ikke
    ultraforarbejdet, rigtig vare frem for smagsvariant, profilens præferencer). Usikre linjer
    skal altid tjekkes, evt. med en bedre søgetekst, før kurven fyldes."""
    antal = antal or []
    liste = [(v, antal[i] if i < len(antal) else 1) for i, v in enumerate(varer)]
    try:
        resultater = await sammenlign([hent_butik(b) for b in butikker or ALLE], liste, oekologisk)
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
                    {
                        "soegning": ln.soegning,
                        "behov": str(ln.behov) if ln.behov else None,
                        "antal": ln.antal,
                        "valgt": ln.valgt,
                        "pakker": ln.pakker,
                        "beloeb": ln.beloeb,
                        "tip": ln.tip,
                        "sikker": ln.sikker,
                        "alternativer": [
                            {"vare": a, "pakker": antal_pakker(a, ln.behov) or 1, "oeko": er_oeko(a)} for a in ln.alternativer
                        ],
                    }
                    for ln in r.linjer
                ],
            }
        )
    return rens(til_json(ud))


@mcp.tool(annotations=LAESER)
async def vis_kurv(butik: str = "nemlig") -> Any:
    """Kurvens linjer, gebyrer, total, minimumskøb og valgt leveringstid."""
    return await _koer(_butik(butik).kurv())


@mcp.tool(annotations=SKRIVER)
async def saet_antal_i_kurv(butik: str, antal: dict[str, int]) -> Any:
    """Sæt antal af varer i kurven: {"varenummer": antal}. Antallet er det samlede antal
    (ikke et tillæg), og 0 fjerner varen. Ændrer KUN kurven. Spørg brugeren først."""
    if any(n < 0 or n > 50 for n in antal.values()):
        raise ToolError("Antal skal være mellem 0 og 50.")
    return await _koer(_butik(butik).saet_antal(antal))


@mcp.tool(annotations=LOKAL_LAESER)
async def vis_beholdning() -> Any:
    """Det I har hjemme (køleskab, fryser, skab), som I sidst har fortalt det.
    `noteret` er hvornår hver vare sidst blev opdateret: gamle varer kan være brugt eller for gamle.
    Brug den til madplaner og indkøbslister, så I ikke køber det, I allerede har."""
    try:
        return til_json(beholdning.hent())
    except LokalFejl as fejl:
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
    except LokalFejl as fejl:
        raise ToolError(str(fejl)) from None
    return til_json({"beholdning": varer, "ikke_fundet": ukendte})


def _lokalt(kald, *args, **kwargs) -> Any:
    """Kør noget mod de lokale filer og gør fejl forståelige for Claude."""
    try:
        return til_json(kald(*args, **kwargs))
    except LokalFejl as fejl:
        raise ToolError(str(fejl)) from None


@mcp.tool(annotations=LOKAL_LAESER)
async def koebsmoenster(andel_af_indkoeb: float = 1.0) -> Any:
    """Hvad husstanden faktisk køber, ud fra gemte kvitteringer (i dag fra Coop/Brugsen):
    basisvarer med typisk interval og dage siden sidst (`snart_tom`), favoritter pr. madkategori,
    hvad pengene går til, øko-andel og forbrug pr. uge. `andel_af_indkoeb` er hvor stor en del af
    indkøbene kvitteringerne dækker (se profilens note, fx 0.7), så det samlede budget kan skønnes.
    Brug det til madplaner (retter med det, de kan lide), til at foreslå basisvarer der snart er
    tomme, og til at sammenligne ugebudgettet med det faktiske forbrug."""
    return _lokalt(historik.moenster, andel_af_indkoeb)


@mcp.tool(annotations=LOKAL_LAESER)
async def vis_profil() -> Any:
    """Husstandens profil: antal personer, ugebudget (kr., inkl. levering), præferencer og ting
    at undgå (allergier, det I ikke kan lide). Læs den, før du laver madplan eller indkøbsliste."""
    return _lokalt(profil.hent)


@mcp.tool(annotations=LOKAL_SKRIVER)
async def opdater_profil(
    personer: int | None = None,
    ugebudget_kr: float | None = None,
    praeferencer: list[str] | None = None,
    undgaa: list[str] | None = None,
    note: str | None = None,
) -> Any:
    """Ret husstandens profil. Kun de felter du sender, ændres. Lister erstattes helt, så send
    hele den nye liste (læs vis_profil først). ugebudget_kr=0 fjerner budgettet."""
    return _lokalt(profil.opdater, personer, ugebudget_kr, praeferencer, undgaa, note)


@mcp.tool(annotations=LOKAL_LAESER)
async def vis_opskrifter(tag: str | None = None, soeg: str | None = None) -> Any:
    """Jeres egen opskriftssamling: retter I kender og stoler på. Vælg retter herfra til
    madplaner, før du foreslår nye. `tag` fx "hverdag" eller "vegetar", `soeg` et ord i navn,
    ingredienser eller note."""
    return _lokalt(samling.hent, tag, soeg)


@mcp.tool(annotations=LOKAL_SKRIVER)
async def gem_opskrift(
    navn: str,
    link: str | None = None,
    ingredienser: list[str] | None = None,
    personer: int | None = None,
    tags: list[str] | None = None,
    note: str | None = None,
) -> Any:
    """Gem en ret i jeres opskriftssamling. Findes navnet, erstattes den. Brug link til
    nemlig-opskrifter (hent_opskrift kan så give varerne). Ingredienser er fri tekst, fx
    "500 g hakket oksekød". Gem kun det, brugeren beder om."""
    return _lokalt(samling.gem, navn, link, ingredienser, personer, tags, note)


@mcp.tool(annotations=LOKAL_SKRIVER)
async def fjern_opskrift(navn: str) -> Any:
    """Fjern en ret fra opskriftssamlingen. Spørg brugeren først."""
    if not _lokalt(samling.fjern, navn):
        raise ToolError(f"Der er ingen opskrift, der hedder {navn!r}.")
    return {"fjernet": navn}


@mcp.tool(annotations=LAESER)
async def leveringstider(butik: str = "nemlig", dage: int = 5) -> Any:
    """Ledige leveringstider med pris. Kun til orientering: brugeren vælger selv tiden."""
    return await _koer(_butik(butik).leveringstider(min(dage, 14)))


@mcp.tool(annotations=LAESER)
async def tidligere_ordrer(butik: str = "nemlig", antal: int = 10, ordre_id: str | None = None) -> Any:
    """Tidligere ordrer. Med ordre_id: varerne i den ordre (godt til 'det vi plejer at købe')."""
    b = _butik(butik)
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
