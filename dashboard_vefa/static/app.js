const MAX_SCENARIOS = 4;
const $ = id => document.getElementById(id);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const eur = (x, d = 0) => x.toLocaleString("fr-FR", {style: "currency", currency: "EUR", minimumFractionDigits: d, maximumFractionDigits: d});
const duree = m => { const a = Math.floor(m / 12), r = m % 12;
  return [a ? `${a} an${a > 1 ? "s" : ""}` : "", r ? `${r} mois` : ""].filter(Boolean).join(" ") || "0 mois"; };
const fmt = (v, d) => d === "mois" ? duree(v) : eur(v, d);
const moisLabel = (date, i) => { const d0 = new Date(date);
  return new Date(d0.getFullYear(), d0.getMonth() + i + 1, 1).toLocaleDateString("fr-FR", {month: "short", year: "numeric"}); };
const budget = {epargne: 1130, loyer: 2000, finLoyer: "2029-07-01"};
const ecartMois = (a, b) => { const [x, y] = a.split("-").map(Number), [u, v] = b.split("-").map(Number); return (u - x) * 12 + (v - y); };
const idxLoyer = date => ecartMois(date, budget.finLoyer) - 1;           // 1er mois (base 0) sans loyer
const budgetMois = (i, date) => budget.epargne + (i >= idxLoyer(date) ? budget.loyer : 0);
const ligneBudget = (len, date) => ({label: "Budget disponible", data: Array.from({length: len}, (_, i) => budgetMois(i, date)),
  borderColor: css("--ink"), borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false, stepped: true});
const plage = (a, b) => Math.abs(a - b) < 0.005 ? eur(a, 2) : `${eur(a, 2)} – ${eur(b, 2)}`;
const signe = (e, d = 2) => `${e >= 0 ? "+" : "−"}${eur(Math.abs(e), d)}`;

const LIGNES = [  // [libellé, clé du KPI, décimales, ligne mise en avant]
  ["Mensualité finale (après livraison)", "mensualite_finale", 2, "hl"],
  ["dont capital du prêt principal", "mf_princ", 2, "sub"],
  ["dont capital Action Logement", "mf_al", 2, "sub"],
  ["dont capital PTZ", "mf_ptz", 2, "sub"],
  ["dont intérêts", "mf_interet", 2, "sub"],
  ["dont assurance", "mf_ass", 2, "sub"],
  ["Maximum avant livraison", "max_avant", 2],
  ["Mensualité après la fin du PTZ", "apres_ptz", 2],
  ["Prêt principal", "pret_principal", 0],
  ["Total emprunté", "total_emprunte", 0],
  ["Intérêts", "interets", 0],
  ["Assurance", "assurance", 0],
  ["Coût du crédit", "cout_credit", 0, "hl"],
  ["Total remboursé", "total_rembourse", 0],
  ["Durée totale du crédit", "duree_mois", "mois"],
];

let scenarios = [{nom: "Scénario A", p: {...DEFAULTS}}];
let actif = 0, resultats = [], charts = {}, timer = null;
const couleur = i => css(`--s${i + 1}`);
const nomDefaut = () => "Scénario " + "ABCD"[scenarios.length];

// ---------- formulaire
function remplirFormulaire() {
  document.querySelectorAll("[data-k]").forEach(el => el.value = scenarios[actif].p[el.dataset.k]);
  $("nom").value = scenarios[actif].nom;
  document.querySelector(".panel").style.setProperty("--c", couleur(actif));
}

document.querySelectorAll("[data-k]").forEach(el => el.addEventListener("input", e => {
  const k = el.dataset.k, texte = el.type === "date" || el.tagName === "SELECT";
  const v = texte ? el.value : (parseFloat(el.value) || 0);
  scenarios[actif].p[k] = v;
  document.querySelectorAll(`[data-k="${k}"]`).forEach(o => { if (o !== el) o.value = v; });
  planifier();
}));

$("nom").addEventListener("input", e => { scenarios[actif].nom = e.target.value || "Sans nom"; renderOnglets(); render(); });
$("dup").onclick = () => ajouter(structuredClone(scenarios[actif].p));
$("del").onclick = () => {
  if (scenarios.length < 2) return;
  scenarios.splice(actif, 1);
  actif = Math.min(actif, scenarios.length - 1);
  tout();
};
$("reset").onclick = () => { scenarios[actif].p = {...DEFAULTS}; tout(); };

