"""Madplanens forløb: køleskab → menu → pris → "bestil".

Fundet ved første rigtige brug: Når forløbet bare sagde "brug Telegram, hvis det er sat op",
stillede Claude spørgsmålene i chatten i stedet. Derfor sender `start()` selv det første
spørgsmål til Telegram, og forløbet siger klart, at al snak med husstanden foregår der.

Teksten ligger her (ikke i server.py), så den kan rettes uden at genstarte Claude Desktop.
"""

from __future__ import annotations

from handle_mcp import telegram

FOERSTE_SPOERGSMAAL = (
    "Hej! Ny madplan på vej.\n"
    "1) Hvad har I i køleskab, fryser og skab, der skal bruges?\n"
    "2) Hvor mange aftener skal vi have mad til, og fra hvilken dag?\n"
    "3) Noget særligt denne uge? (gæster, spise ude, vildt)\n"
    "Svar bare her, gerne med tale."
)

FAELLES = """
TRIN 2: MENUEN (uden priser, men med et prisskøn)
3. Find retterne på vores opskriftssider (Valdemarsro, Arla, DR Mad, Madens Verden, HelloFresh,
   Årstiderne). Søg på nettet og hent dem med hent_opskrift_fra_link, eller tag dem fra
   vis_opskrifter. Brug det, vi har hjemme, først. Ram profilen og det, vi plejer at købe.
4. Skøn prisen pr. ret ud fra hovedingredienserne og kendte priser (koebsmoenster, tidligere
   søgninger). Hold menuen under ugebudgettet (vis_profil), gerne tæt på vores normale forbrug.
5. Send menuen kort: én linje pr. dag med ret, tid og skønnet pris, og til sidst det samlede skøn
   mod budgettet. Spørg: "Rettelser, eller er menuen god?" Vent på svaret.
6. Ret menuen, til den er god. Gør det en rettelse meget dyrere, så sig det og foreslå et
   alternativ. Først når menuen er godkendt, går du videre.

TRIN 3: PRISEN
7. Lav indkøbslisten ud fra opskrifternes ingredienser med mængder til vores antal personer,
   minus det vi har hjemme. Tag basisvarer med, der er snart_tom. Prissæt i begge butikker med
   sammenlign_liste (oekologisk efter profilen). Vær grundig: gå alternativerne igennem, søg igen
   på usikre linjer, og vælg ud fra pris, mængde og kvalitet.
8. Følg `anbefaling` fra sammenlign_liste: én butik, eller begge, når det samlet er billigst med
   levering, pakning og minimumskøb. Del aldrig for en lille besparelse, som gebyrerne æder.
   Er totalen over ugebudgettet, så find konkrete billigere udskiftninger og nævn dem.
9. Gem med gem_madplan. Ved deling: butik="begge" og "butik" på hver vare. Send: butik(ker),
   total inkl. levering mod budget, antal varer, de 3-5 vigtigste valg kort, evt. budgetadvarsel,
   og "Skriv bestil, eller ret noget".

TRIN 4: BESTIL
10. "bestil" (eller "ok"/"ja") fylder kurven/kurvene og giver besked. Vent på svaret og fortæl,
    hvad der skete. Kommer der rettelser i stedet, så ret, gem en ny plan med gem_madplan (den
    gamle annulleres selv) og send den nye total.
11. Bestil og betal aldrig selv: vi vælger leveringstid og betaler i butikkens app."""

FORBEREDELSE = """FORBEREDELSE (stille, uden at spørge nogen)
1. Læs vis_profil, koebsmoenster (andel_af_indkoeb fra profilens note), vis_beholdning og
   vis_opskrifter. Har du garmin-værktøjerne, så tag dagsstatus og træninger 14 dage tilbage."""


def tekst_telegram(sendt_til: int) -> str:
    return f"""Lav vores madplan. Følg trinene i rækkefølge.

VIGTIGT: Al snak med husstanden foregår på Telegram, så de kan svare fra telefonen.
- Spørgsmålet om køleskabet og antal aftener ER ALLEREDE SENDT til {sendt_til} telefon(er).
  Stil det IKKE igen, og stil ingen spørgsmål til husstanden her i chatten.
- Brug send_besked til alt, du vil spørge eller vise dem (menu, total, "skriv bestil").
- Brug vent_paa_svar til at vente på deres svar. Får du "ingen_svar_endnu", så kald den igen,
  i op til 20 minutter pr. spørgsmål.
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


async def start() -> dict:
    """Start madplanen: send det første spørgsmål på Telegram, hvis det er sat op."""
    if telegram_klar():
        try:
            n = await telegram.send_til_alle(FOERSTE_SPOERGSMAAL)
        except telegram.TelegramFejl as fejl:
            return {"telegram": f"kunne ikke sende ({fejl})", "forloeb": tekst_chat()}
        return {"telegram": f"spørgsmålet er sendt til {n} telefon(er)", "forloeb": tekst_telegram(n)}
    return {"telegram": "ikke sat op", "forloeb": tekst_chat()}
