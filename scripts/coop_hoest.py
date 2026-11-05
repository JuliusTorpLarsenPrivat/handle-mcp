"""Engangs-modtager til Coop-kvitteringer fra browseren (se coop_hoest.js).

    uv run python scripts/coop_hoest.py

Skriver en nøgle, lytter kun på 127.0.0.1:8799, fletter de modtagne kvitteringer ind i
%LOCALAPPDATA%\\handle-mcp\\coop_kvitteringer.json uden dubletter og lukker igen (højst 10 min).
Kvitteringer med kort- eller medlemsnumre afvises.

Sådan bruges det (fx af Claude med Chrome-udvidelsen):
  1. Den, der ejer Coop-kontoen, siger ja og logger selv ind på medlem.coop.dk.
  2. Start dette script og notér nøglen.
  3. Åbn https://medlem.coop.dk/din-profil/konto-kvitteringer/kvitteringer/ og kør
     coop_hoest.js i fanen med NOEGLE sat til nøglen.
  4. Klik på den grønne knap. Chrome tillader kun pop-up-vinduet efter et rigtigt klik.
"""

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from handle_mcp.historik import flet_kvitteringer
from handle_mcp.lokal import LokalFejl

NOEGLE = secrets.token_hex(16)
COOP = "https://medlem.coop.dk"
SIDE = """<!doctype html><meta charset="utf-8"><title>Modtager</title>
<p id="s">Venter på data fra Coop-fanen…</p>
<script>
window.addEventListener('message', async (e) => {
  if (e.origin !== 'COOP') return;
  const r = await fetch('/NOEGLE', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(e.data)});
  const t = await r.text();
  document.getElementById('s').textContent = t;
  e.source.postMessage(t, 'COOP');
});
if (window.opener) window.opener.postMessage('klar', 'COOP');
</script>""".replace("COOP", COOP).replace("NOEGLE", NOEGLE)


class Modtager(BaseHTTPRequestHandler):
    def _svar(self, kode: int, tekst: str, type_: str = "text/plain; charset=utf-8") -> None:
        self.send_response(kode)
        self.send_header("Content-Type", type_)
        self.end_headers()
        self.wfile.write(tekst.encode())

    def do_GET(self):
        if self.path != f"/{NOEGLE}":
            return self._svar(403, "forkert nøgle")
        self._svar(200, SIDE, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != f"/{NOEGLE}":
            return self._svar(403, "forkert nøgle")
        try:
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            nye, i_alt = flet_kvitteringer(data)
        except (LokalFejl, ValueError, TypeError) as fejl:
            print(f"Afvist: {fejl}", flush=True)
            return self._svar(400, f"afvist: {fejl}")
        besked = f"{nye} nye kvitteringer, {i_alt} i alt"
        print(besked, flush=True)
        self._svar(200, besked)
        self.server.faerdig = True

    def log_message(self, *args):
        pass


def main() -> None:
    s = HTTPServer(("127.0.0.1", 8799), Modtager)
    s.faerdig = False
    s.timeout = 5
    print(f"Nøgle: {NOEGLE}\nLytter på http://127.0.0.1:8799 i højst 10 minutter …", flush=True)
    slut = time.monotonic() + 600
    while not s.faerdig and time.monotonic() < slut:
        s.handle_request()
    print("Færdig." if s.faerdig else "Ingen data modtaget. Lukker.", flush=True)


if __name__ == "__main__":
    main()