function ajouter(params) {
  if (scenarios.length >= MAX_SCENARIOS) return;
  scenarios.push({nom: nomDefaut(), p: params});
  actif = scenarios.length - 1;
  tout();
}

function renderOnglets() {
  $("tabs").innerHTML = "";
  scenarios.forEach((s, i) => {
    const b = document.createElement("button");
    b.className = "tab"; b.style.setProperty("--c", couleur(i));
    b.setAttribute("aria-current", i === actif);
    b.innerHTML = `<i></i>${s.nom.replace(/</g, "&lt;")}`;
    b.onclick = () => { actif = i; remplirFormulaire(); renderOnglets(); renderActif(); };
    $("tabs").append(b);
  });
  if (scenarios.length < MAX_SCENARIOS) {
    const b = document.createElement("button");
    b.className = "tab add"; b.textContent = "+ Nouveau scénario";
    b.onclick = () => ajouter({...DEFAULTS});
    $("tabs").append(b);
  }
}

// ---------- calcul (côté Python)
const planifier = () => { clearTimeout(timer); timer = setTimeout(refresh, 120); };

async function refresh() {
  try {
    const r = await fetch("/api/simulate", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scenarios: scenarios.map(s => s.p)})});
    if (!r.ok) throw new Error("erreur " + r.status);
    resultats = await r.json();
    render();
  } catch (e) {
    $("alertes").innerHTML = `<p class="warn">Calcul impossible : ${e.message}</p>`;
  }
}

function tout() { remplirFormulaire(); renderOnglets(); refresh(); }

// ---------- affichage
function render() {
  if (resultats.length !== scenarios.length) return;
  $("alertes").innerHTML = resultats.flatMap((r, i) => r.alertes.map(a => `<p class="warn">${scenarios[i].nom} : ${a}</p>`)).join("");
  renderComparaison(); renderGraphiques(); renderActif();
}

function renderComparaison() {
  let t = `<tr><th></th>${scenarios.map((s, i) => `<th style="--c:${couleur(i)}"><span class="dot"></span>${s.nom.replace(/</g, "&lt;")}</th>`).join("")}</tr>`;
  LIGNES.forEach(([lib, k, d, fort]) => {
    t += `<tr class="${fort || ""}"><td>${lib}</td>` + resultats.map((r, i) => {
      const v = r.kpis[k];
      if (v === null) return "<td>—</td>";
      const ref = resultats[0].kpis[k];
      const dv = i > 0 && ref !== null ? v - ref : 0;
      const delta = i > 0 && Math.abs(dv) >= (d === "mois" ? 0.5 : 0.5 * 10 ** -d)
        ? `<span class="delta ${dv < 0 ? "neg" : "pos"}">${dv > 0 ? "+" : "−"}${fmt(Math.abs(dv), d)} vs ${scenarios[0].nom}</span>` : "";
      return `<td>${fmt(v, d)}${delta}</td>`;
    }).join("") + "</tr>";
  });
  t += `<tr class="hl"><td>Marge après livraison (budget − mensualité)</td>` + resultats.map((r, i) => {
    const e = budgetMois(r.kpis.mois_chantier, scenarios[i].p.date_achat) - r.kpis.mensualite_finale;
    return `<td class="${e >= 0 ? "ok" : "ko"}">${signe(e)}</td>`; }).join("") + "</tr>";
  $("cmp").innerHTML = t;
}

function upsert(id, type, data, options) {
  const o = {responsive: true, maintainAspectRatio: false, animation: false, ...options};
  o.plugins = {legend: {position: "bottom"}, ...(options.plugins || {})};
  if (charts[id]) { charts[id].data = data; charts[id].options = o; charts[id].update(); }
  else charts[id] = new Chart($(id), {type, data, options: o});
}

