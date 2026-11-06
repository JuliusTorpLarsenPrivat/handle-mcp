"""MCP-serveren: de værktøjer (knapper), som Claude kan trykke på.

Hver funktion med @mcp.tool bliver et værktøj. Funktionens navn er værktøjets navn,
docstringen er beskrivelsen, som Claude læser, og parametrene bliver værktøjets felter.

Serveren taler med Claude Desktop over stdin/stdout (det hedder 'stdio'). Derfor må intet
andet skrives til stdout: log går til stderr.
"""

from __future__ import annotations

import functools
import logging
import sys
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from handle_mcp import beholdning, data, genindlaes, historik, login, lokal, madplan, maengde, opskrifter_web, persondata, profil, samling, telegram, vagt
from handle_mcp import butikker as butik_modul
from handle_mcp import sammenlign as sml

# Modulerne slås op ved hvert kald (sml.rangeret, butik_modul.ButikFejl …) i stedet for at blive
# importeret som navne. Så kan genindlaes.py skifte koden ud, mens serveren kører.

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


def _ny_kode() -> None:
    """Indlæs ændret kode før kaldet (se genindlaes.py). En fejl i ny kode vises for Claude,
    og den gamle kode bruges ikke halvt: intet er skiftet ud, før alle filer er tjekket."""
    try:
        genindlaes.tjek()
    except Exception as fejl:  # noqa: BLE001  fx en tastefejl i en fil, der lige er gemt
        raise ToolError(f"Ny kode i handle-mcp kunne ikke indlæses, så intet er ændret: {type(fejl).__name__}: {fejl}") from None


def vaerktoej(annotations: ToolAnnotations):
    """Som @mcp.tool, men tjekker for ny kode før hvert kald."""

    def pynt(funktion):
        @functools.wraps(funktion)
        async def med_ny_kode(*args, **kwargs):
            _ny_kode()
            return await funktion(*args, **kwargs)

        return mcp.tool(annotations=annotations)(med_ny_kode)

    return pynt


def hent_butik(navn: str):
    return butik_modul.hent_butik(navn)


def _butik(navn: str):
    """Butikken, eller en forståelig fejl til Claude ved et ukendt navn (fx "coop")."""
    try:
        return hent_butik(navn)
    except butik_modul.ButikFejl as fejl:
        raise ToolError(str(fejl)) from None


async def _koer(kald) -> Any:
    """Kør et butikskald, rens persondata og gør fejl forståelige for Claude."""
    try:
        return persondata.rens(data.til_json(await kald))
    except (butik_modul.ButikFejl, vagt.VagtFejl, login.IntetLogin) as fejl:
        raise ToolError(str(fejl)) from None


@vaerktoej(LAESER)
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
        rent, behov_ = maengde.behov(tekst)
        varer = await sml.kandidater(b, rent, await sml.hent_tilbud(b))
        return [
            {
                "vare": v,
                "pakker": sml.antal_pakker(v, behov_),
                "pris_for_behov": sml.pris_for_behov(v, behov_),
                "oeko": sml.er_oeko(v),
                "tip": sml.tilbud_tip(v, sml.antal_pakker(v, behov_) or 1),
            }
            for _, v in sml.rangeret(rent, varer, behov_, oekologisk)[:antal]
        ]

    return await _koer(billigst())


@vaerktoej(LAESER)
async def ugens_tilbud(butik: str = "nemlig", antal: int = 25) -> Any:
    """Ugens tilbud i én butik. nemlig: Skarp pris, Priskup, Dagstilbud og Undgå madspild."""
    antal = max(1, min(antal, 50))
    # nemlig har flere tilbudslister, og butikken giver `antal` pr. liste: skær ned til i alt `antal`
    return (await _koer(_butik(butik).tilbud(antal)))[:antal]


@vaerktoej(LAESER)
async def soeg_opskrifter(tekst: str, antal: int = 5) -> Any:
    """Søg i nemligs opskrifter. Giver navn og link. Brug hent_opskrift for varer og pris."""
    return await _koer(hent_butik("nemlig").soeg_opskrifter(tekst, min(antal, 20)))


