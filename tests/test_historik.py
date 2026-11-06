from datetime import date

import pytest

from handle_mcp import historik
from handle_mcp.lokal import LokalFejl, skriv


@pytest.fixture(autouse=True)
def egen_mappe(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path))


def kv(dag, *varer, total=None):
    v = [{"navn": n, "pris": p, "rabat": r} for n, p, r in varer]
    return {"tid": f"{dag}T12:00:00Z", "butik": "SuperBrugsen", "total": total if total is not None else sum(p + r for _, p, r in varer), "varer": v}


def gem(*kvitteringer):
    skriv("coop_kvitteringer", {"kilde": "test", "kvitteringer": list(kvitteringer)})


@pytest.mark.parametrize(
    "navn, kat",
    [
        ("ØKO GRÆSK YOGHURT", "mejeri og æg"),
        ("KOLDSKÅL", "mejeri og æg"),  # ikke kål
        ("FLÆSKESVÆR", "snacks og søde sager"),  # ikke kød
        ("ØFFER SALT 75G", "snacks og søde sager"),
        ("2 x STEGEFLÆSK 450 G", "kød"),
        ("ENTRECOTES 400 G", "kød"),
        ("ØKO TOMATER", "frugt og grønt"),
        ("ØKO DARK CHOKOLADE", "snacks og søde sager"),
        ("APOLLINARIS CITRUS", "drikkevarer"),
        ("TOILETPAPIR 6 RL", "ikke mad"),
        ("PANT", "pant"),
        ("ØKO RUGBRØD", "kolonial"),
        ("RISENGRØD", "andet"),  # "RIS" skal være et helt ord
        ("ØKO NAKKEKOTELET BBQ", "kød"),  # lange nøgler må stå inde i ordet
        ("ØKO BLADSELLERI", "frugt og grønt"),
        ("ØKO GRÆSK INSP YOG", "mejeri og æg"),  # Brugsens forkortelse
        ("FAZER TYRKISK PEBER", "snacks og søde sager"),  # ikke kolonial
        ("ØKO PEBERFRUGTER", "frugt og grønt"),
    ],
)
def test_kategori(navn, kat):
    assert historik.kategori(navn) == kat


def test_ingen_kvitteringer_giver_klar_fejl():
    with pytest.raises(LokalFejl, match="ingen kvitteringer"):
        historik.moenster()


def test_basisvarer_budget_og_favoritter():
    gem(
        kv("2026-10-01", ("ØKO GRÆSK YOGHURT", 40, -10), ("ENTRECOTES 400 G", 90, 0), ("PANT", 3, 0)),
        kv("2026-10-11", ("ØKO GRÆSK YOGHURT", 40, 0), ("TOILETPAPIR 6 RL", 39, 0)),
        kv("2026-10-21", ("2 x ØKO GRÆSK YOGHURT", 80, 0), ("ENTRECOTES 400 G", 90, 0)),
        kv("2026-10-29", ("LAKS 300 G", 60, 0)),
    )
    m = historik.moenster(andel_af_indkoeb=0.7, i_dag=date(2026, 10, 30))
    (yoghurt,) = m["basisvarer"]  # kun yoghurt er købt 3 gange
    assert yoghurt["vare"] == "ØKO GRÆSK YOGHURT" and yoghurt["typisk_interval_dage"] == 10
    assert yoghurt["dage_siden_sidst"] == 9 and yoghurt["snart_tom"]
    assert m["periode"]["uger"] == 4.0
    total = 30 + 90 + 3 + 40 + 39 + 80 + 90 + 60
    assert m["budget"]["kvitteringer_kr_pr_uge"] == round(total / 4)
    assert m["budget"]["skoennet_samlet_kr_pr_uge"] == round(total / 4 / 0.7)
    assert m["budget"]["heraf_ikke_mad_og_pant_kr_pr_uge"] == round(42 / 4)
    assert m["favoritter"]["kød"] == ["ENTRECOTES 400 G"] and m["favoritter"]["fisk"] == ["LAKS 300 G"]
    assert m["oeko_andel"] == 0.5  # 3 af 6 linjer uden pant
    assert set(m["kategorier_andel"]) == {"mejeri og æg", "kød", "fisk"}


def test_saesonvare_er_ikke_snart_tom():
    """Fundet i jeres data: koldskål (hver 14. dag) ikke købt i 69 dage er ikke 'snart tom'."""
    gem(*(kv(d, ("ØKO KOLDSKÅL", 25, 0)) for d in ("2026-06-01", "2026-06-15", "2026-06-29")))
    (b,) = historik.moenster(i_dag=date(2026, 9, 6))["basisvarer"]
    assert b["ikke_koebt_laenge"] and not b["snart_tom"]


