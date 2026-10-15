"""Vérification indépendante du moteur de calcul.   Lancer :  python -m unittest discover -s tests -v

- un 2e calcul « de référence » (formules fermées) est comparé à app.simuler/synthese ;
- des invariants financiers sont contrôlés sur 400 scénarios aléatoires ;
- des cas limites (taux 0, livraison immédiate, apport = prix…) vérifient l'absence d'erreur.
"""
import os, random, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import app, DEFAULTS, APPELS_VEFA, nettoyer, pmt, simuler, synthese, mois_entre

TOL = 0.011


def solde(P, r, pm, k):
    """Capital restant dû après k échéances (formule fermée)."""
    return P - pm * k if r == 0 else P * (1 + r) ** k - pm * ((1 + r) ** k - 1) / r


def reference(p):
    """Mensualité totale mois par mois, recalculée sans réutiliser app.simuler."""
    nb = max(0, mois_entre(p["date_achat"], p["date_liv"]))
    P1 = p["prix"] - p["apport"] - p["ptz"] - p["al"]
    tot = p["prix"] - p["apport"]
    r1, r2, ia = p["taux"] / 1200, p["taux_al"] / 1200, p["ass"] / 1200
    n1, n2 = int(p["duree"] * 12), int(p["duree_al"] * 12)
    m1, m2 = pmt(P1, r1, n1), pmt(p["al"], r2, n2)
    out = []
    for m in range(1, nb + 1):
        pct = {"tout": 1.0, "lin": m / nb}.get(p["fonds"])
        if pct is None:
            pct = max([q for mo, q in APPELS_VEFA if mo < m], default=0)
        d = min(max(p["prix"] * pct - p["apport"], 0), tot)
        f = d / tot if tot > 0 else 0
        out.append(P1 * f * r1 + p["al"] * f * r2 + d * ia)
    npz = max(1, int((p["ptz_dur"] - p["ptz_dif"]) * 12))
    dif, dur = int(p["ptz_dif"] * 12), int(p["ptz_dur"] * 12)
    for k in range(1, int(max(p["duree"], p["duree_al"], p["ptz_dur"]) * 12) + 1):
        t = 0.0
        if k <= n1 and P1 > 0:
            t += m1 if k < n1 else solde(P1, r1, m1, k - 1) * (1 + r1)
        if k <= n2 and p["al"] > 0:
            t += m2 if k < n2 else solde(p["al"], r2, m2, k - 1) * (1 + r2)
        if dif < k <= dur:
            t += p["ptz"] / npz
        if k <= max(n1, n2):
            t += tot * ia
        out.append(t)
    return out