@vaerktoej(LAESER)
async def hent_opskrift(opskrift: str, personer: int | None = None) -> Any:
    """Varerne til en nemlig-opskrift (link eller id) for et antal personer, med pris.
    Varer markeret har_hjemme (salt, olie osv.) er ikke med i prisen."""
    return await _koer(hent_butik("nemlig").opskrift(opskrift, personer))


@vaerktoej(LAESER)
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
        resultater = await sml.sammenlign([hent_butik(b) for b in butikker or ALLE], liste, oekologisk)
    except butik_modul.ButikFejl as fejl:
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
                            {"vare": a, "pakker": sml.antal_pakker(a, ln.behov) or 1, "oeko": sml.er_oeko(a)} for a in ln.alternativer
                        ],
                    }
                    for ln in r.linjer
                ],
            }
        )
    return persondata.rens(data.til_json(ud))


@vaerktoej(LAESER)
async def vis_kurv(butik: str = "nemlig") -> Any:
    """Kurvens linjer, gebyrer, total, minimumskøb og valgt leveringstid."""
    return await _koer(_butik(butik).kurv())


@vaerktoej(SKRIVER)
async def saet_antal_i_kurv(butik: str, antal: dict[str, int]) -> Any:
    """Sæt antal af varer i kurven: {"varenummer": antal}. Antallet er det samlede antal
    (ikke et tillæg), og 0 fjerner varen. Ændrer KUN kurven. Spørg brugeren først."""
    if any(n < 0 or n > 50 for n in antal.values()):
        raise ToolError("Antal skal være mellem 0 og 50.")
    return await _koer(_butik(butik).saet_antal(antal))


@vaerktoej(LOKAL_LAESER)
async def vis_beholdning() -> Any:
    """Det I har hjemme (køleskab, fryser, skab), som I sidst har fortalt det.
    `noteret` er hvornår hver vare sidst blev opdateret: gamle varer kan være brugt eller for gamle.
    Brug den til madplaner og indkøbslister, så I ikke køber det, I allerede har."""
    try:
        return data.til_json(beholdning.hent())
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None


@vaerktoej(LOKAL_SKRIVER)
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
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None
    return data.til_json({"beholdning": varer, "ikke_fundet": ukendte})


def _lokalt(kald, *args, **kwargs) -> Any:
    """Kør noget mod de lokale filer og gør fejl forståelige for Claude."""
    try:
        return data.til_json(kald(*args, **kwargs))
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None


@vaerktoej(LOKAL_LAESER)
async def koebsmoenster(andel_af_indkoeb: float = 1.0) -> Any:
    """Hvad husstanden faktisk køber, ud fra gemte kvitteringer (i dag fra Coop/Brugsen):
    basisvarer med typisk interval og dage siden sidst (`snart_tom`), favoritter pr. madkategori,
    hvad pengene går til, øko-andel og forbrug pr. uge. `andel_af_indkoeb` er hvor stor en del af
    indkøbene kvitteringerne dækker (se profilens note, fx 0.7), så det samlede budget kan skønnes.
    Brug det til madplaner (retter med det, de kan lide), til at foreslå basisvarer der snart er
    tomme, og til at sammenligne ugebudgettet med det faktiske forbrug."""
    return _lokalt(historik.moenster, andel_af_indkoeb)


@vaerktoej(LOKAL_LAESER)
async def vis_profil() -> Any:
    """Husstandens profil: antal personer, ugebudget (kr., inkl. levering), præferencer og ting
    at undgå (allergier, det I ikke kan lide). Læs den, før du laver madplan eller indkøbsliste."""
    return _lokalt(profil.hent)


@vaerktoej(LOKAL_SKRIVER)
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


@vaerktoej(LOKAL_LAESER)
async def vis_opskrifter(tag: str | None = None, soeg: str | None = None) -> Any:
    """Jeres egen opskriftssamling: retter I kender og stoler på. Vælg retter herfra til
    madplaner, før du foreslår nye. `tag` fx "hverdag" eller "vegetar", `soeg` et ord i navn,
    ingredienser eller note."""
    return _lokalt(samling.hent, tag, soeg)


