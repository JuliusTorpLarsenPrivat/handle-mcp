"""Ny kode uden genstart: genindlaes.py. Testes på en lille pakke i en midlertidig mappe,
så de rigtige moduler ikke skiftes ud midt i testkørslen."""

import importlib
import os
import sys
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from handle_mcp import genindlaes, server


def skriv(fil, tekst):
    fil.write_text(tekst, encoding="utf-8")
    t = time.time() + 2  # sikker ny mtime, også på filsystemer med grov tidsopløsning
    os.utime(fil, (t, t))


@pytest.fixture
def pakke(tmp_path, monkeypatch):
    """gentest.a (VAERDI og Fejl) og gentest.b, der importerer fra a med `from … import …`."""
    p = tmp_path / "gentest"
    p.mkdir()
    (p / "__init__.py").write_text("", encoding="utf-8")
    (p / "a.py").write_text("VAERDI = 1\nclass Fejl(Exception):\n    pass\n", encoding="utf-8")
    (p / "b.py").write_text(
        "from gentest.a import VAERDI, Fejl\n\ndef hent():\n    return VAERDI\n\ndef fejl():\n    raise Fejl('x')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import gentest.a  # noqa: F401
    import gentest.b  # noqa: F401

    monkeypatch.setattr(genindlaes, "RAEKKEFOLGE", ("gentest.a", "gentest.b"))
    monkeypatch.setattr(genindlaes, "_kendt", {})
    genindlaes.start()
    yield p
    for m in ("gentest.b", "gentest.a", "gentest"):
        sys.modules.pop(m, None)


def test_intet_aendret(pakke):
    assert genindlaes.tjek() == []


def test_aendret_modul_og_dem_efter_det_genindlaeses(pakke):
    skriv(pakke / "a.py", "VAERDI = 2\nclass Fejl(Exception):\n    pass\n")
    assert genindlaes.tjek() == ["gentest.a", "gentest.b"]
    assert sys.modules["gentest.b"].hent() == 2  # b har fået fat i den nye værdi
    assert genindlaes.tjek() == []  # og kun én gang


def test_foerste_indlaesning_er_ikke_en_aendring(pakke, monkeypatch):
    """Fundet i ende-til-ende-testen: Bilka indlæses først ved første Bilka-søgning, og det
    blev opfattet som ny kode, så halvdelen af modulerne blev genindlæst uden grund."""
    (pakke / "c.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(genindlaes, "RAEKKEFOLGE", ("gentest.a", "gentest.b", "gentest.c"))
    genindlaes.start()  # c er ikke indlæst endnu
    # Pythons import-cache kan have mappens indhold fra før c.py blev skrevet (samme tidsstempel
    # på mappen), så importen af og til ikke fandt filen. Fundet som ustabil test i v0.16.0.
    importlib.invalidate_caches()
    import gentest.c  # noqa: F401  nu bliver den brugt for første gang

    try:
        assert genindlaes.tjek() == []
        skriv(pakke / "c.py", "X = 2\n")
        assert genindlaes.tjek() == ["gentest.c"]  # men en rigtig ændring opdages stadig
    finally:
        sys.modules.pop("gentest.c", None)


def test_laasen_overlever_genindlaesning(tmp_path, monkeypatch):
    """Ellers kunne en lytter nr. to starte, når lytteren selv fik ny kode."""
    import importlib

    from handle_mcp import telegram

    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    assert telegram.tag_laas()
    try:
        importlib.reload(telegram)
        assert telegram._laas_fil is not None and telegram.lytter_koerer()
    finally:
        telegram.slip_laas()


def test_ny_fejlklasse_fanges_med_modulopslag(pakke):
    """Derfor slår server.py fejlklasser op i modulet ved hvert kald (butik_modul.ButikFejl)."""
    gammel = sys.modules["gentest.a"].Fejl
    skriv(pakke / "a.py", "VAERDI = 1\nclass Fejl(Exception):\n    pass\n")
    genindlaes.tjek()
    with pytest.raises(sys.modules["gentest.a"].Fejl):
        sys.modules["gentest.b"].fejl()
    assert sys.modules["gentest.a"].Fejl is not gammel


def test_tastefejl_skifter_intet_ud(pakke):
    skriv(pakke / "a.py", "VAERDI = = 3\n")
    with pytest.raises(SyntaxError):
        genindlaes.tjek()
    assert sys.modules["gentest.b"].hent() == 1  # den gamle kode kører videre
    skriv(pakke / "a.py", "VAERDI = 3\nclass Fejl(Exception):\n    pass\n")
    assert genindlaes.tjek() == ["gentest.a", "gentest.b"]  # næste forsøg virker, når fejlen er rettet
    assert sys.modules["gentest.b"].hent() == 3


async def test_tastefejl_giver_klar_besked_til_claude(pakke, tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    skriv(pakke / "a.py", "VAERDI = = 3\n")
    with pytest.raises(ToolError, match="kunne ikke indlæses, så intet er ændret"):
        await server.vis_beholdning()


async def test_vaerktoejerne_beholder_deres_felter():
    """Indpakningen må ikke skjule parametrene for Claude."""
    vaerktoejer = {t.name: t for t in await server.mcp.list_tools()}
    assert len(vaerktoejer) == 25
    felter = vaerktoejer["sammenlign_liste"].input_schema["properties"]
    assert {"varer", "antal", "butikker", "oekologisk"} <= set(felter)
    assert "OK" in vaerktoejer["fyld_kurv_fra_madplan"].description


def test_alle_moduler_er_med_i_raekkefoelgen():
    """Et nyt modul skal også kunne genindlæses: læg det i RAEKKEFOLGE efter dem, det importerer fra."""
    from pathlib import Path

    mappe = Path(genindlaes.__file__).parent
    filer = {f"handle_mcp.{f.stem}" for f in mappe.glob("*.py")} | {f"handle_mcp.butikker.{f.stem}" for f in (mappe / "butikker").glob("*.py")}
    filer = {m.removesuffix(".__init__") for m in filer}
    ikke_med = filer - set(genindlaes.RAEKKEFOLGE) - {"handle_mcp", "handle_mcp.server", "handle_mcp.cli", "handle_mcp.netvaerk", "handle_mcp.genindlaes"}
    assert ikke_med == set()
