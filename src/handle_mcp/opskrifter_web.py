"""Opskrifter fra de sider, I stoler på.

Claude finder retter med sin websøgning og henter dem her fra et link. Kun disse sider er
tilladt (også i vagt.py): Valdemarsro, Arla, DR Mad, Madens Verden, HelloFresh og Årstiderne.

Siderne viser opskrifter på fire måder, som prøves i rækkefølge:
  1. schema.org Recipe som JSON-LD (Madens Verden, Årstiderne, HelloFresh)
  2. microdata: itemprop="recipeIngredient" (Valdemarsro)
  3. opskrifts-JSON gemt i siden: "recipeIngredient": [...] (Arla)
  4. en almindelig liste under overskriften "Ingredienser" (DR Mad)
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

import httpx

from handle_mcp.vagt import VagtFejl, ny_klient, send

SIDER = {
    "www.valdemarsro.dk": "Valdemarsro",
    "valdemarsro.dk": "Valdemarsro",
    "www.arla.dk": "Arla",
    "arla.dk": "Arla",
    "www.dr.dk": "DR Mad",
    "dr.dk": "DR Mad",
    "madensverden.dk": "Madens Verden",
    "www.madensverden.dk": "Madens Verden",
    "www.hellofresh.dk": "HelloFresh",
    "hellofresh.dk": "HelloFresh",
    "www.aarstiderne.com": "Årstiderne",
    "aarstiderne.com": "Årstiderne",
}
MAKS_INGREDIENSER = 60
_transport: httpx.AsyncBaseTransport | None = None  # kun til test


class OpskriftFejl(Exception):
    pass


@dataclass
class WebOpskrift:
    navn: str
    kilde: str
    link: str
    personer: int | None
    tid_minutter: int | None
    ingredienser: list[str] = field(default_factory=list)


def _tekst(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _find_recipe(j: object) -> dict | None:
    if isinstance(j, list):
        for x in j:
            if r := _find_recipe(x):
                return r
    if isinstance(j, dict):
        t = j.get("@type") or j.get("type")
        if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
            return j
        for noegle in ("@graph", "mainEntity"):
            if noegle in j and (r := _find_recipe(j[noegle])):
                return r
    return None


def _personer(yield_: object) -> int | None:
    if isinstance(yield_, list):
        yield_ = " ".join(map(str, yield_))
    m = re.search(r"\d+", str(yield_ or ""))
    return int(m.group()) if m and 0 < int(m.group()) <= 50 else None


def _minutter(iso: object) -> int | None:
    """"PT1H30M" → 90."""
    m = re.fullmatch(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?", str(iso or "").replace("0D", "").replace("P0DT", "PT"))
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def _navn(side: str) -> str:
    for moenster in (r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', r"<title>(.*?)</title>"):
        if m := re.search(moenster, side, re.S | re.I):
            return _tekst(m.group(1)).split(" | ")[0].split(" - Opskrift")[0]
    return "Opskrift"


def laes_side(side: str, link: str, kilde: str) -> WebOpskrift:
    """Find opskriften i sidens HTML (de fire måder, se øverst)."""
    # 1. JSON-LD
    for blok in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", side, re.S | re.I):
        try:
            r = _find_recipe(json.loads(blok))
        except ValueError:
            continue
        if r and r.get("recipeIngredient"):
            return _byg(r.get("name"), kilde, link, r.get("recipeYield"), r.get("totalTime"), r["recipeIngredient"], side)
    # 2. microdata
    if fund := re.findall(r'<[^>]+itemprop="recipeIngredient"[^>]*>(.*?)</(?:li|span|div|p)>', side, re.S | re.I):
        yield_ = re.search(r'itemprop="recipeYield"[^>]*>(.*?)<', side, re.S)
        tid = re.search(r'itemprop="totalTime"[^>]*content="([^"]+)"', side)
        return _byg(None, kilde, link, yield_.group(1) if yield_ else None, tid.group(1) if tid else None, fund, side)
    # 3. opskrifts-JSON i siden
    if m := re.search(r'"recipeIngredient"\s*:\s*(\[[^\]]*\])', side):
        try:
            ingr = json.loads(m.group(1))
        except ValueError:
            ingr = []
        if ingr:
            yield_ = re.search(r'"recipeYield"\s*:\s*("[^"]*"|\d+|\[[^\]]*\])', side)
            tid = re.search(r'"totalTime"\s*:\s*"([^"]+)"', side)
            return _byg(None, kilde, link, json.loads(yield_.group(1)) if yield_ else None, tid.group(1) if tid else None, ingr, side)
    # 4. under en overskrift med "Ingredienser": en liste (DR Mad) eller et afsnit med én
    #    ingrediens pr. linje (WordPress-blogs, fx Madens Verdens "Ingredienser til dyreryg")
    m = re.search(r">\s*Ingredienser\s*<", side) or re.search(r"<h[1-6][^>]*>(?:(?!</h[1-6]>).)*Ingredienser(?:(?!</h[1-6]>).)*</h[1-6]>", side, re.S | re.I)
    if m:
        rest = side[m.end():]
        slut = re.search(r">\s*(Fremgangsmåde|Fremgangsmaade|Sådan gør du|Tilberedning|Anretning|Kommentarer)[^<]*<", rest)
        afsnit = rest[: slut.start()] if slut else rest[:20000]
        ingr = [_tekst(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", afsnit, re.S)]
        if not ingr:  # afsnit med linjeskift i stedet for en liste
            for p in re.findall(r"<p[^>]*>(.*?)</p>", afsnit, re.S):
                ingr += [_tekst(x) for x in re.split(r"<br\s*/?>", p) if _tekst(x)]
        if ingr:
            personer = re.search(r"(\d+)\s*(?:personer|pers\.|portioner)", _tekst(side[: m.start()]))
            return _byg(None, kilde, link, personer.group(1) if personer else None, None, ingr, side)
    raise OpskriftFejl("Fandt ingen ingrediensliste på siden. Er det et link til en enkelt opskrift?")


def _byg(navn, kilde, link, yield_, tid, ingredienser, side) -> WebOpskrift:
    rene = []
    for x in ingredienser:
        t = _tekst(str(x)).lstrip("•·- ").strip()
        if t and t not in rene:
            rene.append(t[:150])
    if not rene:
        raise OpskriftFejl("Ingredienslisten var tom.")
    return WebOpskrift(
        navn=_tekst(str(navn)) if navn else _navn(side),
        kilde=kilde,
        link=link,
        personer=_personer(yield_),
        tid_minutter=_minutter(tid),
        ingredienser=rene[:MAKS_INGREDIENSER],
    )


async def hent(link: str) -> WebOpskrift:
    try:
        url = httpx.URL(link.strip())
    except (httpx.InvalidURL, TypeError):
        raise OpskriftFejl("Det er ikke et gyldigt link.") from None
    if url.scheme != "https" or url.host not in SIDER:
        tilladte = ", ".join(sorted(set(SIDER.values())))
        raise OpskriftFejl(f"Opskrifter hentes kun fra jeres sider: {tilladte}.")
    try:
        async with ny_klient(timeout=20, transport=_transport) as klient:
            svar = await send(klient, "GET", str(url))
    except (httpx.HTTPError, VagtFejl) as fejl:
        raise OpskriftFejl(f"Kunne ikke hente opskriften: {fejl}") from None
    if svar.status_code != 200:
        raise OpskriftFejl(f"Siden svarede {svar.status_code}. Findes opskriften stadig?")
    return laes_side(svar.text, str(url), SIDER[url.host])
