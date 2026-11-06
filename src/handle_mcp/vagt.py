"""Vagten: den eneste vej ud på nettet.

Hvert eneste HTTP-kald i handle-mcp går gennem en klient fra `ny_klient()`. Inden kaldet
sendes, tjekker vagten to ting:

1. At adressen hører til en af de få værter, vi kender (butikkerne og deres login/søgning).
   Alt andet afvises. Så kan dit login aldrig ende et ukendt sted, heller ikke ved en fejl.
2. At stien ikke indeholder ord for bestilling, betaling eller ændring af levering.
   handle-mcp må kun læse og ændre antal i kurven. Mennesket betaler altid selv.

Vagten er bevidst dum og streng: hellere et kald for meget afvist end et for lidt.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

TILLADTE_VAERTER = {
    "www.nemlig.com",  # nemlig: login, kurv, leveringstider, ordrer
    "webapi.prod.knl.nemlig.it",  # nemlig: søgning, tilbud, opskrifter
    "accounts.eu1.gigya.com",  # BilkaToGo: login (Salling Groups login-tjeneste)
    "api.bilkatogo.dk",  # BilkaToGo: kurv, leveringstider, ordrer
    "f9vbjlr1bk-dsn.algolia.net",  # BilkaToGo: produktsøgning
    "api.telegram.org",  # jeres egen Telegram-bot: ugeplan og OK (telegram.py)
    # opskriftssider, I stoler på (opskrifter_web.py). Kun læsning af offentlige opskrifter.
    "www.valdemarsro.dk", "valdemarsro.dk", "www.arla.dk", "arla.dk", "www.dr.dk", "dr.dk",
    "madensverden.dk", "www.madensverden.dk", "www.hellofresh.dk", "hellofresh.dk",
    "www.aarstiderne.com", "aarstiderne.com",
}  # fmt: skip

# Små bogstaver. Står et af ordene i stien, sendes kaldet aldrig.
FORBUDTE_ORD = (
    "placeorder",
    "checkout",
    "payment",
    "creditcard",
    "pay/",
    "setdeliveryaddress",
    "setdeliverydate",
    "updatedeliverytime",
    "tryupdatedeliverytime",
    "emptycart",
    "clearbasket",
    "deletebasket",
    "addrecipe",
)


class VagtFejl(Exception):
    """Et kald, vagten ikke vil sende."""


def tjek_url(url: httpx.URL) -> None:
    vaert = url.host
    if vaert == "queue-it.net" or vaert.endswith(".queue-it.net"):
        raise VagtFejl("Butikken har kø på hjemmesiden lige nu (queue-it). Prøv igen om lidt.")
    if vaert not in TILLADTE_VAERTER:
        raise VagtFejl(f"Afvist: {vaert} er ikke en kendt butiksadresse.")
    sti = url.path.lower()
    for ord_ in FORBUDTE_ORD:
        if ord_ in sti:
            raise VagtFejl(f"Afvist: handle-mcp må aldrig kalde '{ord_}'.")


async def _foer_kald(request: httpx.Request) -> None:
    tjek_url(request.url)


def ny_klient(timeout: float = 20, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """En HTTP-klient med vagten indbygget. Den følger også omdirigeringer, og vagten tjekker
    hver af dem, så en omdirigering til en fremmed adresse bliver også stoppet.
    `transport` bruges kun i testene til at lade som om, vi er butikken."""
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
        event_hooks={"request": [_foer_kald]},
        headers={"User-Agent": "Mozilla/5.0 (handle-mcp)"},
    )


GENTAG_STATUS = {429, 500, 502, 503, 504}


async def send(klient: httpx.AsyncClient, metode: str, url: str, **kw) -> httpx.Response:
    """Ét kald. Prøver op til 3 gange ved netværksfejl, 429 og 5xx, med pause imellem.
    Vær flink ved butikkerne: ingen hurtige gentagelser."""
    for forsoeg in range(3):
        try:
            svar = await klient.request(metode, url, **kw)
        except (httpx.TimeoutException, httpx.NetworkError):
            if forsoeg == 2:
                raise
        else:
            if svar.status_code not in GENTAG_STATUS or forsoeg == 2:
                return svar
        await asyncio.sleep(1.5 * (forsoeg + 1))
    raise AssertionError("nås ikke")