def test_flet_nye_kvitteringer_uden_dubletter():
    a, b, c = kv("2026-10-01", ("MÆLK", 10, 0)), kv("2026-10-08", ("MÆLK", 10, 0)), kv("2026-10-15", ("SMØR", 20, 0))
    assert historik.flet_kvitteringer([a, b]) == (2, 2)
    assert historik.flet_kvitteringer([b, c, c]) == (1, 3)  # b kendes, c to gange i samme hentning
    assert [k["tid"][:10] for k in historik.kvitteringer()] == ["2026-10-15", "2026-10-08", "2026-10-01"]


@pytest.mark.parametrize(
    "skidt",
    [
        # opdigtede numre i samme format som på kvitteringerne
        {"tid": "2026-10-01T12:00:00Z", "total": 10, "varer": [{"navn": "PAN: 123456XXXXXX0000", "pris": 0, "rabat": 0}]},
        {"tid": "2026-10-01T12:00:00Z", "total": 10, "varer": [], "note": "Medlemsnummer: 12345678"},
        {"tid": "2026-10-01T12:00:00Z", "varer": []},  # mangler total
    ],
)
def test_flet_afviser_persondata_og_ufuldstaendige(skidt):
    with pytest.raises(LokalFejl):
        historik.flet_kvitteringer([skidt])


def test_ugyldig_andel():
    gem(kv("2026-10-01", ("MÆLK", 10, 0)))
    with pytest.raises(LokalFejl):
        historik.moenster(andel_af_indkoeb=2)


async def test_vaerktoejet_i_serveren():
    from handle_mcp import server

    gem(kv("2026-10-01", ("MÆLK", 10, 0)), kv("2026-10-08", ("MÆLK", 10, 0)), kv("2026-10-15", ("MÆLK", 10, 0)))
    svar = await server.koebsmoenster(1.0)
    assert svar["basisvarer"][0]["vare"] == "MÆLK"


# --- basisvarer pr. type (Julius: "der skal laves et system for basisvarer") ---------------


@pytest.mark.parametrize(
    "navn, typ",
    [
        ("FRILANDSÆG 10 STK", "æg"), ("ÆG M/L 8 STK", "æg"), ("ØKO THISE SMØR 200 G", "smør"),
        ("ØKO GRÆSMÆLK", "mælk"), ("ØKO HVEDEMEL", "hvedemel"), ("ØKO SPAGHETTI", "pasta"),
        ("ØKO DARK CHOKOLADE", "mørk chokolade"), ("SORT PEBER KVÆRN", "peber"),
        ("TANDPASTA 2-PAK", None), ("ØFFER SALT 75G", None), ("FAZER TYRKISK PEBER", None),
        ("GRØN PEBER", None), ("ØKO PEBERFRUGTER", None),
    ],
)
def test_basistype(navn, typ):
    assert historik.basistype(navn) == typ


def test_basisvarer_status_pr_type():
    from datetime import date

    from handle_mcp.lokal import skriv

    def kv(dag, *varer):
        return {"tid": f"2026-10-{dag:02d}T10:00:00+00:00", "total": 100, "butik": "x", "varer": [{"navn": v, "pris": 10} for v in varer]}

    skriv("coop_kvitteringer", {"kvitteringer": [
        kv(1, "FRILANDSÆG 10 STK", "ØKO HVEDEMEL"), kv(11, "ÆG M/L 8 STK"), kv(21, "FRILANDSÆG 10 STK", "ØKO HVEDEMEL"),
    ]})
    status = {b["type"]: b for b in historik.basisvarer(date(2026, 10, 30))}
    assert status["æg"]["gange"] == 3 and status["æg"]["typisk_interval_dage"] == 10
    assert status["æg"]["status"] == "snart tom" and status["æg"]["sted"] == "køl"  # 9 dage ≥ 0,8 × 10
    assert status["hvedemel"]["status"] == "købt for nylig" and status["hvedemel"]["sted"] == "skab"  # 9 < 0,5 × 20
    assert historik.moenster(i_dag=date(2026, 10, 30))["basisvarer_pr_type"]


async def test_butiksordrer_taeller_med():
    from decimal import Decimal

    from handle_mcp.data import Kurvlinje, Ordre

    class Butik:
        navn = "nemlig"

        async def ordrer(self, antal):
            return [Ordre("nemlig", "o1", "Leveret", Decimal(100), "2026-10-25T08:00:00"), Ordre("nemlig", "o2", "Annulleret", None, "2026-10-26T08:00:00")]

        async def ordre(self, oid):
            return Ordre("nemlig", oid, "Leveret", Decimal(42), "2026-10-25T08:00:00", [Kurvlinje("1", "Økologiske æg M/L", 1, Decimal(42))])

    assert await historik.hent_butiksordrer([Butik()]) == 1
    assert await historik.hent_butiksordrer([Butik()]) == 0  # kun én gang
    assert {b["type"] for b in historik.basisvarer()} == {"æg"}
