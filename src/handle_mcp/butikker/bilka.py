"""BilkaToGo via det API, bilkatogo.dk selv bruger. Ikke officielt, så det kan ændre sig.

Alle beløb kommer i øre (3900 = 39 kr).

Login sker i tre trin:
  1. Gigya accounts.login   → et login-token (Gigya er Salling Groups login-tjeneste fra SAP)
  2. Gigya accounts.getJWT  → et JWT, der holder 24 timer
  3. Bilka auth/LoginJWT    → en session hos BilkaToGo
Søgning går til Algolia (en søgetjeneste), med den offentlige søgenøgle fra hjemmesiden.

Fælde: Send aldrig en 'cart'-cookie med ved login. Så erstatter Bilka kontoens kurv med den.

Fundet i brug (uge 48):
- Nogle varer findes i butikkens sortiment, men kan ikke leveres til adressen ("Du kan desværre
  ikke bestille dette produkt til HD Odense"), fx fersk laks. Varen og adressen har hver en
  `blockbit`, og har de en bit til fælles, kan varen ikke bestilles. Sådanne varer vises ikke.
- En fjernet vare kom tilbage i kurven, da der senere blev lagt andet i (lytteren og serveren er
  to processer med hver sin session). Fjernede varer huskes derfor i bilka-fjernet.json og
  fjernes igen, hvis de dukker op.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from handle_mcp import login
from handle_mcp.lokal import laes, skriv
from handle_mcp.butikker import ButikFejl
from handle_mcp.data import AntalResultat, Kurv, Kurvlinje, Leveringstid, Ordre, Vare
from handle_mcp.vagt import ny_klient, send

GIGYA = "https://accounts.eu1.gigya.com"
# Offentlige nøgler fra bilkatogo.dk's egen JavaScript. Ikke hemmelige: alle besøgende sender dem.
GIGYA_NOEGLE = "3_tA6BbV434FQqN73HnUG1KA3qFv8KiG4OqLu9eWPh7sKRqRizH5Vfv5Larmgrb4I2"
API = "https://api.bilkatogo.dk/api"
ALGOLIA = "https://f9vbjlr1bk-dsn.algolia.net/1/indexes/*/queries"
ALGOLIA_HEADERS = {"x-algolia-application-id": "F9VBJLR1BK", "x-algolia-api-key": "1deaf41c87e729779f7695c00f190cc9"}
ALGOLIA_INDEKS = "prod_BILKATOGO_PRODUCTS"
DK = ZoneInfo("Europe/Copenhagen")
FJERNET_FIL = "bilka-fjernet"
FJERNET_HUSKES = 3 * 24 * 3600  # sekunder


def tid_nu() -> float:
    import time as _t

    return _t.time()
GEBYRGRUPPER = {"Emballage", "Services"}  # kurvgrupper, der er gebyrer og ikke varer
IKKE_HJEMLEVERING = "Kurven er sat til afhentning. Vælg hjemlevering på bilkatogo.dk."


def kr(oere: Any) -> Decimal:
    return Decimal(int(oere or 0)) / 100


def er_hjemlevering(adresse: dict) -> bool:
    """Hjemmeadresser har et DAWA-id eller en modtager. Afhentningssteder har ingen af delene."""
    return bool(adresse.get("dawa_uid") or adresse.get("recipient_name"))


def kan_leveres(hit: dict, adresse_blockbit: int) -> bool:
    return not (int(hit.get("blockbit") or 0) & int(adresse_blockbit or 0))


def vare_fra_hit(hit: dict, sid: str, adresse_blockbit: int = 0) -> Vare | None:
    """Ét Algolia-hit. Pris og lager står pr. butik under storeData[sid]. Top-feltet 'price'
    er normalprisen, storeData-prisen er det du betaler (tilbud medregnet)."""
    butik = (hit.get("storeData") or {}).get(sid)
    if not butik or not kan_leveres(hit, adresse_blockbit):
        return None
    nu, normal = kr(butik.get("price")), kr(hit.get("price"))
    tilbud = butik.get("offerDescription") or None
    foer = normal if normal > nu else None
    if tilbud == "Se Her":  # avistilbud uden egen tekst
        tilbud = f"Tilbud, før {foer:.2f} kr".replace(".", ",") if foer else "Tilbud"
    enh = (butik.get("unitsOfMeasurePriceUnit") or "").rstrip(".").lower() or None
    enhedspris = butik.get("unitsOfMeasureShowPrice") or butik.get("unitsOfMeasurePrice")
    return Vare(
        butik="bilka",
        id=str(hit["objectID"]),
        navn=hit.get("name", ""),
        pris=nu,
        maengde=hit.get("netcontent") or None,
        maerke=hit.get("brand") or None,
        enhedspris=kr(enhedspris) if enh and enhedspris else None,
        enhed={"ltr": "l"}.get(enh, enh),
        tilbud=tilbud,
        foer_pris=foer if tilbud else None,
        max_antal=(int(butik.get("offerMax") or 0) or None) if tilbud else None,
        paa_lager=bool(butik.get("inStock")),
        ean=hit.get("gtin") or None,
    )


def linjer(grupper: list[dict] | None) -> list[Kurvlinje]:
    """Kurv- og ordrelinjer ligger i lag: grupper → blokke → linjer."""
    return [
        Kurvlinje(
            str((ln.get("product") or {}).get("objectID", "")),
            (ln.get("product") or {}).get("name", ""),
            int(ln.get("quantity") or 0),
            kr(ln.get("amount")),
        )
        for g in grupper or []
        if g.get("headline") not in GEBYRGRUPPER
        for blok in g.get("lines") or []
        for ln in blok.get("orderlines") or []
    ]


def kurv_fra_svar(data: dict) -> Kurv:
    stat, adresse = data.get("stat") or {}, data.get("deliveryAddress") or {}
    ls = linjer(data.get("lines"))
    gebyrer = {
        navn: kr(stat[felt])
        for navn, felt in (("levering", "delivery_price"), ("pakkeservice", "packing"), ("emballage", "packaging_price"), ("pant", "deposit"))
        if stat.get(felt)
    }
    advarsler = [] if er_hjemlevering(adresse) else [IKKE_HJEMLEVERING]
    if stat.get("minimum_left_text"):
        advarsler.append(re.sub(r"<[^>]+>", "", stat["minimum_left_text"]))
    return Kurv(
        butik="bilka",
        linjer=ls,
        varer_i_alt=sum((ln.beloeb for ln in ls), Decimal(0)),
        gebyrer=gebyrer,
        i_alt=kr(stat.get("price")),
        minimumskoeb=kr(adresse["minimum_buy"]) if adresse.get("minimum_buy") else None,
        minimum_naaet=not stat.get("minimum_left"),
        levering=(data.get("deliveryDate") or {}).get("delivery_message") or None,
        advarsler=advarsler,
    )


def klokkeslaet(dag: str | None, start: str | None, slut: str | None) -> tuple[datetime, datetime] | None:
    try:
        d = date.fromisoformat(dag or "")
        a, b = (datetime.combine(d, time.fromisoformat(t), DK) for t in (start, slut))
    except (TypeError, ValueError):
        return None
    return a, (b if b > a else b + timedelta(days=1))  # natlevering går over midnat


class _Afvist(ButikFejl):
    """Bilka svarede 200 OK, men med en fejlbesked i svaret."""


class Bilka:
    navn = "bilka"

    def __init__(self) -> None:
        self._klient = ny_klient(timeout=30)
        self._jwt: str | None = None
        self._jwt_udloeber = datetime.min.replace(tzinfo=DK)
        self._laas = asyncio.Lock()

    async def _gigya(self, metode: str, felter: dict[str, str]) -> dict:
        svar = await send(self._klient, "POST", f"{GIGYA}/{metode}", data=felter)
        data = svar.json()
        if data.get("errorCode") == 403042:
            raise ButikFejl("BilkaToGo afviste login. Tjek email og kode.")
        if data.get("errorCode", 0) != 0:
            raise ButikFejl(f"BilkaToGo-login fejlede: {data.get('errorMessage', data['errorCode'])}")
        return data

    async def log_ind(self) -> None:
        email, kode = login.hent(self.navn)
        trin1 = await self._gigya("accounts.login", {"loginID": email, "password": kode, "apiKey": GIGYA_NOEGLE, "httpStatusCodes": "true"})
        token = (trin1.get("sessionInfo") or {}).get("cookieValue")
        trin2 = await self._gigya("accounts.getJWT", {"apiKey": GIGYA_NOEGLE, "login_token": token or "", "expiration": "86400"})
        jwt = trin2.get("id_token")
        if not token or not jwt:
            raise ButikFejl("BilkaToGo-login svarede uden token. API'et kan have ændret sig.")
        self._klient.cookies.delete("cart")  # se fælden øverst i filen
        svar = await send(self._klient, "POST", f"{API}/auth/LoginJWT", params={"u": "w"}, headers={"jwt_token": jwt})
        if svar.status_code != 200:
            raise ButikFejl(f"BilkaToGo-login fejlede ({svar.status_code}).")
        self._jwt = jwt
        self._jwt_udloeber = datetime.now(DK) + timedelta(hours=23, minutes=55)

    async def _shop(self, metode: str, sti: str, params: dict | None = None) -> dict:
        """Et kald som logget ind. Svaret {"code": 1, "msg": "Du er ikke logget ind"} betyder
        udløbet session: så logger vi ind igen og prøver én gang til."""
        for frisk in (False, True):
            async with self._laas:
                if frisk or not self._jwt or datetime.now(DK) >= self._jwt_udloeber:
                    await self.log_ind()
            svar = await send(self._klient, metode, f"{API}/{sti}", params={"u": "w", **(params or {})}, headers={"jwt_token": self._jwt or ""})
            if svar.status_code == 401:
                continue
            if svar.status_code >= 400:
                raise ButikFejl(f"BilkaToGo afviste {sti} ({svar.status_code}).")
            try:
                data = svar.json()
            except ValueError:
                raise ButikFejl("BilkaToGo svarede uventet. API'et kan have ændret sig.") from None
            if data.get("code") == 1:
                continue
            if "code" in data and "msg" in data:
                raise _Afvist(data["msg"])
            return data
        raise ButikFejl("BilkaToGo afviser login. Tjek email og kode.")

    async def _kurv_json(self) -> dict:
        return await self._shop("GET", "shop/v6/Cart", {"extra": "deliveryAddress,deliveryDate"})

    async def _algolia(self, tekst: str, antal: int, kun_tilbud: bool = False) -> list[Vare]:
        adresse = (await self._kurv_json()).get("deliveryAddress") or {}
        sid = str(adresse.get("sid") or "")
        if not sid:
            raise ButikFejl("Ingen leveringsadresse i BilkaToGo. Vælg hjemlevering på bilkatogo.dk.")
        filtre = f"isInAssortmentIn:{sid} AND nonsearchable:false"
        if kun_tilbud:
            filtre = f"isInOffer:{sid} AND {filtre}"
        params = urlencode(
            {
                "query": tekst,
                "hitsPerPage": antal,
                "removeWordsIfNoResults": "allOptional",  # færre ord, hvis alle ord giver 0 hits
                "filters": filtre,
                "attributesToRetrieve": json.dumps(["name", "brand", "gtin", "netcontent", "price", "blockbit", f"storeData.{sid}"]),
            }
        )
        svar = await send(self._klient, "POST", ALGOLIA, headers=ALGOLIA_HEADERS, json={"requests": [{"indexName": ALGOLIA_INDEKS, "params": params}]})
        if svar.status_code != 200:
            raise ButikFejl(f"BilkaToGo-søgning fejlede ({svar.status_code}).")
        try:
            hits = svar.json()["results"][0]["hits"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ButikFejl("BilkaToGos søgning har ændret format.") from None
        return [v for h in hits if (v := vare_fra_hit(h, sid, adresse.get("blockbit") or 0))]

    async def soeg(self, tekst: str, antal: int = 10) -> list[Vare]:
        return await self._algolia(tekst, antal)

    async def tilbud(self, antal: int = 25) -> list[Vare]:
        return await self._algolia("", antal, kun_tilbud=True)

    async def kurv(self) -> Kurv:
        return kurv_fra_svar(await self._kurv_json())

    async def saet_antal(self, antal: dict[str, int]) -> list[AntalResultat]:
        """Antallet er absolut. Bilka svarer 200 OK selv ved fejl, så vi sammenligner det
        antal, Bilka melder tilbage, med det ønskede."""
        resultater = []
        for vare_id, n in antal.items():
            try:
                svar = await self._shop("GET", "shop/v6/ChangeLineCount", {"productId": vare_id, "count": n, "fullCart": 0})
            except _Afvist as fejl:
                resultater.append(AntalResultat(vare_id, n, None, False, str(fejl)))
                continue
            faktisk = svar.get("productAmount")
            besked = " ".join(m for m in (svar.get("message"), svar.get("offerLimitMessage")) if m) or None
            ok = faktisk == n
            resultater.append(AntalResultat(vare_id, n, faktisk if isinstance(faktisk, int) else None, ok, besked or (None if ok else f"Bilka satte antallet til {faktisk}.")))
        return resultater + await self._hold_fjernede(antal)

    async def _hold_fjernede(self, antal: dict[str, int]) -> list[AntalResultat]:
        """Husk varer sat til 0, og fjern dem igen, hvis Bilka har lagt dem tilbage."""
        fjernet = {k: v for k, v in laes(FJERNET_FIL).items() if v > tid_nu() - FJERNET_HUSKES}
        for vare_id, n in antal.items():
            if n == 0:
                fjernet[str(vare_id)] = tid_nu()
            else:
                fjernet.pop(str(vare_id), None)
        try:
            skriv(FJERNET_FIL, fjernet)
        except OSError:
            pass
        if not fjernet:
            return []
        tilbage = [ln.id for ln in (await self.kurv()).linjer if ln.id in fjernet]
        ekstra = []
        for vare_id in tilbage:
            svar = await self._shop("GET", "shop/v6/ChangeLineCount", {"productId": vare_id, "count": 0, "fullCart": 0})
            ok = svar.get("productAmount") == 0
            ekstra.append(AntalResultat(vare_id, 0, svar.get("productAmount"), ok, "Bilka havde lagt den fjernede vare i kurven igen. Den er fjernet igen." if ok else "Bilka lagde den fjernede vare i kurven igen, og den kunne ikke fjernes. Fjern den i appen."))
        return ekstra

    async def leveringstider(self, dage: int = 7) -> list[Leveringstid]:
        """Bilka har ingen pris pr. tid: gebyret er fast og står i kurven."""
        data, kurv_data = await asyncio.gather(self._shop("GET", "shop/v3/AvailableDeliveryDates"), self._kurv_json())
        if not er_hjemlevering(kurv_data.get("deliveryAddress") or {}):
            raise ButikFejl(IKKE_HJEMLEVERING)
        gebyr = kurv_fra_svar(kurv_data).gebyrer.get("levering")
        sidste = datetime.now(DK).date() + timedelta(days=dage - 1)
        tider = []
        for dag in data.get("availableIntervals") or []:
            for iv in dag.get("intervals") or []:
                tt = klokkeslaet(dag.get("date"), iv.get("intervalStart"), iv.get("intervalEnd"))
                if not tt or tt[0].date() > sidste:
                    continue
                info = re.sub(r"<[^>]+>", "", " ".join(m.get("message", "") for m in iv.get("info") or [])).strip()
                tider.append(
                    Leveringstid(
                        butik="bilka",
                        start=tt[0],
                        slut=tt[1],
                        pris=gebyr,
                        ledig=not iv.get("closed") and bool(1 & (iv.get("availability_bit") or 0)),
                        type="Nat" if tt[0].hour < 6 else None,
                        note=info or None,
                    )
                )
        return tider

    async def ordrer(self, antal: int = 10) -> list[Ordre]:
        data = await self._shop("POST", "shop/v7/OrderHistory", {"limit": antal, "offset": 0, "muah": 1, "ordertype": "dibs,pgw"})
        return [
            Ordre("bilka", str(o.get("orderId", "")), o.get("status") or "ukendt", kr(o.get("packedPrice") or o.get("orderedPrice") or o.get("price")), (o.get("ordered") or {}).get("date"))
            for o in data.get("orders") or []
        ]

    async def ordre(self, ordre_id: str) -> Ordre:
        o = (await self._shop("GET", "shop/v7/OrderDetails", {"orderId": ordre_id})).get("order")
        if not o:
            raise ButikFejl(f"Fandt ikke ordre {ordre_id} hos BilkaToGo.")
        return Ordre("bilka", ordre_id, o.get("status") or "ukendt", kr(o.get("packedPrice") or o.get("orderedPrice") or o.get("price")), (o.get("ordered") or {}).get("date"), linjer(o.get("lines")))
