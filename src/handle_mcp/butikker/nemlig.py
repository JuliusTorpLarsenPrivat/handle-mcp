"""nemlig.com via det API, hjemmesiden selv bruger.

Det er ikke et officielt API, så det kan ændre sig uden varsel. Beløb er i kroner, undtagen i
det nyere "productbff"-API (tilbud og opskrifter), hvor de er i øre.

Login sker i tre trin:
  1. GET /webapi/Token        → et kortlivet adgangstoken (5 min)
  2. GET /webapi/AntiForgery  → et XSRF-token
  3. POST /webapi/login       → en login-cookie, der holder længe
Udløber tokenet (svar 401), henter vi et nyt. Virker det ikke, logger vi ind igen.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from handle_mcp import login
from handle_mcp.butikker import ButikFejl
from handle_mcp.data import (
    AntalResultat,
    Kurv,
    Kurvlinje,
    Leveringstid,
    Opskrift,
    OpskriftDetaljer,
    OpskriftVare,
    Ordre,
    Vare,
)
from handle_mcp.vagt import ny_klient, send

SIDE = "https://www.nemlig.com"
SOEG = "https://webapi.prod.knl.nemlig.it/searchgateway/api/search"
BFF = "https://webapi.prod.knl.nemlig.it/productbff/api/web"
APP_VERSION = os.environ.get("KURV_NEMLIG_VERSION", "11.201.0")
DK = ZoneInfo("Europe/Copenhagen")

# Overskrifterne på nemlig.com/tilbud, der er rigtige tilbudslister (resten er kategorier).
TILBUDSLISTER = {"skarp pris", "ugens priskup", "priskup", "dagstilbud", "undgå madspild"}
LEVERINGSTYPE = {0: "Personlig", 1: "Fleksibel"}
ENHEDER = {"kg": "kg", "l": "l", "ltr": "l", "stk": "stk"}


class _Udloebet(Exception):
    """HTTP 401: token eller login er udløbet."""


def kr(vaerdi: Any) -> Decimal | None:
    return None if vaerdi is None else Decimal(str(vaerdi))


def oere(vaerdi: Any) -> Decimal | None:
    return None if vaerdi is None else Decimal(int(vaerdi)) / 100


def tekst_kr(v: Decimal) -> str:
    return f"{v:.2f}".replace(".", ",").removesuffix(",00")


def enhed(label: str | None) -> str | None:
    """'kr./Kg.' → 'kg', 'kr/l' → 'l'."""
    if not label:
        return None
    sidste = label.lower().replace(".", "").rsplit("/", 1)[-1].strip()
    return ENHEDER.get(sidste, sidste or None)


def opskrift_id(tekst: str) -> str:
    """Et link som https://www.nemlig.com/maaltider/opskrift/kebabfad-98001303 → kebabfad-98001303."""
    slug = tekst.strip().split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise ButikFejl(f"Kender ikke opskriften {tekst!r}. Brug linket fra nemlig.com.")
    return slug


def vare_fra_soegning(hit: dict) -> Vare:
    """Oversæt ét søgeresultat. Kampagnetyper, set på nemlig.com:
    - Rabat (fx 'ProductCampaignDiscountPercent'): Price er FØR-prisen, CampaignPrice er nu-prisen.
    - Mængdetilbud (MinQuantity > 1, fx '3 for 50'): Price er stykprisen.
    - Gratis vare ('ProductCampaignFreeProduct'): koster 0.
    - 'DiscountItem' uden kampagne er bare nemligs faste Discount-mærke, ikke et tilbud."""
    normal = kr(hit.get("Price")) or Decimal(0)
    kampagne = hit.get("Campaign") or {}
    nu, tilbud, foer = normal, None, None
    min_antal = kampagne.get("MinQuantity") or 0
    if kampagne.get("Type") == "ProductCampaignFreeProduct":
        nu, tilbud, foer = Decimal(0), "Gratis", normal
    elif min_antal > 1 and kampagne.get("TotalPrice"):
        tilbud = f"{min_antal} for {tekst_kr(kr(kampagne['TotalPrice']))} kr"
    elif (kp := kr(kampagne.get("CampaignPrice"))) and kp < normal:
        nu, foer = kp, normal
        pct = kampagne.get("DiscountPercent")
        tilbud = f"-{pct} %" if pct else f"Tilbud, før {tekst_kr(normal)} kr"
    max_antal = kampagne.get("MaxQuantity") if tilbud else None
    lager = hit.get("Availability") or {}
    enhedspris = kr(kampagne.get("CampaignUnitPrice")) if foer else kr(hit.get("UnitPriceCalc"))
    return Vare(
        butik="nemlig",
        id=str(hit["Id"]),
        navn=hit.get("Name", ""),
        pris=nu,
        maengde=hit.get("Description") or None,
        maerke=hit.get("Brand") or None,
        enhedspris=enhedspris,
        enhed=enhed(hit.get("UnitPriceLabel")),
        tilbud=tilbud,
        foer_pris=foer,
        max_antal=max_antal or None,
        paa_lager=lager.get("IsDeliveryAvailable", True) and lager.get("IsAvailableInStock", True),
    )


