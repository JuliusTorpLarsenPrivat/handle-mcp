import pytest

from handle_mcp import profil, samling
from handle_mcp.lokal import LokalFejl


@pytest.fixture(autouse=True)
def egen_mappe(tmp_path, monkeypatch):
    """Hver test får sin egen tomme mappe, så de rigtige filer aldrig røres."""
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    return tmp_path


# --- Opskriftssamling ------------------------------------------------------


def test_gem_find_og_fjern_opskrift():
    samling.gem("Spaghetti bolognese", ingredienser=["500 g hakket oksekød", "hakkede tomater"], tags=["hverdag", "Hverdag"], personer=4)
    samling.gem("Linsesuppe", tags=["vegetar", "hverdag"], note="dobbelt spidskommen")
    assert [o.navn for o in samling.hent()] == ["Linsesuppe", "Spaghetti bolognese"]
    assert [o.navn for o in samling.hent(tag="VEGETAR")] == ["Linsesuppe"]
    assert [o.navn for o in samling.hent(soeg="oksekød")] == ["Spaghetti bolognese"]
    assert [o.navn for o in samling.hent(soeg="spidskommen")] == ["Linsesuppe"]
    assert samling.hent(soeg="bolognese")[0].tags == ["hverdag"]  # dubletter fjernes
    assert samling.fjern("linsesuppe") and not samling.fjern("linsesuppe")
    assert [o.navn for o in samling.hent()] == ["Spaghetti bolognese"]


def test_gem_igen_erstatter_og_beholder_dato():
    foerste = samling.gem("Pasta", note="gammel")
    anden = samling.gem("pasta", note="ny")
    assert len(samling.hent()) == 1 and anden.note == "ny" and anden.tilfoejet == foerste.tilfoejet


@pytest.mark.parametrize(
    "kwargs",
    [
        {"navn": ""},
        {"navn": "x" * 81},
        {"navn": "Pasta", "link": "javascript:alert(1)"},
        {"navn": "Pasta", "personer": 0},
        {"navn": "Pasta", "ingredienser": [f"vare {i}" for i in range(61)]},
    ],
)
def test_ugyldige_opskrifter_afvises(kwargs):
    with pytest.raises(LokalFejl):
        samling.gem(**kwargs)
    assert samling.hent() == []


# --- Profil -----------------------------------------------------------------


def test_tom_profil():
    p = profil.hent()
    assert p.personer is None and p.ugebudget_kr is None and p.praeferencer == []


def test_kun_givne_felter_aendres():
    profil.opdater(personer=2, ugebudget_kr=1200, undgaa=["svampe"])
    p = profil.opdater(praeferencer=["vegetar om mandagen"])
    assert (p.personer, p.ugebudget_kr, p.undgaa, p.praeferencer) == (2, 1200, ["svampe"], ["vegetar om mandagen"])
    assert profil.hent() == p  # gemt på disk
    assert profil.opdater(ugebudget_kr=0).ugebudget_kr is None  # 0 fjerner budgettet


def test_faste_maaltider_og_vareoensker():
    p = profil.opdater(
        faste_maaltider=["Morgenmad hver dag, 2 pers.: græsk yoghurt, vindruer, mandler, mørk chokolade"],
        varepraeferencer=["Mørk chokolade: meget mørk, mindst 85 %"],
    )
    assert profil.hent() == p and p.varepraeferencer == ["Mørk chokolade: meget mørk, mindst 85 %"]
    assert profil.opdater(personer=2).faste_maaltider == p.faste_maaltider  # bliver, når andet rettes


def test_gammel_profil_kan_stadig_laeses():
    """Profiler gemt før de nye felter (eller med ukendte felter) må ikke vælte."""
    from handle_mcp.lokal import skriv

    skriv("profil", {"personer": 2, "ugebudget_kr": 1500, "felt_fra_fremtiden": 1})
    p = profil.hent()
    assert (p.personer, p.faste_maaltider, p.varepraeferencer) == (2, [], [])


@pytest.mark.parametrize(
    "kwargs",
    [{"personer": 0}, {"ugebudget_kr": -1}, {"ugebudget_kr": 50000}, {"undgaa": ["x" * 81]}, {"varepraeferencer": ["x" * 151]}],
)
def test_ugyldig_profil_afvises(kwargs):
    with pytest.raises(LokalFejl):
        profil.opdater(**kwargs)


# --- Gennem serveren ---------------------------------------------------------


async def test_vaerktoejerne_i_serveren():
    from mcp.server.mcpserver.exceptions import ToolError

    from handle_mcp import server

    await server.opdater_profil({"personer": 2, "ugebudget_kr": 1000})
    assert (await server.vis_profil())["ugebudget_kr"] == 1000
    await server.gem_opskrift("Tacos", link="https://www.nemlig.com/opskrifter/tacos", tags=["fredag"])
    assert (await server.vis_opskrifter(tag="fredag"))[0]["link"].endswith("/tacos")
    assert await server.fjern_opskrift("tacos") == {"fjernet": "tacos"}
    with pytest.raises(ToolError, match="ingen opskrift"):
        await server.fjern_opskrift("tacos")
    with pytest.raises(ToolError, match="Personer"):
        await server.opdater_profil({"personer": 99})


async def test_ukendte_felter_giver_klar_besked():
    from mcp.server.mcpserver.exceptions import ToolError

    from handle_mcp import server

    with pytest.raises(ToolError, match="Ukendte felter: farve.*Mulige felter: personer"):
        await server.opdater_profil({"farve": "blå"})
    assert "varepraeferencer" in (await server.vis_profil())["felter"]
