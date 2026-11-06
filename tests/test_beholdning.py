import json

import pytest

from handle_mcp import beholdning
from handle_mcp.beholdning import BeholdningFejl


@pytest.fixture(autouse=True)
def egen_mappe(tmp_path, monkeypatch):
    """Hver test får sin egen tomme mappe, så den rigtige beholdning aldrig røres."""
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))
    return tmp_path


def navne(varer):
    return [v.navn for v in varer]


def test_tom_beholdning_uden_fil():
    assert beholdning.hent() == []


def test_tilfoej_ret_og_fjern(egen_mappe):
    beholdning.opdater([{"navn": "Løg", "maengde": "2 stk", "sted": "skab"}, {"navn": "smør", "sted": "køleskab"}])
    varer, ukendte = beholdning.opdater([{"navn": "løg", "maengde": "1 stk"}], fjern=["Smør", "safran"])
    assert navne(varer) == ["løg"] and varer[0].maengde == "1 stk"
    assert ukendte == ["safran"]
    assert navne(beholdning.hent()) == ["løg"]  # gemt på disk
    assert json.loads((egen_mappe / "beholdning.json").read_text(encoding="utf-8"))["varer"][0]["navn"] == "løg"


def test_erstat_alt_efter_kig_i_koeleskabet():
    beholdning.opdater([{"navn": "mælk"}, {"navn": "æg"}])
    varer, _ = beholdning.opdater([{"navn": "ost", "sted": "køleskab"}], erstat_alt=True)
    assert navne(varer) == ["ost"]


def test_sorteret_efter_sted_og_navn():
    varer, _ = beholdning.opdater(
        [{"navn": "ris", "sted": "skab"}, {"navn": "æg", "sted": "køleskab"}, {"navn": "agurk", "sted": "køleskab"}, {"navn": "salt"}]
    )
    assert navne(varer) == ["agurk", "æg", "ris", "salt"]


@pytest.mark.parametrize("vare", [{"navn": ""}, {"navn": "x" * 81}, {"maengde": "2"}])
def test_ugyldige_varer_afvises_og_intet_gemmes(vare):
    beholdning.opdater([{"navn": "mælk"}])
    with pytest.raises(BeholdningFejl):
        beholdning.opdater([vare])
    assert navne(beholdning.hent()) == ["mælk"]


def test_for_mange_varer_afvises(monkeypatch):
    monkeypatch.setattr(beholdning, "MAKS_VARER", 2)
    with pytest.raises(BeholdningFejl):
        beholdning.opdater([{"navn": "a"}, {"navn": "b"}, {"navn": "c"}])


async def test_vaerktoejerne_i_serveren():
    from handle_mcp import server

    svar = await server.opdater_beholdning(tilfoej=[{"navn": "hakkede tomater", "maengde": "3 dåser"}])
    assert svar["beholdning"][0]["navn"] == "hakkede tomater" and svar["ikke_fundet"] == []
    assert (await server.vis_beholdning())[0]["maengde"] == "3 dåser"


def test_gamle_rester_i_koeleskabet_ryddes(monkeypatch):
    """Fundet i test 3: en tærterest fra en anden uge kom med i menuen."""
    from handle_mcp import beholdning
    from handle_mcp.lokal import skriv

    skriv("beholdning", {"varer": [
        {"navn": "rester af tærte", "maengde": "1/2", "sted": "køleskab", "noteret": "2026-10-01T18:00+01:00"},
        {"navn": "grågås", "maengde": "1", "sted": "fryser", "noteret": "2026-10-01T18:00+01:00"},
        {"navn": "smør", "maengde": "1", "sted": "køleskab", "noteret": "2099-01-01T18:00+01:00"},
    ]})
    assert beholdning.ryd_gammelt_koeleskab() == ["rester af tærte"]
    assert {v.navn for v in beholdning.hent()} == {"grågås", "smør"}  # fryseren holder
