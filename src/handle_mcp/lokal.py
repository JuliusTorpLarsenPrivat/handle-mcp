"""Små JSON-filer på din egen maskine: beholdning, opskrifter og profil.

Filerne ligger uden for OneDrive og sendes aldrig til butikkerne:
  Windows:  %LOCALAPPDATA%\\handle-mcp\\
  ellers:   ~/.local/share/handle-mcp/
Miljøvariablen HANDLE_MCP_DATA kan pege et andet sted hen (bruges af testene).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MAKS_TEGN = 80  # standardgrænse for korte tekstfelter


class LokalFejl(Exception):
    pass


def mappe() -> Path:
    if sti := os.environ.get("HANDLE_MCP_DATA"):
        return Path(sti)
    if lokal := os.environ.get("LOCALAPPDATA"):
        return Path(lokal) / "handle-mcp"
    return Path.home() / ".local" / "share" / "handle-mcp"


def laes(navn: str) -> dict:
    """Indholdet af <navn>.json, eller {} hvis filen ikke findes endnu."""
    try:
        return json.loads((mappe() / f"{navn}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as fejl:
        raise LokalFejl(f"Kunne ikke læse {navn}: {fejl}") from None


def _byt_ind(midlertidig: str, fil: Path, forsoeg: int = 6) -> None:
    """os.replace, men tåler at Windows (fx Defender eller en anden proces, der læser filen)
    låser den et øjeblik. Fundet i testene: en sjælden PermissionError."""
    import time

    for i in range(forsoeg):
        try:
            os.replace(midlertidig, fil)
            return
        except PermissionError:
            if i == forsoeg - 1:
                raise
            time.sleep(0.05 * (i + 1))


def skriv(navn: str, data: Any) -> None:
    """Skriv til en midlertidig fil og byt den ind, så filen aldrig står halvt skrevet."""
    fil = mappe() / f"{navn}.json"
    fil.parent.mkdir(parents=True, exist_ok=True)
    indhold = json.dumps(data, ensure_ascii=False, indent=2)
    fd, midlertidig = tempfile.mkstemp(dir=fil.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(indhold)
        _byt_ind(midlertidig, fil)
    except BaseException:
        Path(midlertidig).unlink(missing_ok=True)
        raise


def tekst(vaerdi: object, felt: str, maks: int = MAKS_TEGN) -> str | None:
    """Rens et tekstfelt: samlet mellemrum, højst `maks` tegn, tom tekst bliver None."""
    if vaerdi is None:
        return None
    t = " ".join(str(vaerdi).split())
    if len(t) > maks:
        raise LokalFejl(f"{felt} må højst være {maks} tegn: {t[:30]}…")
    return t or None


def tekster(vaerdier: list | None, felt: str, maks_antal: int, maks: int = MAKS_TEGN) -> list[str]:
    """En liste af tekstfelter uden tomme og uden dubletter (rækkefølgen bevares)."""
    ud: list[str] = []
    for v in vaerdier or []:
        if (t := tekst(v, felt, maks)) and t.lower() not in (x.lower() for x in ud):
            ud.append(t)
    if len(ud) > maks_antal:
        raise LokalFejl(f"Højst {maks_antal} {felt}.")
    return ud


def noegle(navn: str) -> str:
    return " ".join(navn.lower().split())