function renderGraphiques() {
  Chart.defaults.color = css("--mute"); Chart.defaults.borderColor = css("--line");
  const n = Math.max(...resultats.map(r => r.mensualites.length));
  const d0 = new Date(scenarios[0].p.date_achat);
  const labels = Array.from({length: n}, (_, i) =>
    new Date(d0.getFullYear(), d0.getMonth() + i + 1, 1).toLocaleDateString("fr-FR", {month: "short", year: "numeric"}));
  const ligne = (cle, stepped) => scenarios.map((s, i) => ({
    label: s.nom, data: resultats[i][cle], borderColor: couleur(i), backgroundColor: couleur(i),
    borderWidth: 2, pointRadius: 0, stepped, fill: false}));
  const axes = {x: {ticks: {maxTicksLimit: 10}}, y: {ticks: {callback: v => eur(v)}}};
  const tip = d => ({plugins: {tooltip: {mode: "index", intersect: false, callbacks: {label: c => `${c.dataset.label} : ${eur(c.parsed.y, d)}`}}}, interaction: {mode: "index", intersect: false}});

  upsert("c1", "line", {labels, datasets: [...ligne("mensualites", true), ligneBudget(n, scenarios[0].p.date_achat)]}, {scales: axes, ...tip(2)});
  upsert("c2", "line", {labels, datasets: ligne("capital", false)}, {scales: axes, ...tip(0)});
  upsert("c3", "bar", {labels: scenarios.map(s => s.nom), datasets: [
    {label: "Intérêts", data: resultats.map(r => r.kpis.interets), backgroundColor: css("--mute")},
    {label: "Assurance", data: resultats.map(r => r.kpis.assurance), backgroundColor: css("--s4")}]},
    {scales: {x: {stacked: true}, y: {stacked: true, ticks: {callback: v => eur(v)}}},
     plugins: {tooltip: {callbacks: {label: c => `${c.dataset.label} : ${eur(c.parsed.y)}`}}}});
}

function renderSplit() {
  const r = resultats[actif]; if (!r) return;
  $("split-titre").textContent = `Répartition de la mensualité – ${scenarios[actif].nom}`;
  const d0 = new Date(scenarios[actif].p.date_achat);
  const labels = r.mensualites.map((_, i) =>
    new Date(d0.getFullYear(), d0.getMonth() + i + 1, 1).toLocaleDateString("fr-FR", {month: "short", year: "numeric"}));
  const ds = (l, k, c) => ({label: l, data: r.detail[k], backgroundColor: css(c), borderWidth: 0, barPercentage: 1, categoryPercentage: 1});
  upsert("c4", "bar", {labels, datasets: [ds("Prêt principal", "princ", "--s1"), ds("Action Logement", "al", "--s2"),
      ds("PTZ", "ptz", "--s3"), ds("Intérêts", "interet", "--mute"), ds("Assurance", "ass", "--s4"),
      {...ligneBudget(r.mensualites.length, scenarios[actif].p.date_achat), type: "line", order: 0}]},
    {scales: {x: {stacked: true, ticks: {maxTicksLimit: 10}}, y: {stacked: true, ticks: {callback: v => eur(v)}}},
     interaction: {mode: "index", intersect: false},
     plugins: {tooltip: {mode: "index", intersect: false, callbacks: {
       label: c => `${c.dataset.label} : ${eur(c.parsed.y, 2)}`,
       footer: i => "Total : " + eur(i.filter(c => c.dataset.type !== "line").reduce((a, b) => a + b.parsed.y, 0), 2)}}}});
}

function periodesBudget(r, sp) {   // coupe les périodes à la date de fin du loyer
  const cut = idxLoyer(sp.date_achat), n = r.mensualites.length, out = [];
  r.periodes.forEach(s => {
    const fin = s.debut + s.mois;
    const parts = cut > s.debut && cut < fin ? [[s.debut, cut, s.label], [cut, fin, s.label + " (sans loyer)"]] : [[s.debut, fin, s.label]];
    parts.forEach(([a, b, label]) => {
      const m = r.mensualites.slice(a, b);
      out.push({label, debut: a, mois: b - a, mini: Math.min(...m), maxi: Math.max(...m), restant: n - a, budget: budgetMois(a, sp.date_achat)});
    });
  });
  return out;
}

