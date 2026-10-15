"""Compare le moteur Python (app.py) au port JavaScript (calcul.js) sur 300 scénarios aléatoires.
Nécessite Node.js ; sinon le test est simplement ignoré.   Lancer :  python tests/parite_js.py"""
import json, os, random, shutil, subprocess, sys
RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, RACINE); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import synthese
from test_calculs import tirage

if not shutil.which("node"):
    print("Node.js absent : test de parité ignoré."); sys.exit(0)
rng = random.Random(3)
cas = [tirage(rng) for _ in range(300)]
js = ("const fs=require('fs');eval(fs.readFileSync('calcul.js','utf8')+';globalThis.synthese=synthese');"
      "const c=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(c.map(synthese)))")
res = subprocess.run(["node", "-e", js], input=json.dumps(cas), capture_output=True, text=True, cwd=RACINE, check=True)
sortie, ecart, n_cmp = json.loads(res.stdout), 0.0, 0
for p, a in zip(cas, sortie):
    b = synthese(p)
    for k, v in b["kpis"].items():
        if k in a["kpis"] and v is not None and a["kpis"][k] is not None:
            ecart = max(ecart, abs(v - a["kpis"][k])); n_cmp += 1
    assert len(a["mensualites"]) == len(b["mensualites"]), p
    ecart = max(ecart, max((abs(x - y) for x, y in zip(a["mensualites"], b["mensualites"])), default=0))
    assert [x["label"] for x in a["periodes"]] == [x["label"] for x in b["periodes"]], p
assert ecart < 0.02, ecart
print(f"Parité OK : {len(cas)} scénarios, {n_cmp} indicateurs comparés, écart maximal {ecart:.4f} €")
