"""Madplanens forløb: køleskab → menu → pris → "bestil".

Fundet ved første rigtige brug: Når forløbet bare sagde "brug Telegram, hvis det er sat op",
stillede Claude spørgsmålene i chatten i stedet. Derfor sender `start()` selv det første
spørgsmål til Telegram, og forløbet siger klart, at al snak med husstanden foregår der.

Teksten ligger her (ikke i server.py), så den kan rettes uden at genstarte Claude Desktop.
"""

from __future__ import annotations

from handle_mcp import persondata, telegram
from handle_mcp.lokal import LokalFejl

FOERSTE_SPOERGSMAAL = (
    "Hej! Ny madplan på vej.\n"
    "1) Hvad har I i køleskab, fryser og skab, der skal bruges?\n"
    "2) Hvor mange aftener skal vi have mad til, og fra hvilken dag?\n"
    "3) Noget særligt denne uge? (gæster, spise ude, vildt)\n"
    "Svar bare her. Tal gerne ind med mikrofonen på tastaturet eller via Siri (ikke som talebesked)."
)

FAELLES = """
UGENS BUDGET (regn det ud, før du laver menuen)
- Profilens ugebudget_kr er for en hel uge (7 dage) til profilens antal personer.
- Færre dage: skalér ned (6 af 7 dage → budget × 6/7). Gæster: skalér op efter antal ekstra
  måltider (2 gæster til 1 aftensmad hos 2 personer ≈ + 2 aftensmåltider). Skriv kort, hvordan
  du regnede, og send ugens budget til gem_madplan som valg={"ugebudget_kr": …}.
- Sig altid tydeligt, hvad du sammenligner med: "ugens budget" (ovenstående) eller "normalt
  forbrug" (det vi plejer at bruge ifølge koebsmoenster). De to tal er ikke det samme.

SVARENE ER OFTE INDTALT
- Diktering giver fejl og ingen tegnsætning (fx "en halv tær" kan være en halv tærte eller en
  halv tærtedej). Gæt det mest sandsynlige ud fra sammenhængen, og skriv gættet i menubeskeden
  i stedet for at stille et ekstra spørgsmål.
- Mangler startdagen, så regn med dagen efter den første mulige levering, og skriv det i
  menubeskeden. Spørg kun, hvis svaret er helt uklart.

TRIN 2: MENUEN (uden priser, men med et prisskøn)
3. Find retterne på vores opskriftssider (Valdemarsro, Arla, DR Mad, Madens Verden, HelloFresh,
   Årstiderne). Søg på nettet og hent dem med hent_opskrift_fra_link, eller tag dem fra
   vis_opskrifter. Brug det, vi har hjemme, først. Ram profilen og det, vi plejer at købe.
   De faste måltider i profilen (fx morgenmad og weekendfrokost) hører med hver uge.
4. Skøn prisen pr. ret ud fra hovedingredienserne og kendte priser (koebsmoenster, tidligere
   søgninger), og skøn de faste måltider for sig. Læg levering og poser til (nemlig ca. 36 kr
   levering + ca. 18 kr poser). Hold det samlede skøn under ugens budget.
5. Send menuen kort: én linje pr. dag med ret, tid og skønnet pris, én linje med de faste
   måltider og deres skøn, og det samlede skøn mod ugens budget. Skriv så, hvad du regner med,
   vi HAR hjemme og derfor ikke køber (fx æg, citron, dijon, olie, krydderier), så vi kan rette
   det nu og ikke efter prissætningen. Spørg: "Rettelser, eller er menuen god?" Vent på svaret.
6. Ret menuen, til den er god. Gør det en rettelse meget dyrere, så sig det og foreslå et
   alternativ. Følg ØNSKER TIL FREMOVER nedenfor. Først når menuen er godkendt, går du videre.

TRIN 3: PRISEN
7. Lav indkøbslisten ud fra opskrifternes ingredienser og de faste måltider, med mængder til
   vores antal personer og dage, minus det vi har hjemme (og det vi bekræftede i trin 2). Tag
   basisvarer med, der er snart_tom. Prissæt i begge butikker med sammenlign_liste (oekologisk
   efter profilen). Overhold profilens vareønsker (fx kakaoprocent). Vær grundig: gå
   alternativerne igennem, søg igen på usikre linjer, og vælg ud fra pris, mængde og kvalitet.
8. Følg `anbefaling` fra sammenlign_liste: én butik, eller begge, når det samlet er billigst med
   levering, poser, pakning og minimumskøb. Del aldrig for en lille besparelse, som gebyrerne
   æder. Er totalen over ugens budget, så find konkrete billigere udskiftninger og nævn dem.
9. Gem med gem_madplan (gebyrer = alle gebyrer inkl. poser, valg={"ugebudget_kr": ugens budget}). Ved
   deling: butik="begge" og "butik" på hver vare. Send: butik(ker), total inkl. levering og
   poser mod ugens budget, antal varer, de 3-5 vigtigste valg kort, evt. budgetadvarsel, og
   "Skriv bestil, eller ret noget".

TRIN 4: BESTIL
10. "bestil" (eller "ok"/"ja") fylder kurven/kurvene og giver besked. Vent på svaret og fortæl,
    hvad der skete. Kommer der rettelser i stedet, så ret, gem en ny plan med gem_madplan (den
    gamle annulleres selv) og send den nye total.
11. Når kurven er fyldt: slå leveringstider op og send ét forslag til levering, dagen før
    planens første dag (fx "Lever lørdag 15/11 mellem 17 og 20, maden starter søndag"). Skriv,
    at de selv vælger tiden og betaler i butikkens app.
12. Retter de i kurven bagefter, så ret med saet_antal_i_kurv og tjek med vis_kurv, at
    kurven er præcis, som den skal være (også at fjernede varer er væk), før du skriver, at
    det er gjort. Bed aldrig husstanden rette kurven selv. Kan noget ikke lade sig gøre, så
    sig præcis hvad og hvorfor.
13. Bestil og betal aldrig selv: vi vælger leveringstid og betaler i butikkens app.

NÅR EN RET IKKE KAN KØBES ORDENTLIGT (også efter at menuen er godkendt)
Fundet i en test: alle laks i Bilka var enten opdrættede, frosne eller kunne ikke leveres.
- Kan en rets hovedingrediens ikke købes, så den passer til profilen (ikke på lager, kan ikke
  leveres, frost/opdræt når profilen siger nej, langt over prisskønnet), så må du skifte
  RETTEN ud. Du skal ikke bare tage den næstbedste vare, der bryder profilen, og ikke lade
  dagen stå tom: der skal stadig være mad til alle de aftener, vi bad om.
- Prøv i denne rækkefølge, og tag den første, der virker:
  1. Samme ret med en anden god udgave af varen (anden størrelse eller mærke, eller den anden
     butik, hvis det samlet stadig er billigst).
  2. En ret med samme tilbehør og en anden protein, der passer til profilen og dagens tid
     (fx laks med kartofler og broccoli → svinemørbrad eller kylling med kartofler og
     broccoli). Undgå en protein, der allerede er på menuen samme uge.
  3. En helt anden ret fra vores opskriftssider til samme dag og tid.
  Hold prisen på niveau med den gamle ret, og fjern de varer, der kun var til den gamle ret.
- Sker det FØR "bestil" (under prissætningen): skift retten, prissæt den, og skriv øverst i
  prisbeskeden "Ændret: Tor laks → svinemørbrad med kartofler og broccoli (ingen vildfanget
  fersk laks i Bilka)". "bestil" godkender så også ændringen. Ingen ekstra spørgsmål.
- Sker det EFTER "bestil" (botten melder, at en vare ikke kom i kurven): find erstatningen
  efter samme rækkefølge, læg den i kurven med saet_antal_i_kurv, fjern varer der kun var til
  den gamle ret, tjek kurven med vis_kurv, og send én kort besked: hvad der er skiftet,
  hvorfor, og den nye total. Spørg kun først, hvis den nye total bliver over ugens budget,
  eller hvis husstanden selv har bedt om netop den ret.

ØNSKER TIL FREMOVER (gælder hele forløbet)
- Siger husstanden på nogen måde, at noget skal huskes til fremover ("husk", "fra nu af",
  "altid", "aldrig", "vi kan ikke lide …", "næste gang …", "gem det"), så gem det i profilen
  med opdater_profil i det rette felt: vareønsker (en bestemt vare), ting at undgå,
  præferencer (madstil) eller faste måltider. Gør det med det samme, uden at spørge.
- Beskeder med "gemt_i_profilen" har botten allerede lagt i profilens fra_husstanden. Flyt
  dem til det rette felt og ryd fra_husstanden i samme opdater_profil-kald. Handler et af dem
  kun om denne uge (fx "husk gæster lørdag"), så brug det i planen og gem det ikke.
- Nævn ikke for husstanden, hvad du har gemt, medmindre de spørger.
- Spørg kun, om noget skal gemmes, når en rettelse handler om smag eller en vare generelt og
  ikke står i profilen i forvejen, fx "fennikel er ikke lige os" eller "kødet var for fedt
  sidst". Spørg IKKE ved rettelser om denne uge: dage, gæster, tid, lyst, rækkefølge eller
  pris ("ikke fisk tirsdag", "byt torsdag og fredag", "noget hurtigere onsdag").
  Højst ét sådant spørgsmål pr. madplan, som én kort linje sidst i din næste besked, fx
  "Skal jeg huske, at I ikke vil have fennikel?". Ja → gem. Intet svar eller nej → gem ikke."""

