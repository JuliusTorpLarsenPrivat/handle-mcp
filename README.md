# handle-mcp

En MCP-server, der giver Claude adgang til **nemlig.com** og **BilkaToGo**: søg varer, se ugens tilbud og nemligs opskrifter, sammenlign en indkøbsliste i begge butikker og fyld kurven i den billigste.

**Den kan aldrig bestille eller betale.** Den kode findes ikke, en "vagt" blokerer kaldene, og testene fejler, hvis nogen prøver at tilføje dem. Du åbner selv butikkens hjemmeside, tjekker kurven, vælger leveringstid og betaler.

Uofficiel. Bruger butikkernes egne hjemmeside-API'er, så det kan holde op med at virke uden varsel.

## Installér (Windows)

Kræver [uv](https://docs.astral.sh/uv/) og [Git](https://git-scm.com/download/win).

```powershell
# 1. Gem login (spørger om email og kode, tjekker dem og gemmer i Windows Credential Manager)
uvx --from git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.14.0 handle-mcp login nemlig
uvx --from git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.14.0 handle-mcp login bilka
```

2. Claude Desktop → Settings → Developer → Edit Config:

```json
{
  "mcpServers": {
    "handle": {
      "command": "C:\\Users\\JuliusTorpLarsen\\.local\\bin\\uvx.exe",
      "args": ["--from", "git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.14.0", "handle-mcp"]
    }
  }
}
```

3. Genstart Claude Desktop helt. BilkaToGo: vælg hjemlevering én gang på bilkatogo.dk.

## Værktøjer

| Værktøj | Ændrer noget? | Hvad |
|---|---|---|
| `soeg_varer` | nej | Pris, enhedspris, tilbud, lager. `billigst_foerst=True` gennemsøger 50 hits og ugens tilbud og sorterer efter enhedspris |
| `ugens_tilbud` | nej | Ugens tilbud i én butik |
| `soeg_opskrifter` / `hent_opskrift` | nej | nemligs opskrifter og varerne til dem |
| `sammenlign_liste` | nej | Prissætter en liste i begge butikker inkl. gebyrer og giver en `anbefaling`: den samlet billigste butik, eller begge, når det kan betale sig efter levering, pakning og minimumskøb. Skriv mængden med ("500 g hakket oksekød"), så vælges de pakker, der dækker behovet billigst. `oekologisk=True` foretrækker øko. Rå varer frem for forarbejdede, og 4 alternativer pr. vare |
| `vis_kurv` | nej | Kurv, gebyrer, minimumskøb |
| `saet_antal_i_kurv` | **ja, kun kurven** | Sætter antal af varer i kurven |
| `leveringstider` | nej | Ledige tider og pris |
| `tidligere_ordrer` | nej | Ordrehistorik |
| `vis_beholdning` | nej | Hvad I har hjemme (køleskab, fryser, skab) |
| `opdater_beholdning` | **ja, kun den lokale liste** | Gem hvad I har hjemme, ret eller fjern varer |
| `vis_opskrifter` | nej | Jeres egen samling af retter, I stoler på (søg på tag eller ord) |
| `gem_opskrift` / `fjern_opskrift` | **ja, kun den lokale samling** | Gem eller fjern en ret |
| `gem_madplan` / `vis_madplan` | **ja, kun den lokale plan** | Gemmer ugens madplan og præcise indkøbsliste med en kode (fx UGE-4821) og en SMS-tekst til godkendelse |
| `fyld_kurv_fra_madplan` | **ja, kun kurven** | Lægger præcis den godkendte plans varer i kurven, når I har skrevet "OK UGE-4821". Én gang pr. plan |
| `ugeplan_forloeb` | nej | Ugeplanens faste forløb, som Claude følger, når nogen beder om en ugeplan |
| `hent_opskrift_fra_link` | nej | Opskrift (ingredienser, personer, tid) fra Valdemarsro, Arla, DR Mad, Madens Verden, HelloFresh eller Årstiderne |
| `vent_paa_svar` | nej | Venter på husstandens svar på Telegram |
| `send_besked` | **ja, sender til jeres telefoner** | Sender ugeplanens spørgsmål og plan til jeres Telegram-bot |
| `vis_beskeder` | nej | Jeres svar fra Telegram |
| `koebsmoenster` | nej | Basisvarer (hvor tit og hvornår sidst), favoritter, øko-andel og forbrug pr. uge ud fra gemte kvitteringer |
| `vis_profil` | nej | Antal personer, ugebudget, præferencer og ting at undgå |
| `opdater_profil` | **ja, kun den lokale profil** | Ret profilen |

Beholdning, opskrifter, profil og kvitteringer ligger som JSON-filer i `%LOCALAPPDATA%\handle-mcp\` på din egen computer og sendes aldrig til butikkerne.

## Sikkerhed

- Login ligger kun i styresystemets nøglering på din computer (eller i miljøvariabler).
- Alle kald går gennem `vagt.py`, som kun tillader butikkernes egne adresser og afviser alt, der ligner bestilling, betaling eller valg af levering.
- Navn, adresse, telefon og e-mail fjernes fra svarene, før Claude ser dem.
- Lås altid installationen til en version (fx `@v0.14.0`), så koden ikke ændrer sig uden din viden.

## Ugeplan

Skriv bare **"lav ugeplan"** i Claude Desktop (eller vælg `+` → Connectors → Add from handle → Ugeplan). Husstanden bliver spurgt om køleskab og antal aftener på Telegram. Forløbet: køleskabet → menu med prisskøn, som I retter → præcis pris i den billigste butik → "bestil" fylder kurven. En ny version af planen annullerer den forrige. Når I svarer "bestil" (eller "ok"/"ja"), lægges præcis de godkendte varer i kurven, i én eller begge butikker. I vælger selv leveringstid og betaler i butikkens app.

## Telegram (svar fra telefonen, gratis)

1. Opret en bot: skriv `/newbot` til @BotFather i Telegram.
2. Kopiér botnøglen og kør `handle-mcp telegram-opsaet udklipsholder` (eller `handle-mcp telegram-opsaet` og indsæt den). Den gemmes i nøgleringen, og du får en parringskode.
3. Send koden til botten fra begge telefoner. Lytteren starter selv i baggrunden, når Claude sender den første besked, og stopper, når ugens plan er afsluttet (højst 24 timer). Den kan også startes manuelt med `handle-mcp telegram`.

Nu sender ugeplanen sit spørgsmål og sin plan til jer, jeres svar læses af Claude, og "OK UGE-xxxx" fra en af jer fylder kurven med det samme. Kun tilkoblede telefoner kan skrive med botten. Tale virker med telefonens diktering eller Siri. Botten bestiller og betaler aldrig.

## Kvitteringer fra Coop

`koebsmoenster` læser kvitteringer fra medlem.coop.dk, gemt lokalt. Sådan hentes nye (kan køres igen og igen, dubletter springes over):

1. Den, der ejer Coop-kontoen, siger ja og logger selv ind på medlem.coop.dk.
2. `uv run python scripts/coop_hoest.py` starter en modtager på 127.0.0.1 og skriver en nøgle.
3. Kør `scripts/coop_hoest.js` i fanen https://medlem.coop.dk/din-profil/konto-kvitteringer/kvitteringer/ med nøglen indsat, og klik på den grønne knap.

Kun dato, butik, total og varer gemmes. Kvitteringer med kort- eller medlemsnummer afvises.

## Over netværket (HTTP)

Til en maskine, der altid er tændt. Hver forespørgsel kræver en adgangsnøgle (`Authorization: Bearer <nøgle>`).

```powershell
handle-mcp http-noegle               # laver nøglen og viser den én gang
handle-mcp http                      # kun denne maskine: http://127.0.0.1:8765/mcp
handle-mcp http 100.x.y.z 8765       # fx maskinens Tailscale-adresse
```

Stil den aldrig åbent på internettet. Brug et privat netværk som [Tailscale](https://tailscale.com).

## Udvikling

Ændringer i logikken (varevalg, mængder, tilbud, kvitteringer …) bruges med det samme: før hvert værktøjskald tjekker serveren, om kodefilerne er ændret, og indlæser dem igen (`genindlaes.py`). Nye værktøjer, nye felter og ændringer i `server.py` kræver stadig, at Claude Desktop genstartes.


```sh
uv sync
uv run pytest
```

Se [LÆR.md](LÆR.md) for en gennemgang af, hvordan det hele hænger sammen.

[MIT](LICENSE)