@vaerktoej(LOKAL_SKRIVER)
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


@vaerktoej(LOKAL_SKRIVER)
async def fjern_opskrift(navn: str) -> Any:
    """Fjern en ret fra opskriftssamlingen. Spørg brugeren først."""
    if not _lokalt(samling.fjern, navn):
        raise ToolError(f"Der er ingen opskrift, der hedder {navn!r}.")
    return {"fjernet": navn}


@mcp.prompt(title="Ugeplan", description="Ugens madplan: menu, rettelser, pris og 'bestil' via Telegram")
def ugeplan(aftener: int = 7, butik: str = "begge") -> str:
    """Hele ugeplanen, startet manuelt (indtil en maskine kan starte den automatisk)."""
    return f"""Lav vores ugeplan for {aftener} aftener. Butik: {butik}. Følg trinene i rækkefølge.
Er Telegram sat op, foregår al snak med husstanden der (send_besked og vent_paa_svar).
Ellers foregår den her i chatten.

FORBEREDELSE (stille, uden at spørge nogen)
1. Læs vis_profil, koebsmoenster (andel_af_indkoeb fra profilens note), vis_beholdning og
   vis_opskrifter. Har du garmin-værktøjerne, så tag dagsstatus og træninger 14 dage tilbage.

TRIN 1: KØLESKABET
2. Spørg kort, hvad vi har i køleskabet, og om der er noget særligt denne uge (gæster,
   aftener ude, vildt fra Julius). Vent på svaret med vent_paa_svar: kald den igen, til der er
   svar, i op til 20 minutter. Gem svaret med opdater_beholdning.

TRIN 2: MENUEN (uden priser, men med et prisskøn)
3. Find {aftener} retter fra vores opskriftssider (Valdemarsro, Arla, DR Mad, Madens Verden,
   HelloFresh, Årstiderne). Søg på nettet og hent dem med hent_opskrift_fra_link, eller tag dem fra
   vis_opskrifter. Brug det, vi har hjemme, først. Ram profilen og det, vi plejer at købe.
4. Skøn prisen pr. ret ud fra hovedingredienserne og kendte priser (koebsmoenster, tidligere
   søgninger). Hold menuen under ugebudgettet (vis_profil), og læg dig gerne tæt på vores normale
   forbrug.
5. Send menuen kort: én linje pr. dag med ret, tid og skønnet pris, og til sidst det samlede skøn
   mod budgettet. Spørg: "Rettelser, eller er menuen god?" Vent på svaret.
6. Ret menuen, til den er god. Gør det en rettelse meget dyrere, så sig det og foreslå et
   alternativ. Først når menuen er godkendt, går du videre.

TRIN 3: PRISEN
7. Lav indkøbslisten ud fra opskrifternes ingredienser med mængder til vores antal personer,
   minus det vi har hjemme. Tag basisvarer med, der er snart_tom. Prissæt med sammenlign_liste
   (oekologisk efter profilen). Vær grundig: gå alternativerne igennem, søg igen på usikre linjer,
   og vælg ud fra pris, mængde og kvalitet.
8. Er totalen over ugebudgettet, så find konkrete billigere udskiftninger og nævn dem.
9. Vælg butik og gem med gem_madplan. Send: butik, total inkl. levering mod budget,
   antal varer, de 3-5 vigtigste valg kort, evt. budgetadvarsel, og "Skriv bestil, eller ret noget".

TRIN 4: BESTIL
10. Lytteren fylder kurven, når en af os svarer "bestil" (eller "ok"/"ja"), og sender besked.
    Vent med vent_paa_svar og fortæl, hvad der skete. Kommer der rettelser i stedet, så ret,
    gem en ny plan med gem_madplan (den gamle annulleres selv) og send den nye total.
11. Bestil og betal aldrig selv: vi vælger leveringstid og betaler i butikkens app."""