def vare_fra_tilbudsliste(p: dict, liste: str) -> Vare | None:
    """En vare fra en tilbudsliste (productbff, beløb i øre). None hvis den ikke er billigere."""
    pris, foer = oere(p.get("price")), oere(p.get("priceOriginal"))
    if pris is None or (foer is not None and pris >= foer):
        return None
    mix = next(
        (
            ln["text"]
            for ln in p.get("campaignLines") or []
            if re.match(r"^(Mix )?\d+ stk\.", ln.get("text") or "")
        ),
        None,
    )
    tekst = "Gratis" if pris == 0 else mix or (f"{liste}, før {tekst_kr(foer)} kr" if foer else liste)
    pr_enhed = p.get("pricePerUnit") or {}
    return Vare(
        butik="nemlig",
        id=str(p["id"]),
        navn=p.get("title", ""),
        pris=pris,
        maengde=p.get("description") or None,
        enhedspris=oere(pr_enhed.get("value")),
        enhed={"Weight": "kg", "Volume": "l", "Pieces": "stk"}.get(pr_enhed.get("unitType")),
        tilbud=tekst,
        foer_pris=foer,
        max_antal=p.get("labelMaxQuantity") or None,
        paa_lager=(p.get("availability") or {}).get("type", "Available") == "Available",
    )


def kurv_fra_svar(b: dict) -> Kurv:
    linjer = [
        Kurvlinje(str(ln["Id"]), ln.get("Name", ""), int(ln["Quantity"]), kr(ln.get("Price")) or Decimal(0))
        for ln in b.get("Lines") or []
        if int(ln.get("Quantity") or 0) > 0  # fjernede linjer kan blive liggende med antal 0
    ]
    varer = kr(b.get("TotalProductsPrice"))
    if varer is None:
        varer = sum((ln.beloeb for ln in linjer), Decimal(0))
    gebyrer = {
        navn: kr(b[felt])
        for navn, felt in (("levering", "DeliveryPrice"), ("poser", "TotalBagsPrice"), ("pant", "TotalDepositsPrice"))
        if b.get(felt)
    }
    return Kurv(
        butik="nemlig",
        linjer=linjer,
        varer_i_alt=varer,
        gebyrer=gebyrer,
        i_alt=kr(b.get("TotalPrice")),
        minimumskoeb=kr(b.get("MinimumOrderTotal")),
        minimum_naaet=b.get("IsMinTotalValid"),
        levering=b.get("FormattedDeliveryTime") or None,
        advarsler=[
            f.get("Message", str(f)) if isinstance(f, dict) else str(f)
            for f in b.get("ValidationFailures") or []
        ],
    )


def tid(tekst: str | None) -> datetime | None:
    if not tekst or tekst.startswith(("0001-", "9999-")):
        return None
    try:
        return datetime.fromisoformat(tekst)
    except ValueError:
        return None


def leveringstider_fra_svar(data: dict) -> list[Leveringstid]:
    tider = []
    for dag in data.get("DayRangeHours") or []:
        for t in dag.get("DayHours") or []:
            d = tid(t.get("Date"))
            if d is None or t.get("StartHour") is None or t.get("EndHour") is None:
                continue
            d = d.replace(tzinfo=DK)
            frist = tid(t.get("Deadline"))
            tider.append(
                Leveringstid(
                    butik="nemlig",
                    start=d + timedelta(hours=t["StartHour"]),
                    slut=d + timedelta(hours=t["EndHour"]),
                    pris=kr(t.get("DeliveryPrice")),
                    ledig=t.get("Availability") == 0,  # 1 efter frist, 2 udsolgt, 3 ikke aktiv
                    type=LEVERINGSTYPE.get(t.get("Type")),
                    note=f"Bestil senest {frist:%d/%m kl. %H:%M}" if frist else None,
                )
            )
    return tider


