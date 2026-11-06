"""Test af oversættelsen fra butikkernes svar. Svarene her er små, håndskrevne eksempler i
butikkernes format, så testene kører uden internet og uden login."""

import json
from decimal import Decimal

import httpx
import pytest

from handle_mcp.butikker import bilka, nemlig
from handle_mcp.vagt import ny_klient
from datetime import datetime


# --- nemlig -------------------------------------------------------------------


def hit(**kw):
    return {"Id": "100", "Name": "Letmælk", "Price": 12.95, "UnitPriceCalc": 12.95, "UnitPriceLabel": "kr./L.", "Description": "1 l", **kw}


def test_nemlig_almindelig_vare():
    v = nemlig.vare_fra_soegning(hit())
    assert (v.pris, v.enhed, v.tilbud, v.paa_lager) == (Decimal("12.95"), "l", None, True)


def test_nemlig_rabat_bruger_kampagneprisen():
    v = nemlig.vare_fra_soegning(hit(Campaign={"Type": "ProductCampaignDiscountPercent", "CampaignPrice": 9.95, "DiscountPercent": 23, "CampaignUnitPrice": 9.95}))
    assert v.pris == Decimal("9.95") and v.foer_pris == Decimal("12.95") and v.tilbud == "-23 %"


def test_nemlig_maengdetilbud_beholder_stykprisen():
    v = nemlig.vare_fra_soegning(hit(Campaign={"Type": "ProductCampaignMixOffer", "MinQuantity": 3, "TotalPrice": 30}))
    assert v.pris == Decimal("12.95") and v.tilbud == "3 for 30 kr"


def test_nemlig_discount_maerke_er_ikke_tilbud():
    assert nemlig.vare_fra_soegning(hit(DiscountItem=True)).tilbud is None


def test_nemlig_gratis_vare():
    v = nemlig.vare_fra_soegning(hit(Campaign={"Type": "ProductCampaignFreeProduct", "MaxQuantity": 1}))
    assert v.pris == 0 and v.tilbud == "Gratis" and v.max_antal == 1


def test_nemlig_kurv():
    k = nemlig.kurv_fra_svar(
        {
            "Lines": [{"Id": 1, "Name": "Mælk", "Quantity": 2, "Price": 25.9}, {"Id": 2, "Name": "Fjernet", "Quantity": 0, "Price": 5}],
            "TotalProductsPrice": 25.9,
            "DeliveryPrice": 29,
            "TotalBagsPrice": 6,
            "TotalPrice": 60.9,
            "MinimumOrderTotal": 300,
            "IsMinTotalValid": False,
        }
    )
    assert len(k.linjer) == 1 and k.gebyrer == {"levering": 29, "poser": 6} and k.minimum_naaet is False


def test_nemlig_leveringstider():
    data = {"DayRangeHours": [{"DayHours": [{"Date": "2026-11-05T00:00:00", "StartHour": 14, "EndHour": 21, "DeliveryPrice": 19, "Type": 0, "Availability": 0}]}]}
    (t,) = nemlig.leveringstider_fra_svar(data)
    assert t.start.hour == 14 and t.pris == 19 and t.ledig and t.type == "Personlig"


def test_nemlig_opskrift_link():
    assert nemlig.opskrift_id("https://www.nemlig.com/maaltider/opskrift/kebabfad-98001303?x=1") == "kebabfad-98001303"


async def test_nemlig_login_og_soegning(monkeypatch):
    monkeypatch.setenv("KURV_NEMLIG_EMAIL", "test@example.dk")
    monkeypatch.setenv("KURV_NEMLIG_KODE", "hemmelig")
    kald = []

    def butik(r: httpx.Request) -> httpx.Response:
        kald.append(r.url.path)
        match r.url.path:
            case "/webapi/Token":
                return httpx.Response(200, json={"access_token": "tok"})
            case "/webapi/AntiForgery":
                return httpx.Response(200, json={"Value": "xsrf"})
            case "/webapi/login":
                assert json.loads(r.content)["Password"] == "hemmelig"
                return httpx.Response(200, json={"RedirectUrl": "/"})
            case "/webapi/v2/AppSettings/Website" | "/webapi/basket/GetBasket":
                return httpx.Response(200, json={})
            case "/searchgateway/api/search":
                assert r.headers["Authorization"] == "Bearer tok"
                return httpx.Response(200, json={"Products": {"Products": [hit()]}})
        return httpx.Response(404)

    n = nemlig.Nemlig()
    n._klient = ny_klient(transport=httpx.MockTransport(butik))
    (v,) = await n.soeg("mælk")
    assert v.navn == "Letmælk" and "/webapi/login" in kald


async def test_nemlig_forkert_login(monkeypatch):
    monkeypatch.setenv("KURV_NEMLIG_EMAIL", "a@b.dk")
    monkeypatch.setenv("KURV_NEMLIG_KODE", "forkert")

    def butik(r):
        if r.url.path == "/webapi/login":
            return httpx.Response(200, json={"ErrorMessage": "nej"})
        return httpx.Response(200, json={"access_token": "t", "Value": "x"})

    n = nemlig.Nemlig()
    n._klient = ny_klient(transport=httpx.MockTransport(butik))
    with pytest.raises(nemlig.ButikFejl, match="afviste login"):
        await n.log_ind()


# --- Bilka --------------------------------------------------------------------


