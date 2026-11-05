"""Stor live-test af varevalg i nemlig og Bilka. Kun søgning: kurven røres ikke.

Kør efter ændringer i sammenlign.py (kræver login til begge butikker):
    uv run python scripts/livetest_varevalg.py runde1

Markerer mistænkelige valg (forarbejdet, ikke-mad, andet kød, usikker, mange pakker,
ukendt pakke) og gemmer alle valg i livetest_<runde>.json, så to runder kan sammenlignes.
Gennemgå også de ikke-markerede valg med egne øjne: flagene fanger ikke alt.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from handle_mcp.butikker import hent_butik
from handle_mcp.historik import kategori
from handle_mcp.sammenlign import antal_pakker, er_forarbejdet, er_oeko, har_tilbehoer, ord_i, point, sammenlign
from handle_mcp.maengde import behov

LISTE = [
    # mejeri og æg
    "1 l letmælk", "1 l sødmælk", "500 g græsk yoghurt", "1 kg skyr", "10 stk æg", "200 g feta", "250 g smør",
    "250 ml piskefløde", "200 g revet ost", "125 g mozzarella", "100 g parmesan", "250 g hytteost", "200 g creme fraiche",
    # kød
    "500 g hakket oksekød", "500 g hakket svinekød", "500 g kyllingebryst", "1 kg kyllingelår", "400 g kalkunbryst",
    "150 g bacon", "450 g stegeflæsk", "400 g entrecote", "600 g nakkekoteletter", "500 g svinemørbrad",
    "200 g leverpostej", "1 stk hel kylling",
    # fisk
    "400 g laks", "400 g torsk", "200 g rejer", "125 g makrel i tomat", "185 g tun i vand",
    # frugt og grønt
    "500 g tomater", "250 g cherrytomater", "2 stk avocado", "1 stk agurk", "3 stk peberfrugt", "1 kg kartofler",
    "1 kg gulerødder", "1 kg løg", "500 g rødløg", "1 stk hvidløg", "1 stk broccoli", "1 stk blomkål", "1 stk spidskål",
    "200 g spinat", "1 stk squash", "1 stk aubergine", "250 g champignon", "1 kg æbler", "1 kg bananer", "4 stk citroner",
    "4 stk lime", "100 g ingefær", "75 g rucola", "1 stk icebergsalat", "1 bdt forårsløg", "1 stk porre",
    "500 g søde kartofler", "1 stk hokkaido græskar", "250 g jordbær", "125 g blåbær", "1 bdt persille", "1 bdt basilikum",
    # kolonial
    "500 g pasta", "1 kg ris", "1 kg havregryn", "1 kg hvedemel", "400 g kidneybønner", "400 g kikærter",
    "500 g røde linser", "400 g hakkede tomater", "500 g passata", "400 ml kokosmælk", "500 ml olivenolie",
    "500 g rugbrød", "350 g peanutbutter", "400 g honning", "200 g kalamata oliven", "100 g soltørrede tomater",
    "100 g græskarkerner", "200 g mandler", "8 stk tortilla", "500 g müsli", "400 g kaffe", "100 g mørk chokolade",
    "1,5 l danskvand", "500 g frosne ærter", "1 l hønsebouillon", "250 g bulgur",
]


def flag(ln, rent) -> list[str]:
    v = ln.valgt
    if not v:
        return ["INTET FUNDET"]
    f = []
    if not ln.sikker:
        f.append("usikker")
    if er_forarbejdet(v, ord_i(rent)):
        f.append("forarbejdet/andet kød")
    if har_tilbehoer(v):
        f.append("tilbehør i navn")
    if kategori(v.navn) == "ikke mad":
        f.append("IKKE MAD")
    if point(rent, v) < 1:
        f.append(f"point {point(rent, v):.2f}")
    if ln.behov and antal_pakker(v, ln.behov) is None:
        f.append("pakke ukendt")
    if ln.pakker > 4:
        f.append(f"{ln.pakker} pakker")
    return f


def vis(v, b):
    return f"{antal_pakker(v, b) or 1} x {v.navn[:40]} ({(v.maengde or '')[:20]}) {v.pris} kr{' ØKO' if er_oeko(v) else ''}"


async def main(runde: str):
    t0 = time.perf_counter()
    res = await sammenlign([hent_butik("nemlig"), hent_butik("bilka")], [(x, 1) for x in LISTE], oeko=True)
    ud = {}
    for r in res:
        print(f"\n######## {r.butik}: {len(r.linjer)} linjer, fejl={r.fejl}")
        flagget = 0
        for ln in r.linjer:
            rent, _ = behov(ln.soegning)
            f = flag(ln, rent)
            ud[f"{r.butik}|{ln.soegning}"] = {"valgt": ln.valgt.navn if ln.valgt else None, "flag": f, "beloeb": float(ln.beloeb)}
            if f:
                flagget += 1
                print(f"\n  [{', '.join(f)}] {ln.soegning}")
                print(f"     VALGT {vis(ln.valgt, ln.behov) if ln.valgt else '-'}")
                for a in ln.alternativer[:4]:
                    print(f"     alt   {vis(a, ln.behov)}")
        print(f"\n  => {flagget} af {len(r.linjer)} linjer flagget i {r.butik}")
    print(f"\n{time.perf_counter() - t0:.0f} s")
    Path(f"livetest_{runde}.json").write_text(json.dumps(ud, ensure_ascii=False, indent=1), encoding="utf-8")


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "x"))
