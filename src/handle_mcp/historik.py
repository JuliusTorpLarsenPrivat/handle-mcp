"""Købshistorik: hvad I faktisk køber, hvor ofte, og hvad det koster.

Læser kvitteringer, der er gemt lokalt (i dag: coop_kvitteringer.json, hentet fra
medlem.coop.dk). Ud fra dem findes:
  - basisvarer: varer I køber igen og igen, med typisk interval og dage siden sidst
  - kategorier: hvad pengene går til (kød, fisk, mejeri, grønt, snacks …)
  - favoritter: de varer I oftest køber inden for hver madkategori
  - budget: forbrug pr. uge, og et skøn over det samlede forbrug, hvis kvitteringerne
    kun er en del af jeres indkøb (fx 70 % i Brugsen og resten i Netto)

Alt sker lokalt. Filen indeholder ingen kort- eller medlemsnumre.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import date, datetime

from handle_mcp.lokal import LokalFejl, laes

KILDER = ("coop_kvitteringer",)

# Rækkefølgen betyder noget: første kategori, der passer, vinder ("FLÆSKESVÆR" er snacks,
# ikke kød, og "KOLDSKÅL" er mejeri, ikke kål). Ord matcher i starten af et ord i varenavnet.
KATEGORIER: list[tuple[str, tuple[str, ...]]] = [
    ("pant", ("PANT",)),
    ("ikke mad", ("TOILETPAPIR", "OPVASK", "VASKEMIDDEL", "SHAMPOO", "SÆBE", "KØKKENRULLE", "POSER", "BLE", "TANDPASTA", "TOILETRENS", "RENGØRING", "SKYLLEMIDDEL", "BATTERI", "STEARIN", "FOLIE", "BAGEPAPIR")),
    ("snacks og søde sager", ("CHOKOLADE", "CHIPS", "SLIK", "KAGE", "IS", "MARCIPAN", "BAGEMARCIPAN", "KIKS", "SKIPPER", "FLÆSKESVÆR", "ØFFER", "LAKRIDS", "VINGUMMI", "POPCORN", "SNACK")),
    ("drikkevarer", ("APOLLINARIS", "VAND", "JUICE", "ØL", "VIN", "SODAVAND", "KAFFE", "TE", "COLA", "SAFT", "KOMBUCHA", "LEMONADE", "LEMONADE")),
    ("fisk", ("LAKS", "TORSK", "REJER", "TUN", "MAKREL", "SILD", "FISK", "KULLER", "SEJ", "RØDSPÆTTE", "MUSLING")),
    ("kød", ("KYLLING", "OKSE", "HAKKET", "ENTRECOTE", "FLÆSK", "STEGEFLÆSK", "BACON", "PØLSE", "SKINKE", "LAM", "KALKUN", "MØRBRAD", "KOTELET", "STEAK", "BØF", "FARS", "SVINE", "GRISE", "KALV", "VILDT", "HJORT", "ANDEBRYST", "SALAMI", "PEPPERONI")),
    ("mejeri og æg", ("MÆLK", "GRÆSMÆLK", "YOGHURT", "SKYR", "OST", "FETA", "SMØR", "FLØDE", "PISKEFLØDE", "CREME", "KOLDSKÅL", "ÆG", "FRILANDSÆG", "CHEDDAR", "MOZZARELLA", "PARMESAN", "HYTTEOST", "KVARK", "RYGEOST", "BURRATA", "HALLOUMI")),
    ("frugt og grønt", ("TOMAT", "TOMATER", "CHERRYTOMAT", "AGURK", "AVOCADO", "PEBERFRUGT", "PEBERFRUGTER", "KARTOFLER", "LØG", "RØDLØG", "CITRON", "CITRONER", "LIME", "BANAN", "BANANER", "ÆBLE", "ÆBLER", "SALAT", "ROMAINE", "MINI", "SPIDSKÅL", "KÅL", "GULERØD", "GULERØDDER", "BROCCOLI", "SQUASH", "SPINAT", "PURLØG", "PERSILLE", "ESTRAGON", "BASILIKUM", "KORIANDER", "DILD", "ÆRTESKUD", "BÆR", "JORDBÆR", "BLÅBÆR", "HINDBÆR", "PÆRE", "PÆRER", "HVIDLØG", "INGEFÆR", "SVAMPE", "CHAMPIGNON", "SELLERI", "PORRE", "PORRER", "ASPARGES", "BLOMKÅL", "GRØNKÅL", "RÆDIS", "FENNIKEL", "MANGO", "APPELSIN", "MELON", "VINDRUER", "DRUER", "KIWI", "OLIVEN", "SOLTØRRET", "CHILI", "ROSENKÅL", "BØNNER", "ÆRTER", "MAJS")),
    ("kolonial", ("RUGBRØD", "BRØD", "BOLLER", "MEL", "HVEDEMEL", "PASTA", "SPAGHETTI", "RIS", "LINSER", "HAVREGRYN", "MÜSLI", "MUSLI", "GRANOLA", "OLIE", "OLIVENOLIE", "SENNEP", "TORTILLA", "NUDLER", "KIDNEY", "KIKÆRTER", "BULGUR", "QUINOA", "SUKKER", "SALT", "PEBER", "KRYDDERI", "SPIDSKOMMEN", "BOUILLON", "KETCHUP", "MAYONNAISE", "PESTO", "HONNING", "MARMELADE", "NØDDER", "MANDLER", "GRÆSKARKERNER", "SOLSIKKEKERNER", "KOKOSMÆLK", "PASSATA", "EDDIKE", "SOJA", "TAHIN")),
]
MADKATEGORIER = ("kød", "fisk", "mejeri og æg", "frugt og grønt", "kolonial")


def _ord(navn: str) -> list[str]:
    return re.findall(r"[A-ZÆØÅÉÜ]+", navn.upper())


# Flere ord fra Brugsens kvitteringer (forkortelser og sammensatte ord), fundet i jeres data
EKSTRA = {
    "ikke mad": ("COLOR", "POWER", "TABS", "MULEPOSE", "LOTUS", "PLASTER"),
    "snacks og søde sager": ("SUNCHINE", "CANDY", "DESSERT", "TYRKISK"),
    "drikkevarer": ("KONDI", "FAXE", "GRANT", "CHARDONNAY"),
    "fisk": ("ROGN",),
    "kød": ("HAKKEBØF", "CHORIZO", "KOTELET", "CULOTTE", "RIB", "LÅRFILET", "SERRANO", "POSTEJ", "SPEGEPØLSE", "HØNSESALAT", "TYKSTEG", "CUVETTE", "GRILLPØLSE"),
    "mejeri og æg": ("YOG", "YOGH", "GRANA", "PARMIGIANO", "VESTERHAV", "HAVARTI", "BURRAT", "SØDMÆLK", "PROTEIN", "GEDE", "MASCARPONE", "RICOTTA", "SKRABEÆG"),
    "frugt og grønt": ("RUCOLA", "HOKKAIDO", "FERSKNER", "COURGETTE", "CHAMPIG", "DADLER", "TIMIAN", "BLADSELLERI", "NEKTARIN", "BLOMMER", "SPIRER"),
    "kolonial": ("PEANUT", "SIRUP", "KAKAO", "RISPAPIR", "TACO", "BBQ", "SAUCE", "TERIYAKI", "RIGATONI", "TORTELLONI", "FALKSALT", "HAVSSALT", "PENNE", "FUSILLI", "LASAGNE", "BOOST"),
}
KATEGORIER = [(kat, noegler + EKSTRA.get(kat, ())) for kat, noegler in KATEGORIER]


def _passer(o: str, n: str) -> bool:
    """Korte nøgler (IS, TE, ØL) skal være hele ord, 4 bogstaver skal starte et ord,
    og lange nøgler må stå inde i et sammensat ord (NAKKEKOTELET, BLADSELLERI)."""
    if len(n) <= 3:
        return o == n
    if len(n) == 4:
        return o.startswith(n)
    return n in o


def kategori(navn: str) -> str:
    ord_ = _ord(navn)
    for kat, noegler in KATEGORIER:
        if any(_passer(o, n) for n in noegler for o in ord_):
            return kat
    return "andet"


def varenavn(navn: str) -> str:
    """Samme vare, selv om der står "2 x" foran."""
    return re.sub(r"^\d+\s*x\s+", "", navn.strip(), flags=re.I).upper()


def kvitteringer() -> list[dict]:
    alle = []
    for kilde in KILDER:
        alle += laes(kilde).get("kvitteringer", [])
    if not alle:
        raise LokalFejl("Der er ingen kvitteringer gemt endnu.")
    return alle


def _dato(k: dict) -> date:
    return datetime.fromisoformat(k["tid"].replace("Z", "+00:00")).date()


def moenster(andel_af_indkoeb: float = 1.0, i_dag: date | None = None, top: int = 25) -> dict:
    """Købsmønstret. `andel_af_indkoeb` er hvor stor en del af jeres indkøb kvitteringerne
    dækker (0,7 hvis 70 % købes i Brugsen og resten i Netto)."""
    if not 0.05 <= andel_af_indkoeb <= 1:
        raise LokalFejl("andel_af_indkoeb skal være mellem 0,05 og 1.")
    kv = kvitteringer()
    i_dag = i_dag or date.today()
    datoer = sorted(_dato(k) for k in kv)
    uger = max((datoer[-1] - datoer[0]).days, 7) / 7

    pr_vare: dict[str, dict] = defaultdict(lambda: {"datoer": set(), "kr": 0.0, "linjer": 0})
    pr_kat: dict[str, float] = defaultdict(float)
    oeko = linjer = 0
    for k in kv:
        d = _dato(k)
        for v in k["varer"]:
            navn = varenavn(v["navn"])
            kr = v["pris"] + v.get("rabat", 0)
            kat = kategori(navn)
            pr_kat[kat] += kr
            if kat == "pant":
                continue
            p = pr_vare[navn]
            p["datoer"].add(d)
            p["kr"] += kr
            p["linjer"] += 1
            if kat != "ikke mad":  # øko-andelen gælder kun mad
                linjer += 1
                oeko += "ØKO" in _ord(navn)

    basis = []
    for navn, p in pr_vare.items():
        ds = sorted(p["datoer"])
        if len(ds) < 3:
            continue
        interval = statistics.median((b - a).days for a, b in zip(ds, ds[1:]))
        siden = (i_dag - ds[-1]).days
        aktiv = siden <= 3 * max(interval, 7)  # ellers en sæsonvare eller noget I er holdt op med
        basis.append(
            {
                "vare": navn,
                "kategori": kategori(navn),
                "gange": len(ds),
                "typisk_interval_dage": round(interval, 1),
                "dage_siden_sidst": siden,
                "snart_tom": aktiv and siden >= 0.8 * interval,
                "ikke_koebt_laenge": not aktiv,
                "kr_pr_gang": round(p["kr"] / p["linjer"], 2),
            }
        )
    basis.sort(key=lambda b: (-b["gange"], b["vare"]))

    i_alt = sum(k["total"] for k in kv)
    ikke_mad = pr_kat.get("ikke mad", 0) + pr_kat.get("pant", 0)
    mad_kr = sum(v for kat, v in pr_kat.items() if kat not in ("ikke mad", "pant"))
    favoritter = {
        kat: [n for n, p in sorted(pr_vare.items(), key=lambda x: (-len(x[1]["datoer"]), -x[1]["kr"])) if kategori(n) == kat][:8]
        for kat in MADKATEGORIER
    }
    return {
        "periode": {"fra": datoer[0].isoformat(), "til": datoer[-1].isoformat(), "uger": round(uger, 1), "kvitteringer": len(kv)},
        "budget": {
            "kvitteringer_kr_pr_uge": round(i_alt / uger),
            "heraf_mad_kr_pr_uge": round(mad_kr / uger),
            "heraf_ikke_mad_og_pant_kr_pr_uge": round(ikke_mad / uger),
            "andel_af_indkoeb": andel_af_indkoeb,
            "skoennet_samlet_kr_pr_uge": round(i_alt / uger / andel_af_indkoeb),
            "indkoeb_pr_uge": round(len(kv) / uger, 1),
        },
        "kategorier_andel": {kat: round(v / mad_kr, 2) for kat, v in sorted(pr_kat.items(), key=lambda x: -x[1]) if kat not in ("ikke mad", "pant") and mad_kr},
        "oeko_andel": round(oeko / linjer, 2) if linjer else 0,
        "favoritter": favoritter,
        "basisvarer": basis[:top],
    }
