"""Vagt mod unødige genstarter af Claude Desktop.

Claude Desktop læser værktøjernes navne, felter og beskrivelser (og serverens instruktioner og
prompts) ÉN gang, når serveren starter. Alt andet indlæses uden genstart (genindlaes.py).
Julius har bedt om, at det ikke skal genstartes hele tiden, så denne test fejler, hvis noget af
det, der kræver genstart, er ændret. Ny logik, nye profilfelter og nye valg hører hjemme i
modulerne (fx profil.FELTER, madplan.VALG, ugeplan.py), ikke i server.py.

Er en ændring virkelig nødvendig: sig det til Julius FØR den laves, saml flere i én genstart,
og opdater aftrykket med:  HANDLE_MCP_NYT_AFTRYK=1 pytest tests/test_ingen_genstart.py
"""

import hashlib
import json
import os
from pathlib import Path

from handle_mcp import server

AFTRYK = Path(__file__).with_name("opstart_aftryk.json")


async def _det_claude_desktop_laeser_ved_start() -> dict:
    vaerktoejer = {}
    for t in await server.mcp.list_tools():
        d = t.model_dump(mode="json", exclude_none=True)
        vaerktoejer[t.name] = {k: d.get(k) for k in ("description", "inputSchema", "input_schema", "annotations") if k in d}
    prompts = {p.name: p.model_dump(mode="json", exclude_none=True) for p in await server.mcp.list_prompts()}
    return {"instruktioner": server.mcp.instructions, "vaerktoejer": vaerktoejer, "prompts": prompts}


async def test_intet_der_kraever_genstart_er_aendret():
    nu = await _det_claude_desktop_laeser_ved_start()
    tekst = json.dumps(nu, ensure_ascii=False, indent=1, sort_keys=True)
    if os.environ.get("HANDLE_MCP_NYT_AFTRYK") == "1" or not AFTRYK.exists():
        AFTRYK.write_text(tekst, encoding="utf-8")
        return
    gemt = json.loads(AFTRYK.read_text(encoding="utf-8"))
    aendret = sorted(
        {n for n in set(nu["vaerktoejer"]) | set(gemt["vaerktoejer"]) if nu["vaerktoejer"].get(n) != gemt["vaerktoejer"].get(n)}
    )
    if nu["instruktioner"] != gemt["instruktioner"]:
        aendret.append("(serverens instruktioner)")
    if nu["prompts"] != gemt["prompts"]:
        aendret.append("(prompts)")
    assert not aendret, (
        f"Kræver genstart af Claude Desktop: {', '.join(aendret)}. Læg ændringen i et modul i stedet, "
        "eller aftal genstarten med Julius først (se øverst i filen). "
        f"Aftryk {hashlib.sha256(tekst.encode()).hexdigest()[:8]}"
    )