@vaerktoej(LOKAL_LAESER)
async def vent_paa_svar(sekunder: int = 50) -> Any:
    """Vent på svar fra husstanden på Telegram (højst 55 sekunder pr. kald). Giver de nye svar,
    eller {"ingen_svar_endnu": true}. Kald den igen, mens I venter. Svar fra "handle-botten"
    fortæller, at kurven er fyldt efter et "bestil" (eller hvorfor den ikke blev det)."""
    try:
        nye = await telegram.vent_paa_svar(sekunder)
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None
    return persondata.rens(nye) if nye else {"ingen_svar_endnu": True}


@vaerktoej(LAESER)
async def hent_opskrift_fra_link(link: str) -> Any:
    """Hent en opskrift fra et link på husstandens opskriftssider: Valdemarsro, Arla, DR Mad,
    Madens Verden, HelloFresh og Årstiderne. Giver navn, antal personer, tid og ingredienser med
    mængder. Find linket med websøgning på de sider. Gem retter, de kan lide, med gem_opskrift."""
    try:
        return data.til_json(await opskrifter_web.hent(link))
    except opskrifter_web.OpskriftFejl as fejl:
        raise ToolError(str(fejl)) from None


@vaerktoej(SKRIVER)
async def send_besked(tekst: str) -> Any:
    """Send en besked til husstandens telefoner via jeres Telegram-bot (kun tilkoblede telefoner).
    Brug den til ugeplanen: spørgsmålet om køleskabet, og planens sms_tekst til godkendelse.
    Send ikke andet, og ikke flere gange end nødvendigt. Højst 4000 tegn."""
    try:
        n = await telegram.send_til_alle(tekst)
    except telegram.TelegramFejl as fejl:
        raise ToolError(str(fejl)) from None
    return {"sendt_til": n}


@vaerktoej(LOKAL_LAESER)
async def vis_beskeder(kun_nye: bool = True) -> Any:
    """Svar fra husstanden via Telegram (fx hvad der er i køleskabet), nyeste sidst. Markerer dem
    som læst. "OK UGE-xxxx" står ikke her: dem klarer lytteren selv ved at fylde kurven."""
    try:
        return persondata.rens(telegram.beskeder(kun_nye))
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None


def _plan_svar(p: madplan.Madplan, **ekstra: Any) -> dict:
    d = data.til_json(p)
    return {**d, "varer_i_alt": p.varer_i_alt, "i_alt": p.i_alt, "sms_tekst": madplan.sms_tekst(p), **ekstra}


@vaerktoej(LOKAL_SKRIVER)
async def gem_madplan(
    butik: str, retter: list[str], linjer: list[madplan.NyLinje], gebyrer: float = 0.0, note: str | None = None
) -> Any:
    """Gem ugens madplan og den præcise indkøbsliste til godkendelse. Giver en kode (fx UGE-4821)
    og en kort `sms_tekst`, som brugeren kan sende til sin partner. Ændrer ikke kurven.
    `butik`: nemlig eller bilka (den I har valgt efter sammenligningen).
    `retter`: fx ["Man: Ovnbagt laks med kartofler", "Tir: ..."].
    `linjer`: de varer du har valgt: [{"vare_id": "5026540", "navn": "Hakket grisekød 8-12%",
    "antal": 2, "pris": 70.0}], hvor `antal` er antal pakker og `pris` linjens samlede pris.
    `gebyrer`: levering og pakning i kr. Vis planen og sms_tekst for brugeren bagefter."""
    try:
        p = madplan.gem(butik, retter, linjer, gebyrer, note)
        budget = profil.hent().ugebudget_kr
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None
    advarsel = f"Planen koster {p.i_alt:.0f} kr, over ugebudgettet på {budget:.0f} kr." if budget and p.i_alt > budget else None
    return _plan_svar(p, advarsel=advarsel)


@vaerktoej(LOKAL_LAESER)
async def vis_madplan(kode: str | None = None) -> Any:
    """En gemt madplan med varer, pris, status og sms_tekst. Uden kode: den nyeste."""
    try:
        return _plan_svar(madplan.hent(kode))
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None