FORBEREDELSE = """FORBEREDELSE (stille, uden at spørge nogen)
1. Læs vis_profil (også faste måltider, vareønsker og fra_husstanden), koebsmoenster
   (andel_af_indkoeb fra profilens note), vis_beholdning og vis_opskrifter. Har du
   garmin-værktøjerne, så tag dagsstatus og træninger 14 dage tilbage.
   Gå `beskeder_siden_sidst` (beskeder sendt mellem madplanerne) og profilens fra_husstanden
   igennem efter ØNSKER TIL FREMOVER, og ryd op i profilen, før du laver menuen."""


def tekst_telegram(sendt_til: int) -> str:
    return f"""Lav vores madplan. Følg trinene i rækkefølge.

VIGTIGT: Al snak med husstanden foregår på Telegram, så de kan svare fra telefonen.
- Spørgsmålet om køleskabet og antal aftener ER ALLEREDE SENDT til {sendt_til} telefon(er).
  Stil det IKKE igen, og stil ingen spørgsmål til husstanden her i chatten.
- Brug send_besked til alt, du vil spørge eller vise dem (menu, total, "skriv bestil").
- Brug vent_paa_svar til at vente på deres svar. Får du "ingen_svar_endnu", så kald den igen,
  i op til 20 minutter pr. spørgsmål.
- AFSLUT ALDRIG midt i samtalen. Efter HVER besked til husstanden (også den sidste med
  leveringsforslaget) kalder du vent_paa_svar igen og bliver ved, til der har været 20
  minutter uden svar. Først da skriver du opsummeringen her i chatten og stopper. Ellers
  bliver deres næste svar ikke læst (fundet i demoen: "Gem i profilen" blev aldrig læst).
- Her i chatten skriver du kun korte statuslinjer til Julius, fx "Venter på svar om køleskabet".

{FORBEREDELSE}

TRIN 1: KØLESKABET (spørgsmålet er sendt)
2. Vent på svaret med vent_paa_svar. Gem det, de har, med opdater_beholdning, og brug det antal
   aftener, de svarer.
{FAELLES}"""


