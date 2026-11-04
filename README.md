# handle-mcp

En MCP-server, der giver Claude adgang til **nemlig.com** og **BilkaToGo**: søg varer, se ugens tilbud og nemligs opskrifter, sammenlign en indkøbsliste i begge butikker og fyld kurven i den billigste.

**Den kan aldrig bestille eller betale.** Den kode findes ikke, en "vagt" blokerer kaldene, og testene fejler, hvis nogen prøver at tilføje dem. Du åbner selv butikkens hjemmeside, tjekker kurven, vælger leveringstid og betaler.

Uofficiel. Bruger butikkernes egne hjemmeside-API'er, så det kan holde op med at virke uden varsel.

## Installér (Windows)

Kræver [uv](https://docs.astral.sh/uv/) og [Git](https://git-scm.com/download/win).

```powershell
# 1. Gem login (spørger om email og kode, tjekker dem og gemmer i Windows Credential Manager)
uvx --from git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.1.0 handle-mcp login nemlig
uvx --from git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.1.0 handle-mcp login bilka
```

2. Claude Desktop → Settings → Developer → Edit Config:

```json
{
  "mcpServers": {
    "handle": {
      "command": "C:\\Users\\JuliusTorpLarsen\\.local\\bin\\uvx.exe",
      "args": ["--from", "git+https://github.com/JuliusTorpLarsenPrivat/handle-mcp@v0.1.0", "handle-mcp"]
    }
  }
}
```

3. Genstart Claude Desktop helt. BilkaToGo: vælg hjemlevering én gang på bilkatogo.dk.

## Værktøjer

| Værktøj | Ændrer noget? | Hvad |
|---|---|---|
| `soeg_varer` | nej | Pris, enhedspris, tilbud, lager |
| `ugens_tilbud` | nej | Ugens tilbud i én butik |
| `soeg_opskrifter` / `hent_opskrift` | nej | nemligs opskrifter og varerne til dem |
| `sammenlign_liste` | nej | Prissætter en liste i begge butikker inkl. gebyrer |
| `vis_kurv` | nej | Kurv, gebyrer, minimumskøb |
| `saet_antal_i_kurv` | **ja, kun kurven** | Sætter antal af varer i kurven |
| `leveringstider` | nej | Ledige tider og pris |
| `tidligere_ordrer` | nej | Ordrehistorik |

## Sikkerhed

- Login ligger kun i styresystemets nøglering på din computer (eller i miljøvariabler).
- Alle kald går gennem `vagt.py`, som kun tillader butikkernes egne adresser og afviser alt, der ligner bestilling, betaling eller valg af levering.
- Navn, adresse, telefon og e-mail fjernes fra svarene, før Claude ser dem.
- Lås altid installationen til en version (`@v0.1.0`), så koden ikke ændrer sig uden din viden.

## Udvikling

```sh
uv sync
uv run pytest
```

Se [LÆR.md](LÆR.md) for en gennemgang af, hvordan det hele hænger sammen.

[MIT](LICENSE)
