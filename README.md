# handle-mcp

En MCP-server, der giver Claude adgang til **nemlig.com** og **BilkaToGo**: søg varer, se ugens tilbud og nemligs opskrifter, sammenlign en indkøbsliste i begge butikker og fyld kurven i den billigste.

**Den kan aldrig bestille eller betale.** Den kode findes ikke, en "vagt" blokerer kaldene, og testene fejler, hvis nogen prøver at tilføje dem. Du åbner selv butikkens hjemmeside, tjekker kurven, vælger leveringstid og betaler.

Uofficiel. Bruger butikkernes egne hjemmeside-API'er, så det kan holde op med at virke uden varsel.

## Installér (Windows)

Kræver [uv](https://docs.astral.sh/uv/) og [Git](https://git-scm.com/download/win).

```powershell
# 1. Gem login (spørger om email og kode, tjekker dem og gemmer i Windows Credential Manager)
uvx --from git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.8.0 handle-mcp login nemlig
uvx --from git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.8.0 handle-mcp login bilka
```

2. Claude Desktop → Settings → Developer → Edit Config:

```json
{
  "mcpServers": {
    "handle": {
      "command": "C:\\Users\\JuliusTorpLarsen\\.local\\bin\\uvx.exe",
      "args": ["--from", "git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.8.0", "handle-mcp"]
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
| `sammenlign_liste` | nej | Prissætter en liste i begge butikker inkl. gebyrer. Skriv mængden med ("500 g hakket oksekød"), så vælges de pakker, der dækker behovet billigst. `oekologisk=True` foretrækker øko. Rå varer frem for forarbejdede, og 4 alternativer pr. vare |
| `vis_kurv` | nej | Kurv, gebyrer, minimumskøb |
| `saet_antal_i_kurv` | **ja, kun kurven** | Sætter antal af varer i kurven |
| `leveringstider` | nej | Ledige tider og pris |
| `tidligere_ordrer` | nej | Ordrehistorik |
| `vis_beholdning` | nej | Hvad I har hjemme (køleskab, fryser, skab) |
| `opdater_beholdning` | **ja, kun den lokale liste** | Gem hvad I har hjemme, ret eller fjern varer |
| `vis_opskrifter` | nej | Jeres egen samling af retter, I stoler på (søg på tag eller ord) |
| `gem_opskrift` / `fjern_opskrift` | **ja, kun den lokale samling** | Gem eller fjern en ret |
| `koebsmoenster` | nej | Basisvarer (hvor tit og hvornår sidst), favoritter, øko-andel og forbrug pr. uge ud fra gemte kvitteringer |
| `vis_profil` | nej | Antal personer, ugebudget, præferencer og ting at undgå |
| `opdater_profil` | **ja, kun den lokale profil** | Ret profilen |

Beholdning, opskrifter, profil og kvitteringer ligger som JSON-filer i `%LOCALAPPDATA%\handle-mcp\` på din egen computer og sendes aldrig til butikkerne.

## Sikkerhed

- Login ligger kun i styresystemets nøglering på din computer (eller i miljøvariabler).
- Alle kald går gennem `vagt.py`, som kun tillader butikkernes egne adresser og afviser alt, der ligner bestilling, betaling eller valg af levering.
- Navn, adresse, telefon og e-mail fjernes fra svarene, før Claude ser dem.
- Lås altid installationen til en version (fx `@v0.8.0`), så koden ikke ændrer sig uden din viden.

## Over netværket (HTTP)

Til en maskine, der altid er tændt. Hver forespørgsel kræver en adgangsnøgle (`Authorization: Bearer <nøgle>`).

```powershell
handle-mcp http-noegle               # laver nøglen og viser den én gang
handle-mcp http                      # kun denne maskine: http://127.0.0.1:8765/mcp
handle-mcp http 100.x.y.z 8765       # fx maskinens Tailscale-adresse
```

Stil den aldrig åbent på internettet. Brug et privat netværk som [Tailscale](https://tailscale.com).

## Udvikling

```sh
uv sync
uv run pytest
```

Se [LÆR.md](LÆR.md) for en gennemgang af, hvordan det hele hænger sammen.

[MIT](LICENSE)
