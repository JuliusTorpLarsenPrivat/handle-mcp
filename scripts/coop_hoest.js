// Henter alle kvitteringer med varelinjer fra medlem.coop.dk og sender dem til modtageren
// (scripts/coop_hoest.py) på denne computer. Kør det i fanen
// https://medlem.coop.dk/din-profil/konto-kvitteringer/kvitteringer/ efter at have logget ind.
//
// Siden må ikke sende data ud (CSP). Derfor lægger scriptet en grøn knap på siden: et klik
// åbner modtagerens side på 127.0.0.1, og data gives videre med postMessage.
// Kort-, medlems- og kvitteringsnumre bliver i browseren: kun dato, butik, total og varer sendes.
//
// Sæt NOEGLE til den nøgle, coop_hoest.py skriver, før scriptet køres.
const NOEGLE = "SÆT-NØGLEN-HER";
const MODTAGER = "http://127.0.0.1:8799";

const vent = (ms) => new Promise((r) => setTimeout(r, ms));
const BETALING = /^(betalingskort|kontant|mobilepay|dankort|gavekort|visa|mastercard|byttepenge|coop pay|kreditkort)/i;
const HOVED = /^(rabat på køb|bonus på køb|det fik du|medlemsbonus|se mere)/i;
const kr = (s) => parseFloat(String(s).replace(/\./g, "").replace(",", "."));

async function hentListe() {
  const alle = [];
  let cursor = "";
  for (let side = 0; side < 200; side++) {
    const url = "/umbraco/api/receiptsapi/get?limit=25&cursor=" + encodeURIComponent(cursor);
    const e = await (await fetch(url, { credentials: "include" })).json();
    alle.push(...(e.receipts || []));
    if (!e.nextCursor || !(e.receipts || []).length) break;
    cursor = e.nextCursor;
    await vent(300); // vær flink ved Coop
  }
  return alle;
}

async function hentVarer(id) {
  const t = await (await fetch("/umbraco/api/receiptsapi/getdetails?id=" + id, { credentials: "include" })).text();
  const d = new DOMParser().parseFromString(t, "text/html");
  const rows = [...d.querySelectorAll("table tr")].map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent.replace(/\s+/g, " ").trim()));
  const varer = [];
  for (const r of rows) {
    if (BETALING.test(r[0] || "") || /^PAN:/.test(r[0] || "")) break;
    if (r.length < 2 || HOVED.test(r[0])) continue;
    const pris = kr(r[1]);
    if (isNaN(pris)) continue;
    if (/rabat/i.test(r[0])) {
      if (varer.length) varer[varer.length - 1].rabat = Math.round((varer[varer.length - 1].rabat + pris) * 100) / 100;
      continue;
    }
    varer.push({ navn: r[0], pris, rabat: 0 });
  }
  return varer;
}

const liste = await hentListe();
const kvitteringer = [];
let afvigelser = 0;
for (const k of liste) {
  const varer = await hentVarer(k.receiptId);
  const total = k.totalAmount && k.totalAmount.value;
  const sum = varer.reduce((s, v) => s + v.pris + v.rabat, 0);
  if (Math.abs(sum - total) > 1) afvigelser++;
  kvitteringer.push({ tid: k.localPurchaseTimestamp, butik: k.storeName, total, varer });
  await vent(400);
}

window.__coopSvar = "venter på klik";
const knap = document.createElement("button");
knap.textContent = `Send ${kvitteringer.length} kvitteringer til pc`;
Object.assign(knap.style, { position: "fixed", top: "200px", left: "500px", zIndex: 99999, padding: "20px", fontSize: "20px", background: "#0a0", color: "#fff" });
knap.onclick = () => {
  let vindue = null;
  const lyt = (e) => {
    if (e.origin !== MODTAGER) return;
    if (e.data === "klar") vindue.postMessage(kvitteringer, MODTAGER);
    else {
      window.removeEventListener("message", lyt);
      window.__coopSvar = e.data;
      knap.remove();
    }
  };
  window.addEventListener("message", lyt);
  vindue = window.open(`${MODTAGER}/${NOEGLE}`, "modtager");
  if (!vindue) window.__coopSvar = "popup blokeret";
};
document.body.appendChild(knap);
({ kvitteringer: kvitteringer.length, varelinjer: kvitteringer.reduce((s, k) => s + k.varer.length, 0), sum_passer_ikke: afvigelser });