class Nemlig:
    navn = "nemlig"

    def __init__(self) -> None:
        self._klient = ny_klient()
        self._token: str | None = None
        self._xsrf: str | None = None
        self._logget_ind = False
        self._laas = asyncio.Lock()
        self._indstillinger: tuple[float, dict] | None = None

    # --- HTTP og login ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "Platform": "web",
            "Device-Size": "desktop",
            "Version": APP_VERSION,
            "X-Correlation-Id": str(uuid.uuid4()),
            "Referer": SIDE + "/",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._xsrf:
            h["X-XSRF-TOKEN"] = self._xsrf
        return h

    async def _raa(self, metode: str, url: str, **kw: Any) -> Any:
        svar = await send(self._klient, metode, url, headers=self._headers(), **kw)
        if svar.status_code == 401:
            raise _Udloebet
        if svar.status_code >= 400:
            raise ButikFejl(f"nemlig.com svarede {svar.status_code}.")
        if not svar.content:
            return None
        try:
            return svar.json()
        except ValueError:
            raise ButikFejl("nemlig.com svarede ikke med JSON. API'et kan have ændret sig.") from None

    async def _nye_tokens(self) -> None:
        self._token = self._xsrf = None
        self._token = (await self._raa("GET", f"{SIDE}/webapi/Token"))["access_token"]
        self._xsrf = (await self._raa("GET", f"{SIDE}/webapi/AntiForgery"))["Value"]

    async def log_ind(self) -> None:
        email, kode = login.hent(self.navn)
        try:
            await self._nye_tokens()
            svar = await self._raa(
                "POST",
                f"{SIDE}/webapi/login",
                json={
                    "Username": email,
                    "Password": kode,
                    "CheckForExistingProducts": True,
                    "DoMerge": True,
                    "AppInstalled": False,
                    "SaveExistingBasket": False,
                },
            )
        except _Udloebet:
            svar = None
        if not isinstance(svar, dict) or "RedirectUrl" not in svar:
            raise ButikFejl("nemlig.com afviste login. Tjek email og kode.")
        await self._nye_tokens()  # tokens skifter efter login
        self._logget_ind = True

    async def _kald(self, metode: str, url: str, **kw: Any) -> Any:
        """Et kald som logget ind. Ved 401: nyt token, og ellers nyt login."""
        async with self._laas:
            if not self._logget_ind:
                await self.log_ind()
        for redning in (self._nye_tokens, self.log_ind):
            try:
                return await self._raa(metode, url, **kw)
            except _Udloebet:
                await redning()
        try:
            return await self._raa(metode, url, **kw)
        except _Udloebet:
            raise ButikFejl("nemlig.com afviser login. Tjek email og kode.") from None

    async def _rammer(self) -> dict:
        """Søgning og priser afhænger af leveringszone og valgt tid. Gemmes i 10 minutter."""
        if self._indstillinger and time.monotonic() - self._indstillinger[0] < 600:
            return self._indstillinger[1]
        app = await self._kald("GET", f"{SIDE}/webapi/v2/AppSettings/Website") or {}
        b = await self._kald("GET", f"{SIDE}/webapi/basket/GetBasket") or {}
        rammer = {
            "timestamp": app.get("CombinedProductsAndSitecoreTimestamp", ""),
            "timeslotUtc": b.get("TimeslotUtc", ""),
            "deliveryZoneId": b.get("DeliveryZoneId") or 1,
            "slot_id": str((b.get("DeliveryTimeSlot") or {}).get("Id") or ""),
        }
        self._indstillinger = (time.monotonic(), rammer)
        return rammer

    # --- Butikkens metoder -------------------------------------------------

    async def soeg(self, tekst: str, antal: int = 10) -> list[Vare]:
        r = await self._rammer()
        params = {"query": tekst, "take": antal, "skip": 0, "recipeCount": 0}
        params |= {k: r[k] for k in ("timestamp", "timeslotUtc", "deliveryZoneId")}
        data = await self._kald("GET", SOEG, params=params)
        try:
            hits = data["Products"]["Products"]
        except (KeyError, TypeError):
            raise ButikFejl("nemligs søgning har ændret format.") from None
        return [vare_fra_soegning(h) for h in hits]

    async def tilbud(self, antal: int = 25) -> list[Vare]:
        params = {"path": "/tilbud"}
        if slot := (await self._rammer())["slot_id"]:
            params["timeslotId"] = slot
        side = await self._kald("GET", f"{BFF}/page", params=params) or {}
        fundet: dict[str, Vare] = {}
        for blok in side.get("pageContent") or []:
            overskrift = str((blok.get("header") or {}).get("title") or "").strip()
            if overskrift.lower() not in TILBUDSLISTER:
                continue
            varer = [v for p in blok.get("products") or [] if (v := vare_fra_tilbudsliste(p, overskrift))]
            for v in varer[:antal]:
                fundet.setdefault(v.id, v)
        if not fundet:
            raise ButikFejl("Fandt ingen tilbudslister hos nemlig. Siden kan have ændret sig.")
        return list(fundet.values())

    async def kurv(self) -> Kurv:
        return kurv_fra_svar(await self._kald("GET", f"{SIDE}/webapi/basket/GetBasket") or {})

    async def saet_antal(self, antal: dict[str, int]) -> list[AntalResultat]:
        """Antallet er absolut: 3 betyder 3 i kurven, 0 fjerner varen."""
        resultater = []
        for vare_id, n in antal.items():
            try:
                b = await self._kald(
                    "POST",
                    f"{SIDE}/webapi/basket/AddToBasket",
                    json={"ProductId": vare_id, "quantity": n, "AffectPartialQuantity": False, "disableQuantityValidation": False},
                ) or {}
            except ButikFejl as fejl:
                resultater.append(AntalResultat(vare_id, n, None, False, str(fejl)))
                continue
            faktisk = next((int(ln.get("Quantity", 0)) for ln in b.get("Lines") or [] if str(ln.get("Id")) == vare_id), 0)
            ok = faktisk == n
            resultater.append(AntalResultat(vare_id, n, faktisk, ok, None if ok else f"nemlig satte antallet til {faktisk}."))
        return resultater

    async def leveringstider(self, dage: int = 7) -> list[Leveringstid]:
        """Kun læsning. Tiden vælger du selv på nemlig.com."""
        data = await self._kald(
            "GET", f"{SIDE}/webapi/v2/Delivery/GetDeliveryDays", params={"days": dage, "showForSubscriptions": "false"}
        )
        return leveringstider_fra_svar(data or {})

    async def ordrer(self, antal: int = 10) -> list[Ordre]:
        data = await self._kald("GET", f"{SIDE}/webapi/order/GetBasicOrderHistory", params={"skip": 0, "take": antal})
        return [
            Ordre("nemlig", str(o.get("Id", "")), str(o.get("Status", "")), kr(o.get("Total")), o.get("OrderDate"))
            for o in (data or {}).get("Orders") or []
        ]

    async def ordre(self, ordre_id: str) -> Ordre:
        data = await self._kald("GET", f"{SIDE}/webapi/v2/order/GetOrderHistory/{ordre_id}") or {}
        linjer = [
            Kurvlinje(str(ln.get("ProductNumber", "")), ln.get("ProductName", ""), int(ln.get("Quantity", 0)), kr(ln.get("Amount")) or Decimal(0))
            for ln in data.get("Lines") or []
        ]
        if not linjer:
            raise ButikFejl(f"Fandt ikke ordre {ordre_id} hos nemlig.")
        return Ordre("nemlig", ordre_id, str(data.get("Status", "")), sum((ln.beloeb for ln in linjer), Decimal(0)), data.get("OrderDate"), linjer)

    # --- Opskrifter ----------------------------------------------------------

    async def soeg_opskrifter(self, tekst: str, antal: int = 5) -> list[Opskrift]:
        data = await self._kald("GET", f"{BFF}/search/recipes", params={"query": tekst, "take": antal, "skip": 0}) or {}
        hits = (data.get("searchResult") or {}).get("items") or []
        return [Opskrift("nemlig", h["id"], h.get("title", ""), f"{SIDE}/maaltider/opskrift/{h['id']}") for h in hits[:antal] if h.get("id")]

    async def opskrift(self, opskrift: str, personer: int | None = None) -> OpskriftDetaljer:
        """Varerne bag knappen 'Se varer og læg i kurv' på nemlig.com."""
        slug = opskrift_id(opskrift)
        params: dict[str, Any] = {"recipeId": slug, "maxProductsPerIngredient": 1}
        if personer:
            params["servings"] = personer
        detaljer, varer = await asyncio.gather(
            self._kald("GET", f"{BFF}/meals/recipeDetails/{slug}"),
            self._kald("GET", f"{BFF}/meals/recipeProductDialog", params=params),
        )
        detaljer, varer = detaljer or {}, varer or {}
        if "includedProducts" not in varer:
            raise ButikFejl("nemligs opskrifter har ændret format.")
        liste = []
        for raekker, hjemme in ((varer.get("includedProducts") or [], False), (varer.get("selfSuppliedProducts") or [], True)):
            for r in raekker:
                p = (r.get("item") or {}).get("product") or {}
                if p.get("id"):
                    liste.append(
                        OpskriftVare(
                            ingrediens=r.get("ingredientTitle") or "",
                            vare_id=str(p["id"]),
                            navn=p.get("title", ""),
                            antal=int((r.get("item") or {}).get("quantity") or 1),
                            pris=oere(p.get("price")) or Decimal(0),
                            har_hjemme=hjemme,
                        )
                    )
        minutter = (detaljer.get("durationTotal") or {}).get("from")
        return OpskriftDetaljer(
            butik="nemlig",
            id=slug,
            navn=varer.get("title") or detaljer.get("title", ""),
            link=f"{SIDE}/maaltider/opskrift/{slug}",
            personer=varer.get("servings") or detaljer.get("servings") or personer,
            tid=f"{minutter} min" if minutter else None,
            varer=liste,
        )
