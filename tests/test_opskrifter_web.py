"""Opskrifter fra jeres sider. Siderne er gemt som små eksempler i de fire formater, siderne bruger."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from handle_mcp import opskrifter_web, server
from handle_mcp.opskrifter_web import OpskriftFejl, laes_side

JSONLD = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},
{"@type":"Recipe","name":"Baronens gryderet","recipeYield":["4","4 personer"],"totalTime":"PT1H15M",
"recipeIngredient":["500 g kalvemørbrad (i mindre tern)","3 fed hvidløg &amp; salt"]}]}</script></head></html>"""
MICRODATA = """<title>Kylling i Karry | Valdemarsro</title><span itemprop="recipeYield">4 personer</span>
<ul><li itemprop="recipeIngredient">400 g <b>kyllingefilet</b></li><li itemprop="recipeIngredient">1 løg, finthakket</li></ul>"""
INDLEJRET = """<meta property="og:title" content="Lasagne - Klassisk opskrift med oksekød"><script>window.x={"type":"Recipe",
"recipeYield":"4","totalTime":"PT60M","recipeIngredient":["25 g smør","500 g hakket oksekød"]}</script>"""
LISTE = """<p>4 personer</p><h2><div>Ingredienser</div></h2><ul><li>• 500 g kalv og flæsk</li><li>2 stk. æg</li></ul>
<h2><div>Fremgangsmåde</div></h2><ul><li>Rør farsen</li></ul><meta property="og:title" content="Frikadeller som mor lavede dem">"""


AFSNIT = """<meta property="og:title" content="Dyreryg i ovn"><p>Opskriften er til 6-8 personer.</p>
<h3 class="wp-block-heading">Ingredienser til dyreryg</h3><p>2 kilo dyreryg<br />1 lille potte <a href="/rosmarin/">rosmarin</a><br />15 enebær</p>
<h3>Ingredienser til sovsen</h3><p>4 deciliter fløde<br/>Salt</p><h3 class="wp-block-heading">Fremgangsmåde</h3><ol><li>Steg ryggen</li></ol>"""


@pytest.mark.parametrize(
    "side, navn, personer, tid, ingr",
    [
        # fundet i brug: Madens Verden "Dyreryg i ovn" har ingredienserne som afsnit med linjeskift
        (AFSNIT, "Dyreryg i ovn", 8, None, ["2 kilo dyreryg", "1 lille potte rosmarin", "15 enebær", "4 deciliter fløde", "Salt"]),
        (JSONLD, "Baronens gryderet", 4, 75, ["500 g kalvemørbrad (i mindre tern)", "3 fed hvidløg & salt"]),
        (MICRODATA, "Kylling i Karry", 4, None, ["400 g kyllingefilet", "1 løg, finthakket"]),
        (INDLEJRET, "Lasagne - Klassisk opskrift med oksekød", 4, 60, ["25 g smør", "500 g hakket oksekød"]),
        (LISTE, "Frikadeller som mor lavede dem", 4, None, ["500 g kalv og flæsk", "2 stk. æg"]),  # ikke fremgangsmåden
    ],
)
def test_de_fire_formater(side, navn, personer, tid, ingr):
    o = laes_side(side, "https://x", "test")
    assert (o.navn, o.personer, o.tid_minutter, o.ingredienser) == (navn, personer, tid, ingr)


def test_side_uden_opskrift():
    with pytest.raises(OpskriftFejl, match="ingen ingrediensliste"):
        laes_side("<html><title>Forside</title></html>", "https://x", "test")


@pytest.mark.parametrize(
    "link",
    [
        "https://www.example.com/opskrift",
        "http://www.valdemarsro.dk/kylling-karry/",  # kun https
        "https://www.valdemarsro.dk.evil.dk/x",
        "ikke et link",
    ],
)
async def test_kun_jeres_sider(link):
    with pytest.raises(ToolError, match="jeres sider|gyldigt link"):
        await server.hent_opskrift_fra_link(link)


async def test_hent_via_vaerktoejet(monkeypatch):
    monkeypatch.setattr(opskrifter_web, "_transport", httpx.MockTransport(lambda r: httpx.Response(200, text=JSONLD)))
    o = await server.hent_opskrift_fra_link("https://madensverden.dk/baronens-gryde/")
    assert o["kilde"] == "Madens Verden" and o["personer"] == 4 and len(o["ingredienser"]) == 2


async def test_404_giver_klar_fejl(monkeypatch):
    monkeypatch.setattr(opskrifter_web, "_transport", httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ToolError, match="404"):
        await server.hent_opskrift_fra_link("https://www.arla.dk/opskrifter/findes-ikke/")
