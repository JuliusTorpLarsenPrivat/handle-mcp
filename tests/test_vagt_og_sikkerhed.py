"""Sikkerhedstest. Fejler en af dem, er noget gået galt: ret koden, ikke testen."""

from pathlib import Path

import httpx
import pytest

from handle_mcp.persondata import FJERNET, rens
from handle_mcp.vagt import FORBUDTE_ORD, VagtFejl, ny_klient, tjek_url

KODE = Path(__file__).parents[1] / "src" / "handle_mcp"


def test_kendte_vaerter_er_tilladt():
    tjek_url(httpx.URL("https://www.nemlig.com/webapi/basket/GetBasket"))
    tjek_url(httpx.URL("https://api.bilkatogo.dk/api/shop/v6/Cart"))


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/steal",
        "https://www.nemlig.com.evil.dk/webapi/login",
        "https://www.nemlig.com/webapi/Order/PlaceOrderLoggedIn",
        "https://www.nemlig.com/webapi/Checkout/GetCreditCards",
        "https://api.bilkatogo.dk/api/shop/SetDeliveryDate",
        "https://www.nemlig.com/webapi/v2/Delivery/UpdateDeliveryTime",
    ],
)
def test_ukendt_vaert_og_betaling_afvises(url):
    with pytest.raises(VagtFejl):
        tjek_url(httpx.URL(url))


def test_koe_hos_nemlig_giver_klar_besked():
    with pytest.raises(VagtFejl, match="kø"):
        tjek_url(httpx.URL("https://nemlig.queue-it.net/?c=x"))


async def test_vagten_stopper_kaldet_foer_det_sendes():
    sendt = []
    klient = ny_klient(transport=httpx.MockTransport(lambda r: sendt.append(r) or httpx.Response(200)))
    with pytest.raises(VagtFejl):
        await klient.get("https://evil.example.com/")
    assert sendt == []


async def test_vagten_stopper_omdirigering_til_fremmed_adresse():
    def svar(r):
        if r.url.host == "www.nemlig.com":
            return httpx.Response(302, headers={"location": "https://evil.example.com/"})
        return httpx.Response(200)

    with pytest.raises(VagtFejl):
        await ny_klient(transport=httpx.MockTransport(svar)).get("https://www.nemlig.com/x")


def test_ingen_forbudte_kald_i_koden():
    """Ingen fil uden for vagt.py må nævne bestilling, betaling eller valg af levering."""
    for fil in KODE.rglob("*.py"):
        if fil.name == "vagt.py":
            continue
        tekst = fil.read_text(encoding="utf-8").lower()
        for ord_ in FORBUDTE_ORD:
            assert ord_ not in tekst, f"{fil.name} indeholder '{ord_}'"


def test_alle_http_klienter_har_vagten():
    """Kun vagt.py må lave en httpx-klient. Ellers kunne et kald slippe uden om vagten."""
    for fil in KODE.rglob("*.py"):
        if fil.name != "vagt.py":
            tekst = fil.read_text(encoding="utf-8")
            assert "httpx.AsyncClient(" not in tekst and "httpx.Client(" not in tekst, fil.name
            assert "import requests" not in tekst and "urllib.request" not in tekst, fil.name


def test_persondata_fjernes():
    data = {
        "CustomerName": "Julius",
        "DeliveryAddress": {"Street": "Mosevej 1"},
        "Lines": [{"Id": "12345678", "Name": "Mælk", "note": "ring 12 34 56 78 eller a@b.dk"}],
    }
    renset = rens(data)
    assert renset["CustomerName"] == FJERNET
    assert renset["DeliveryAddress"] == FJERNET
    assert renset["Lines"][0]["Id"] == "12345678"  # varenumre bliver
    assert "12 34 56 78" not in renset["Lines"][0]["note"]
    assert "a@b.dk" not in renset["Lines"][0]["note"]


@pytest.mark.parametrize(
    "tekst",
    ["ring 12345678", "tlf: +45 12 34 56 78.", "Mobil 12 34 56 78, tak", "(12345678)"],
)
def test_telefonnumre_fjernes(tekst):
    assert FJERNET in rens(tekst)


@pytest.mark.parametrize(
    "tekst",
    [
        "https://www.nemlig.com/maaltider/opskrift/citronpasta-laks-asparges-98004249",
        "citronpasta-laks-asparges-98004249",
        "?id=12345678&x=1",
        "vare12345678",
    ],
)
def test_id_i_links_bliver(tekst):
    """Fundet live: opskriftslinks fra nemlig slutter på et 8-cifret id, som blev fjernet."""
    assert rens(tekst) == tekst