def tekst_chat(aftener: str = "") -> str:
    aftener = str(aftener or "").strip()
    antal = f"{aftener} aftener" if aftener.isdigit() else "det antal aftener, husstanden svarer"
    spoerg_antal = "" if aftener.isdigit() else " Spørg samtidig, hvor mange aftener vi skal have mad til."
    return f"""Lav vores madplan for {antal}. Følg trinene i rækkefølge. Telegram er ikke sat op, så
snakken med husstanden foregår her i chatten.

{FORBEREDELSE}

TRIN 1: KØLESKABET
2. Spørg kort, hvad vi har i køleskabet, og om der er noget særligt denne uge (gæster,
   aftener ude, vildt fra Julius).{spoerg_antal} Gem svaret med opdater_beholdning.
{FAELLES}"""


def telegram_klar() -> bool:
    try:
        telegram.hent_token()
    except telegram.TelegramFejl:
        return False
    return bool(telegram.chats())


def _beskeder_siden_sidst() -> list[dict]:
    """Ulæste beskeder fra før denne madplan. Hentes FØR spørgsmålet sendes, så de ikke blandes
    sammen med svaret om køleskabet (og ikke bliver overset)."""
    try:
        return persondata.rens(telegram.beskeder(kun_nye=True))
    except LokalFejl:
        return []


async def start() -> dict:
    """Start madplanen: send det første spørgsmål på Telegram, hvis det er sat op."""
    if telegram_klar():
        tidligere = _beskeder_siden_sidst()
        try:
            n = await telegram.send_til_alle(FOERSTE_SPOERGSMAAL)
        except telegram.TelegramFejl as fejl:
            return {"telegram": f"kunne ikke sende ({fejl})", "beskeder_siden_sidst": tidligere, "forloeb": tekst_chat()}
        return {"telegram": f"spørgsmålet er sendt til {n} telefon(er)", "beskeder_siden_sidst": tidligere, "forloeb": tekst_telegram(n)}
    return {"telegram": "ikke sat op", "forloeb": tekst_chat()}