@vaerktoej(SKRIVER)
async def fyld_kurv_fra_madplan(kode: str, godkendelse: str, kurven_maa_have_andet: bool = False) -> Any:
    """Læg PRÆCIS varerne fra en godkendt madplan i butikkens kurv. Ændrer kun kurven.
    Brug det KUN, når brugeren selv har godkendt: med koden ("OK UGE-4821") eller et kort
    "bestil"/"ok"/"ja", når planen er den eneste ventende. `godkendelse` er brugerens egne ord,
    ordret. Er det ikke en klar godkendelse ("ja, men uden laks"), sker der intet.
    En plan kan kun bruges én gang. Er kurven ikke tom, stopper den, medmindre brugeren har sagt,
    at det er i orden (`kurven_maa_have_andet=True`). Bestiller og betaler ALDRIG: brugeren
    vælger selv leveringstid og betaler i butikkens app. Vis bagefter kurven og evt. advarsler."""
    try:
        p = madplan.hent(kode)
    except lokal.LokalFejl as fejl:
        raise ToolError(str(fejl)) from None
    if not madplan.er_godkendt(p.kode, godkendelse):
        raise ToolError(f"Det er ikke en godkendelse af {p.kode}. Bed brugeren skrive \"OK {p.kode}\".")
    if p.status != "afventer":
        raise ToolError(f"Madplan {p.kode} er allerede brugt (status: {p.status}). Lav en ny plan.")
    butik = _butik(p.butik)
    try:
        foer = await butik.kurv()
        if foer.linjer and not kurven_maa_have_andet:
            andet = ", ".join(f"{ln.antal} x {ln.navn}" for ln in foer.linjer[:8])
            raise ToolError(
                f"Kurven hos {p.butik} er ikke tom ({andet}). Spørg brugeren, om de varer skal blive, "
                "og kald igen med kurven_maa_have_andet=True, eller bed dem tømme kurven."
            )
        resultater = await butik.saet_antal({ln.vare_id: ln.antal for ln in p.linjer})
        efter = await butik.kurv()
    except (butik_modul.ButikFejl, vagt.VagtFejl, login.IntetLogin) as fejl:
        raise ToolError(str(fejl)) from None
    plan_ids = {ln.vare_id for ln in p.linjer}
    i_kurven = sum((ln.beloeb for ln in efter.linjer if ln.id in plan_ids), start=Decimal(0))
    advarsler = [f"{r.id}: {r.besked or 'kunne ikke lægges i kurven'}" for r in resultater if not r.ok]
    if float(i_kurven) > p.varer_i_alt * (1 + float(madplan.PRISSTIGNING)):
        advarsler.append(
            f"Varerne koster nu {float(i_kurven):.2f} kr i kurven mod {p.varer_i_alt:.2f} kr i planen. Vis brugeren forskellen."
        )
    madplan.saet_status(p.kode, "kurv fyldt", f"kurv fyldt efter godkendelse: {godkendelse[:60]!r}")
    return persondata.rens(
        data.til_json(
            {
                "kode": p.kode,
                "butik": p.butik,
                "lagt_i_kurven": [r for r in resultater if r.ok],
                "advarsler": advarsler,
                "plan_varer_i_alt": p.varer_i_alt,
                "kurv_varer_for_planen": i_kurven,
                "kurv": efter,
                "naeste_skridt": "Vælg leveringstid og betal selv i butikkens app eller på hjemmesiden.",
            }
        )
    )


@vaerktoej(LAESER)
async def leveringstider(butik: str = "nemlig", dage: int = 5) -> Any:
    """Ledige leveringstider med pris. Kun til orientering: brugeren vælger selv tiden."""
    return await _koer(_butik(butik).leveringstider(min(dage, 14)))


@vaerktoej(LAESER)
async def tidligere_ordrer(butik: str = "nemlig", antal: int = 10, ordre_id: str | None = None) -> Any:
    """Tidligere ordrer. Med ordre_id: varerne i den ordre (godt til 'det vi plejer at købe')."""
    b = _butik(butik)
    return await _koer(b.ordre(ordre_id) if ordre_id else b.ordrer(min(antal, 30)))


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logger ellers hele URL'er
    genindlaes.start()
    if len(sys.argv) > 1:
        from handle_mcp.cli import kommando

        sys.exit(kommando(sys.argv[1:]))
    mcp.run("stdio")


if __name__ == "__main__":
    main()