function renderPeriodes() {
  const r = resultats[actif]; if (!r) return;
  const sp = scenarios[actif].p, P = periodesBudget(r, sp);
  $("per-titre").textContent = `Mensualité réelle par période – ${scenarios[actif].nom}`;
  $("frise").innerHTML = P.map(s => { const e = s.budget - s.maxi;
    return `<div class="blk ${e >= 0 ? "ok" : "ko"}" style="flex:${Math.max(s.mois, 8)} 1 112px"><b>${eur(s.maxi)}</b><span>${s.label}</span><small>${duree(s.mois)} · marge ${signe(e, 0)}</small></div>`; }).join("");
  $("periodes").innerHTML = "<tr><th>Période</th><th>Du</th><th>Au</th><th>Durée</th><th>Mensualité réelle</th><th>Durée restante du crédit</th><th>Budget</th><th>Marge</th></tr>" +
    P.map(s => { const e = s.budget - s.maxi;
      return `<tr><td>${s.label}</td><td>${moisLabel(sp.date_achat, s.debut)}</td><td>${moisLabel(sp.date_achat, s.debut + s.mois - 1)}</td>` +
        `<td>${duree(s.mois)}</td><td>${plage(s.mini, s.maxi)}</td><td>${duree(s.restant)}</td><td>${eur(s.budget)}</td>` +
        `<td class="${e >= 0 ? "ok" : "ko"}">${signe(e)}</td></tr>`; }).join("");
}

let vue = "an";
document.querySelectorAll("[data-v]").forEach(b => b.onclick = () => { vue = b.dataset.v; renderAnnuel(); });

function renderAnnuel() {
  const r = resultats[actif]; if (!r) return;
  const sp = scenarios[actif].p;
  document.querySelectorAll("[data-v]").forEach(b => b.setAttribute("aria-pressed", b.dataset.v === vue));
  $("annuel-titre").textContent = `Synthèse ${vue === "an" ? "par année" : "par mois"} – ${scenarios[actif].nom}`;
  const ec = (v, b) => `<td class="${b - v >= 0 ? "ok" : "ko"}">${signe(b - v)}</td>`;
  if (vue === "an") {
    let pos = 0;
    $("annuel").innerHTML = "<tr><th>Année</th><th>Mois</th><th>Mensualité moy.</th><th>Min – max</th><th>Intérêts</th><th>Assurance</th><th>Total payé</th><th>Marge moy./mois</th><th>Capital restant fin d'année</th></tr>" +
      r.annuel.map(a => {
        const deb = pos; pos += a.mois;
        const bm = Array.from({length: a.mois}, (_, k) => budgetMois(deb + k, sp.date_achat)).reduce((x, y) => x + y, 0) / a.mois;
        return `<tr><td>${a.annee}</td><td>${a.mois}</td><td>${eur(a.mensualite_moy, 2)}</td><td>${plage(a.mini, a.maxi)}</td><td>${eur(a.interets)}</td><td>${eur(a.assurance)}</td><td>${eur(a.total)}</td>${ec(a.mensualite_moy, bm)}<td>${eur(a.capital_fin)}</td></tr>`; }).join("");
  } else {
    const d = r.detail;
    $("annuel").innerHTML = "<tr><th>Mois</th><th>Prêt principal</th><th>Act. Logement</th><th>PTZ</th><th>Intérêts</th><th>Assurance</th><th>Mensualité réelle</th><th>Marge</th><th>Capital restant</th></tr>" +
      r.mensualites.map((t, i) => `<tr><td>${moisLabel(sp.date_achat, i)}</td><td>${eur(d.princ[i], 2)}</td><td>${eur(d.al[i], 2)}</td><td>${eur(d.ptz[i], 2)}</td><td>${eur(d.interet[i], 2)}</td><td>${eur(d.ass[i], 2)}</td><td><b>${eur(t, 2)}</b></td>${ec(t, budgetMois(i, sp.date_achat))}<td>${eur(r.capital[i])}</td></tr>`).join("");
  }
}

function renderActif() { renderSplit(); renderPeriodes(); renderAnnuel(); }

function majBudget() {
  const fin = new Date(budget.finLoyer).toLocaleDateString("fr-FR", {month: "long", year: "numeric"});
  $("budget-total").innerHTML = `Budget disponible <strong>${eur(budget.epargne)}</strong> / mois, puis <strong>${eur(budget.epargne + budget.loyer)}</strong> dès ${fin}`;
}
["epargne", "loyer", "finLoyer"].forEach(k => {
  const el = $(k.toLowerCase()); el.value = budget[k];
  el.addEventListener("input", e => { budget[k] = k === "finLoyer" ? (e.target.value || budget.finLoyer) : (parseFloat(e.target.value) || 0); majBudget(); render(); });
});
majBudget();

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => resultats.length && (renderOnglets(), render()));
tout();