def bilka_hit(**butik):
    return {"objectID": "555", "name": "Hakkede tomater", "gtin": "5700000000001", "netcontent": "400 g", "price": 1200, "storeData": {"9": {"price": 1200, "inStock": True, "unitsOfMeasurePriceUnit": "Kg.", "unitsOfMeasurePrice": 3000, **butik}}}


def test_bilka_vare_i_oere():
    v = bilka.vare_fra_hit(bilka_hit(), "9")
    assert v.pris == Decimal("12") and v.enhedspris == Decimal("30") and v.enhed == "kg" and v.ean


def test_bilka_tilbud():
    v = bilka.vare_fra_hit(bilka_hit(price=900, offerDescription="Se Her", unitsOfMeasureShowPrice=2250), "9")
    assert v.pris == Decimal("9") and v.foer_pris == Decimal("12") and v.tilbud.startswith("Tilbud, før 12")


def test_bilka_vare_uden_for_butikken_springes_over():
    assert bilka.vare_fra_hit(bilka_hit(), "1") is None


def test_bilka_kurv_og_afhentning():
    data = {
        "lines": [
            {"headline": "Mejeri", "lines": [{"orderlines": [{"product": {"objectID": "1", "name": "Mælk"}, "quantity": 2, "amount": 2590}]}]},
            {"headline": "Services", "lines": [{"orderlines": [{"product": {"objectID": "x", "name": "Pakkeservice"}, "quantity": 1, "amount": 3900}]}]},
        ],
        "stat": {"price": 10490, "delivery_price": 3900, "packing": 3900, "minimum_left": 47410},
        "deliveryAddress": {"minimum_buy": 50000},
    }
    k = bilka.kurv_fra_svar(data)
    assert [ln.navn for ln in k.linjer] == ["Mælk"]
    assert k.gebyrer == {"levering": 39, "pakkeservice": 39} and k.minimum_naaet is False
    assert bilka.IKKE_HJEMLEVERING in k.advarsler


async def test_bilka_login_sender_aldrig_kurv_cookie(monkeypatch):
    monkeypatch.setenv("KURV_BILKA_EMAIL", "a@b.dk")
    monkeypatch.setenv("KURV_BILKA_KODE", "k")

    def butik(r):
        if r.url.host == "accounts.eu1.gigya.com":
            return httpx.Response(200, json={"errorCode": 0, "sessionInfo": {"cookieValue": "lt"}, "id_token": "jwt"})
        assert "cart=" not in r.headers.get("cookie", "")
        return httpx.Response(200, json={"ok": True})

    b = bilka.Bilka()
    b._klient = ny_klient(transport=httpx.MockTransport(butik))
    b._klient.cookies.set("cart", "gammel-kurv", domain="api.bilkatogo.dk")
    await b.log_ind()
    assert b._jwt == "jwt"


def test_bilka_vare_der_ikke_kan_leveres_til_adressen_springes_over():
    """Fundet i brug: fersk laks (blockbit 12) kunne ikke bestilles til adressen (blockbit 7)."""
    assert bilka.vare_fra_hit({**bilka_hit(), "blockbit": 12}, "9", adresse_blockbit=7) is None
    assert bilka.vare_fra_hit({**bilka_hit(), "blockbit": 0}, "9", adresse_blockbit=7) is not None
    assert bilka.vare_fra_hit({**bilka_hit(), "blockbit": 8}, "9", adresse_blockbit=7) is not None


class FalskBilkaKurv:
    """Bilka, der lægger en fjernet vare tilbage, når der senere lægges noget andet i."""

    def __init__(self):
        self.kurv = {"1": 1, "2": 1}
        self.fjernet_igen = []

    def __call__(self, request):
        if request.url.path.endswith("ChangeLineCount"):
            vid, n = request.url.params["productId"], int(request.url.params["count"])
            if n == 0:
                self.fjernet_igen.append(vid)
                self.kurv.pop(vid, None)
            else:
                self.kurv[vid] = n
                if vid == "3":
                    self.kurv["1"] = 1  # fejlen: den fjernede vare kommer tilbage
            return httpx.Response(200, json={"productAmount": n})
        if request.url.path.endswith("/Cart"):
            linjer = [{"product": {"objectID": k, "name": f"vare {k}"}, "quantity": v, "amount": 1000} for k, v in self.kurv.items()]
            return httpx.Response(200, json={"stat": {}, "lines": [{"headline": "Kød", "lines": [{"orderlines": linjer}]}], "deliveryAddress": {"dawa_uid": "x"}})
        return httpx.Response(404)


async def test_bilka_fjernet_vare_der_kommer_tilbage_fjernes_igen(monkeypatch):
    falsk = FalskBilkaKurv()
    b = bilka.Bilka()
    b._klient = ny_klient(transport=httpx.MockTransport(falsk))
    b._jwt, b._jwt_udloeber = "jwt", datetime.max.replace(tzinfo=bilka.DK)
    await b.saet_antal({"1": 0})
    assert "1" not in falsk.kurv
    # en anden proces (fx lytteren) har en anden instans: huskes via filen
    b2 = bilka.Bilka()
    b2._klient, b2._jwt, b2._jwt_udloeber = b._klient, "jwt", b._jwt_udloeber
    res = await b2.saet_antal({"3": 1})
    assert "1" not in falsk.kurv and set(falsk.kurv) == {"2", "3"}
    assert any(r.id == "1" and r.ok and "igen" in r.besked for r in res)
    # lægges varen bevidst i igen, glemmes den som fjernet
    await b2.saet_antal({"1": 2})
    await b2.saet_antal({"4": 1})
    assert falsk.kurv["1"] == 2
