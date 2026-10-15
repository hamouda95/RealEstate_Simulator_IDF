"""Génère dashboard_vefa.html : version autonome (calcul en JS) pour ouvrir sans serveur."""
import re, sys
sys.path.insert(0, ".")
import app as A
with A.app.test_request_context():
    html = A.render_template("index.html", groupes=A.GROUPES, defaults=A.DEFAULTS)
css = open("static/style.css").read().replace(
    "@media (prefers-color-scheme:dark){:root{", '@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){')
css += ':root[data-theme="dark"]{--bg:#0f1715;--card:#17211f;--ink:#e8efec;--mute:#93a39d;--line:#2a3733;--s1:#3fb59d;--s2:#e3b04b;--s3:#6f9fe0;--s4:#e0735c;--good:#3fb59d;--bad:#e0735c}\n:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}html{scroll-padding-top:env(safe-area-inset-top,0px)}\n'
js = re.sub(r"const r = await fetch.*?resultats = await r\.json\(\);", "resultats = scenarios.map(s => synthese(s.p));",
            open("static/app.js").read(), flags=re.S)
calc = open("calcul.js").read()
html = html.replace("initial-scale=1", "initial-scale=1, viewport-fit=cover")
html = re.sub(r'<link rel="stylesheet"[^>]*>', lambda m: "<style>" + css + "</style>", html)
html = re.sub(r'<script src="/static/app.js"></script>', lambda m: "<script>" + calc + js + "</script>", html)
open(sys.argv[1], "w").write(html)