def tirage(rng):
    d0 = rng.choice([2026, 2027, 2028])
    mois = rng.randint(1, 12)
    liv = rng.randint(0, 60)
    y, m = divmod(mois - 1 + liv, 12)
    prix = rng.randrange(100000, 800001, 1000)
    return nettoyer(dict(
        prix=prix, apport=rng.randrange(0, min(300000, prix // 2), 1000),
        date_achat=f"{d0}-{mois:02d}-01", date_liv=f"{d0 + y}-{m + 1:02d}-01",
        fonds=rng.choice(["vefa", "lin", "tout"]), taux=rng.choice([0, 1.5, 3, 4, 5.5, 8]),
        duree=rng.randint(5, 30), ass=rng.choice([0, .1, .25, .5, 1]),
        ptz=rng.randrange(0, 150001, 500), ptz_dif=rng.randint(0, 15), ptz_dur=rng.randint(5, 25),
        al=rng.randrange(0, 40001, 500), taux_al=rng.choice([0, 1, 2, 3]), duree_al=rng.randint(5, 25)))


class Valeurs(unittest.TestCase):
    def test_pmt_connu(self):  # valeur de référence bien connue : 100 000 € à 4 % sur 20 ans
        self.assertAlmostEqual(pmt(100000, .04 / 12, 240), 605.98, places=2)
        self.assertAlmostEqual(pmt(120000, 0, 240), 500.0)

    def test_cas_de_reference(self):
        k = synthese(nettoyer(DEFAULTS))["kpis"]
        self.assertAlmostEqual(k["mensualite_finale"], 1746.65, places=2)
        self.assertAlmostEqual(k["mf_ptz"], 450.0, places=2)
        self.assertAlmostEqual(k["mf_ass"], 52.5, places=2)
        self.assertAlmostEqual(k["mf_interet"], 585.0, places=2)      # 168000×4 %/12 + 30000×1 %/12
        self.assertEqual(k["duree_mois"], 30 + 18 * 12)


class Reference(unittest.TestCase):
    def test_mensualites_vs_reference(self):
        rng = random.Random(7)
        for _ in range(400):
            p = tirage(rng)
            if p["prix"] - p["apport"] - p["ptz"] - p["al"] < 0:
                continue
            ref = reference(p)
            _, _, _, rows = simuler(p)
            self.assertEqual(len(ref), len(rows))
            for a, b in zip(ref, rows):
                self.assertAlmostEqual(a, b["total"], delta=0.02, msg=str(p))


class Invariants(unittest.TestCase):
    def test_invariants(self):
        rng = random.Random(11)
        n_ok = 0
        for _ in range(400):
            p = tirage(rng)
            nb, princ, total, rows = simuler(p)
            if princ < 0 or p["ptz_dur"] <= p["ptz_dif"]:
                continue
            n_ok += 1
            s = synthese(p)
            for col, cap in (("princ", princ), ("al", p["al"]), ("ptz", p["ptz"])):
                self.assertAlmostEqual(sum(x[col] for x in rows), cap, delta=0.05, msg=f"{col} {p}")
            last = rows[-1]
            self.assertLess(last["bal"], 0.05)
            self.assertTrue(all(x["bal"] >= -1e-6 for x in rows))
            k = s["kpis"]
            self.assertAlmostEqual(k["total_rembourse"], total + k["interets"] + k["assurance"], delta=0.05)
            for i, t in enumerate(s["mensualites"]):
                self.assertAlmostEqual(t, sum(s["detail"][c][i] for c in s["detail"]), delta=0.03)
            self.assertEqual(sum(x["mois"] for x in s["periodes"]), len(s["mensualites"]))
            self.assertEqual(sum(x["mois"] for x in s["annuel"]), len(s["mensualites"]))
            self.assertAlmostEqual(sum(x["total"] for x in s["annuel"]), k["total_rembourse"], delta=0.05)
            self.assertLess(abs(k["mf_princ"] + k["mf_al"] + k["mf_ptz"] + k["mf_interet"] + k["mf_ass"] - k["mensualite_finale"]), 0.01)
            if p["taux"] == 0 and p["taux_al"] == 0:
                self.assertAlmostEqual(k["interets"], 0, places=6)
        self.assertGreater(n_ok, 150)


class CasLimites(unittest.TestCase):
    def test_pas_d_erreur(self):
        cas = [dict(taux=0, taux_al=0), dict(date_liv="2027-01-01"), dict(date_liv="2026-01-01"),
               dict(apport=302000, ptz=0, al=0), dict(apport=400000), dict(ptz_dif=10, ptz_dur=8),
               dict(fonds="lin"), dict(fonds="tout"), dict(ptz=0, al=0), dict(ass=0), dict(duree=5, duree_al=25, ptz_dur=5),
               dict(date_achat="pas-une-date"), dict(prix="abc")]
        for c in cas:
            s = synthese(nettoyer({**DEFAULTS, **c}))
            self.assertIn("kpis", s, c)

    def test_alertes(self):
        self.assertTrue(synthese(nettoyer({**DEFAULTS, "apport": 400000}))["alertes"])
        self.assertTrue(synthese(nettoyer({**DEFAULTS, "ptz_dif": 10, "ptz_dur": 8}))["alertes"])


class Api(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()

    def test_eligibilite(self):
        r = self.c.post("/api/eligibilite", json=dict(revenu_fiscal=30000, nb_personnes=2, zone="A",
                                                     type_logement="vefa", prix=300000, salarie=True))
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["PTZ"]["eligible"])
        self.assertEqual(d["PTZ"]["tranche"], 1)
        self.assertFalse(self.c.post("/api/eligibilite", json=dict(zone="C", type_logement="vefa", prix=200000))
                         .get_json()["PTZ"]["eligible"])
        self.assertEqual(self.c.post("/api/eligibilite", json=dict(revenu_fiscal="x", nb_personnes="2")).status_code, 200)

    def test_zone(self):
        z = lambda cp: self.c.post("/api/zone", json=dict(code_postal=cp)).get_json()["zone"]
        self.assertEqual(z("75116"), "Abis")
        self.assertEqual(z("94220"), "Abis")
        self.assertEqual(z("78000"), "A")
        self.assertEqual(self.c.post("/api/zone", json=dict(code_postal=12345)).status_code, 200)

    def test_entrees_invalides(self):
        for corps in ([1], {"scenarios": "x"}, {"scenarios": [1, {"date_achat": 5}]}):
            self.assertEqual(self.c.post("/api/simulate", json=corps).status_code, 200, corps)
        self.assertFalse(self.c.post("/api/poi", json=dict(lat="1];out;", lon=2)).get_json()["success"])

    def test_location_achat(self):
        d = self.c.post("/api/comparer", json=dict(apport=50000)).get_json()
        self.assertGreater(d["location"][0]["patrimoine_net"], 50000)


if __name__ == "__main__":
    unittest.main()
