# Sådan hænger handle-mcp sammen

En gennemgang til dig, der vil forstå MCP, Python-projektet og GitHub, ikke kun bruge det.

## 1. De fire dele

```
 Dig ──► Claude Desktop ──(MCP over stdio)──► handle-mcp på din PC ──(HTTPS)──► nemlig / Bilka
                                                   │
                                     Windows Credential Manager (dit login)
```

- **Claude** beslutter, *hvilket værktøj* der skal bruges, fx `soeg_varer("letmælk")`. Claude ser aldrig dit login.
- **MCP (Model Context Protocol)** er en fælles "stikkontakt". Serveren fortæller, hvilke værktøjer den har (navn, beskrivelse, felter), og Claude kalder dem. Beskederne er JSON og sendes over programmets stdin/stdout ("stdio").
- **handle-mcp** er et almindeligt Python-program. Claude Desktop starter det i baggrunden, når appen åbner (det er det, `claude_desktop_config.json` beskriver).
- **GitHub** er kun der, hvor koden opbevares. `uvx` henter koden derfra første gang og kører den lokalt.

## 2. Login, trin for trin

1. `handle-mcp login nemlig` spørger om email og kode, gemmer dem i Windows' nøglering og prøver at logge ind. Virker det ikke, slettes de igen.
2. Når Claude kalder et værktøj, henter programmet selv login fra nøgleringen (`login.py`).
3. nemlig: hent token → hent XSRF-token → send login → få en cookie (`butikker/nemlig.py`, øverst).
   Bilka: Gigya-login → JWT → Bilka-session (`butikker/bilka.py`, øverst).
4. De næste kald bruger cookie/token. Udløber de, logger programmet ind igen af sig selv.

## 3. Filerne

| Fil | Hvad den gør |
|---|---|
| `src/handle_mcp/server.py` | Værktøjerne. Hver `@mcp.tool` er en "knap" for Claude. Start her. |
| `src/handle_mcp/vagt.py` | Al netværkstrafik går herigennem. Kun kendte adresser, ingen betaling. |
| `src/handle_mcp/login.py` | Henter/gemmer login i nøgleringen. |
| `src/handle_mcp/butikker/nemlig.py` | Taler med nemlig og oversætter svarene. |
| `src/handle_mcp/butikker/bilka.py` | Det samme for BilkaToGo. |
| `src/handle_mcp/data.py` | De fælles typer (Vare, Kurv, ...), som begge butikker oversættes til. |
| `src/handle_mcp/sammenlign.py` | Prissætter en indkøbsliste i begge butikker. |
| `src/handle_mcp/persondata.py` | Fjerner navn, adresse osv., før Claude ser svaret. |
| `src/handle_mcp/cli.py` | `login`, `logout` og `status` i terminalen. |
| `tests/` | Automatiske test. Kør `uv run pytest`. |
| `pyproject.toml` | Projektets "varedeklaration": navn, version, hvilke pakker det bruger, og at kommandoen `handle-mcp` starter `server.py`. |

**Øvelse:** Tilføj et nyt værktøj. Kopiér `vis_kurv` i `server.py`, giv den et nyt navn og en ny docstring, og genstart Claude Desktop. Så kan du se det dukke op.

## 4. Git og GitHub

- **git** er programmet på din PC, der holder styr på versioner. Hver gemt version er et **commit** med en besked og et ID (fx `a1b2c3d`).
- **GitHub** er en hjemmeside, hvor du lægger en kopi af dine commits (**push**), så de er sikret og kan hentes af andre (eller af `uvx`).
- Et **tag** (fx `v0.1.0`) er et navn på et bestemt commit. Det er det, du låser installationen til.

### Første gang: læg projektet på din GitHub

1. Opret en konto på github.com, hvis du ikke har en.
2. Gå til https://github.com/new → navn `handle-mcp` → **Public** → *lad være med* at sætte flueben i README/licens → **Create repository**.
3. Installér Git: https://git-scm.com/download/win (vælg standardindstillingerne).
4. Pak zip-filen ud, fx til `C:\Users\<dig>\kode\handle-mcp`, og kør i PowerShell:

```powershell
cd C:\Users\<dig>\kode\handle-mcp
git config --global user.name "Julius Torp Larsen"
git config --global user.email "<din email>"
git remote add origin https://github.com/JuliusTorpLarsenPrivat/handle-mcp.git
git push -u origin main --tags
```

Første gang åbner et vindue, hvor du logger ind på GitHub. Herefter ligger koden på `github.com/JuliusTorpLarsenPrivat/handle-mcp`.

### Når du ændrer noget senere

```powershell
git status                      # hvad er ændret?
git add .                       # vælg alle ændringer
git commit -m "Hvad og hvorfor" # gem en version lokalt
git push                        # send til GitHub
git tag v0.2.0                  # navngiv versionen
git push --tags
```

Ret derefter `@v0.1.0` til `@v0.2.0` i Claude Desktop-konfigurationen, og genstart appen.

## 5. Regler, der gælder for alle dine MCP'er

1. **Mennesket betaler/beslutter.** Ingen kode, der kan bruge penge, sende noget eller slette noget, medmindre du virkelig har brug for det.
2. **Én dør ud.** Al netværkstrafik går gennem ét sted (her `vagt.py`), med en liste over tilladte adresser.
3. **Hemmeligheder i nøgleringen**, aldrig i koden, i konfigurationsfiler eller på GitHub.
4. **Test, der fejler, hvis reglerne brydes.** Så opdager du det, også når koden er vibe-codet.
5. **Lås versionen**, når du installerer andres kode, og læs ændringerne, før du opdaterer.
