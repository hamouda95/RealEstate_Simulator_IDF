"""Comparateur de scénarios de prêt immobilier en VEFA.

Lancer :  pip install -r requirements.txt && python app.py
Ouvrir :  http://127.0.0.1:5050
"""
import json
import math
import os

from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)

# Appels de fonds VEFA standard : (mois écoulé après lequel le palier s'applique, % du prix appelé)
APPELS_VEFA = [(0, 0.05), (3, 0.35), (9, 0.70), (18, 0.95)]

# ---------------------------------------------------------------- données officielles
# PTZ – offres de prêt émises depuis avril 2025 (service-public.fr F10871, vérifié le 15/09/2026).
SOURCE_PTZ = "service-public.fr – Prêt à taux zéro, offres émises depuis avril 2025 (vérifié le 15/09/2026)"
ZONES_PTZ = {"Abis": "A", "A": "A", "B1": "B1", "B2": "B2", "C": "C"}  # A et A bis partagent le barème
# Plafonds de ressources (revenu pris en compte) selon la zone et le nombre d'occupants (8 et +)
PLAFONDS_PTZ = {
    "A": [49000, 73500, 88200, 102900, 117600, 132300, 147000, 161700],
    "B1": [34500, 51750, 62100, 72450, 82800, 93150, 103500, 113850],
    "B2": [31500, 47250, 56700, 66150, 75600, 85050, 94500, 103950],
    "C": [28500, 42750, 51300, 59850, 68400, 76950, 85500, 94050],
}
# Bornes hautes des tranches 1 à 4 (revenu pris en compte ÷ coefficient familial)
TRANCHES_PTZ = {
    "A": [25000, 31000, 37000, 49000],
    "B1": [21500, 26000, 30000, 34500],
    "B2": [18000, 22500, 27000, 31500],
    "C": [15000, 19500, 24000, 28500],
}
COEF_FAMILIAL = [1, 1.5, 1.8, 2.1, 2.4]  # 1, 2, 3, 4, 5 personnes et +
# Coût maximal de l'opération pris en compte (1 à 5 personnes et +)
COUT_MAX_PTZ = {
    "A": [150000, 225000, 270000, 315000, 360000],
    "B1": [135000, 202500, 243000, 283500, 324000],
    "B2": [110000, 165000, 198000, 231000, 264000],
    "C": [100000, 150000, 180000, 210000, 240000],
}
QUOTITE_PTZ = {"collectif": [0.50, 0.40, 0.40, 0.20], "individuel": [0.30, 0.20, 0.20, 0.10]}
# Durée totale et différé par tranche (barème réglementaire, à confirmer dans l'offre de prêt)
DUREE_PTZ = [(25, 15), (25, 10), (22, 5), (20, 0)]

# Prêt Accession Action Logement (actionlogement.fr) : 30 000 € max, 1 % fixe, 25 ans max.
SOURCE_AL = "actionlogement.fr – Prêt accession : 30 000 € max, taux fixe 1 % hors assurance, 25 ans max"
PLAFONDS_AL = {
    "Abis": {1: 43475, 2: 64976, 3: 85175, 4: 101693, 5: 120995, 6: 136151, "sup": 15168},
    "A": {1: 43475, 2: 64976, 3: 78104, 4: 93556, 5: 110753, 6: 124630, "sup": 13886},
    "B1": {1: 35435, 2: 47321, 3: 56905, 4: 68699, 5: 80816, 6: 91078, "sup": 10161},
    "B2": {1: 31892, 2: 42588, 3: 51215, 4: 61830, 5: 72735, 6: 81971, "sup": 9142},
    "C": {1: 31589, 2: 42186, 3: 50731, 4: 61243, 5: 72044, 6: 81192, "sup": 9063},
}


# ---------------------------------------------------------------- fonctions d'éligibilité aux aides
def nombre(v, defaut):
    """Convertit une saisie en nombre fini, sinon renvoie la valeur par défaut."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return defaut
    return x if math.isfinite(x) else defaut


def calculer_ptz(cout, type_operation, zone, revenu_fiscal, nb_personnes, apport=0.0):
    """PTZ maximal selon les règles des offres émises depuis avril 2025."""
    z = ZONES_PTZ.get(zone)
    if z is None:
        return {"eligible": False, "raison": f"Zone inconnue : {zone}"}
    if cout <= 0:
        return {"eligible": False, "raison": "Indiquez le coût total de l'opération"}
    if type_operation == "ancien" and z not in ("B2", "C"):
        return {"eligible": False, "raison": "Dans l'ancien, le PTZ n'est possible qu'en zone B2 ou C (avec travaux)"}
    n = max(1, int(nb_personnes))
    revenu_retenu = max(revenu_fiscal, cout / 9)
    plafond = PLAFONDS_PTZ[z][min(n, 8) - 1]
    base = {"zone": zone, "revenu_retenu": round(revenu_retenu), "plafond_zone": plafond, "source": SOURCE_PTZ}
    if revenu_retenu > plafond:
        return {**base, "eligible": False,
                "raison": f"Revenu pris en compte {round(revenu_retenu):,} € > plafond {plafond:,} € "
                          f"(le revenu retenu est au moins égal au coût de l'opération ÷ 9)".replace(",", " ")}
    coef = COEF_FAMILIAL[min(n, 5) - 1]
    quotient = revenu_retenu / coef
    tranche = next(i for i, borne in enumerate(TRANCHES_PTZ[z]) if quotient <= borne) + 1
    collectif = "collectif" if type_operation == "vefa" else "individuel"
    quotite = QUOTITE_PTZ[collectif][tranche - 1]
    cout_retenu = min(cout, COUT_MAX_PTZ[z][min(n, 5) - 1])
    montant = cout_retenu * quotite
    # Le PTZ ne peut pas dépasser les autres prêts (> 2 ans), ou 1,25 fois ceux-ci en tranche 1.
    besoin = max(cout - apport, 0)
    ratio = 1.25 if tranche == 1 else 1.0
    montant = min(montant, besoin * ratio / (1 + ratio))
    duree, differe = DUREE_PTZ[tranche - 1]
    return {**base, "eligible": True, "montant_max": int(montant), "quotite": round(quotite * 100),
            "tranche": tranche, "duree_max": duree, "differe": differe, "cout_retenu": cout_retenu,
            "coef_familial": coef, "ratio_revenu": round(revenu_retenu / plafond, 2),
            "logement": "bâtiment collectif" if collectif == "collectif" else "maison individuelle"}


def verifier_eligibilite_al(revenu, zone, nb_personnes, salarie):
    """Vérifie l'éligibilité au prêt Action Logement."""
    if not salarie:
        return False, "Réservé aux salariés d'une entreprise privée non agricole de 10 salariés et plus"
    plafond = PLAFONDS_AL.get(zone, {}).get(min(nb_personnes, 6), 0)
    if nb_personnes > 6:
        plafond += PLAFONDS_AL.get(zone, {}).get("sup", 0) * (nb_personnes - 6)
    if revenu > plafond:
        return False, f"Revenu {revenu:,.0f} € > plafond {plafond:,} €".replace(",", " ")
    return True, "Éligible"


def eligibilite_aides(params):
    """Calcule l'éligibilité à toutes les aides selon les paramètres."""
    revenu = max(0.0, nombre(params.get("revenu_fiscal"), 0))
    nb_personnes = min(20, max(1, int(nombre(params.get("nb_personnes"), 1))))
    zone = params.get("zone", "B1")
    if zone not in ZONES_PTZ:
        zone = "B1"
    type_logement = params.get("type_logement", "vefa")  # vefa (collectif), neuf (maison), ancien
    if type_logement not in ("vefa", "neuf", "ancien"):
        type_logement = "vefa"
    salarie = bool(params.get("salarie", False))
    prix = max(0.0, nombre(params.get("prix"), 0))
    apport = max(0.0, nombre(params.get("apport"), 0))

    aides = {}
    ptz = calculer_ptz(prix, type_logement, zone, revenu, nb_personnes, apport)
    if ptz["eligible"] and type_logement == "ancien":
        ptz["condition"] = "Travaux d'amélioration d'au moins 25 % du coût total de l'opération"
    aides["PTZ"] = ptz

    eligible_al, raison_al = verifier_eligibilite_al(revenu, zone, nb_personnes, salarie)
    if eligible_al:
        aides["Action Logement"] = {"eligible": True, "montant_max": int(min(30000, prix)), "taux": 1.0,
                                    "duree_max": 25, "condition": "Primo-accédant, résidence principale",
                                    "source": SOURCE_AL}
    else:
        aides["Action Logement"] = {"eligible": False, "raison": raison_al}

    # PAS : mêmes plafonds de ressources que le PTZ (revenu fiscal de référence N-2).
    plafond_pas = PLAFONDS_PTZ[ZONES_PTZ[zone]][min(nb_personnes, 8) - 1]
    if revenu <= plafond_pas:
        aides["PAS"] = {"eligible": True, "condition": f"Revenu ≤ {plafond_pas:,} €".replace(",", " "),
                        "avantages": ["Frais de dossier plafonnés", "Ouvre droit aux APL accession",
                                      "Proposé par les banques conventionnées"]}
    else:
        aides["PAS"] = {"eligible": False, "raison": f"Revenu > {plafond_pas:,} €".replace(",", " ")}

    if type_logement in ("neuf", "vefa"):
        aides["PSLA"] = {"eligible": True, "condition": "Uniquement pour un programme agréé PSLA",
                         "avantages": ["TVA réduite (5,5 %)", "Exonération de taxe foncière possible (15 ans)",
                                       "Phase locative avant l'achat"]}
    else:
        aides["PSLA"] = {"eligible": False, "raison": "Réservé aux logements neufs agréés"}
    return aides


# ---------------------------------------------------------------- simulation location vs achat
def simulation_location(params):
    """Simule le scénario location sur une durée donnée."""
    loyer_mensuel = nombre(params.get("loyer"), 1000)
    augmentation_annuelle = nombre(params.get("augmentation_loyer"), 0.015)  # 1.5% par défaut
    epargne_mensuelle = nombre(params.get("epargne"), 500)
    rendement_epargne = nombre(params.get("rendement_epargne"), 0.03)  # 3% livret A
    duree_annees = nombre(params.get("duree_analyse"), 20)
    charges_locatives = nombre(params.get("charges_locatives"), 50)

    mois = int(duree_annees * 12)
    # Le locataire conserve son apport et le place.
    epargne_cumulee = nombre(params.get("apport"), 50000)
    loyer_actuel = loyer_mensuel
    resultats = []

    for m in range(1, mois + 1):
        # Augmentation annuelle du loyer
        if m > 1 and m % 12 == 1:
            loyer_actuel *= (1 + augmentation_annuelle)

        # Coût mensuel location
        cout_mensuel = loyer_actuel + charges_locatives

        # Épargne avec intérêts composés mensuels
        taux_mensuel = rendement_epargne / 12
        epargne_cumulee = epargne_cumulee * (1 + taux_mensuel) + epargne_mensuelle

        resultats.append({
            "mois": m,
            "loyer": round(loyer_actuel, 2),
            "charges": charges_locatives,
            "cout_total": round(cout_mensuel, 2),
            "epargne_mensuelle": epargne_mensuelle,
            "epargne_cumulee": round(epargne_cumulee, 2),
            "patrimoine_net": round(epargne_cumulee, 2),
        })

    return resultats


def simulation_achat(params):
    """Simule le scénario achat avec patrimoine net."""
    prix = nombre(params.get("prix"), 300000)
    apport = nombre(params.get("apport"), 50000)
    plus_value_annuelle = nombre(params.get("plus_value"), 0.02)  # 2% par défaut
    charges_copro = nombre(params.get("charges_copro"), 100)
    taxe_fonciere = nombre(params.get("taxe_fonciere"), 1500)
    assurance_habitation = nombre(params.get("assurance_habitation"), 20)

    # Utiliser la simulation VEFA existante pour les mensualités
    p = nettoyer({
        "prix": prix,
        "apport": apport,
        "date_achat": "2024-01-01",
        "date_liv": "2024-01-01",  # Achat direct, pas de VEFA
        "fonds": "tout",
        "taux": params.get("taux", 4.0),
        "duree": params.get("duree_credit", 20),
        "ass": params.get("taux_assurance", 0.25),
        "ptz": params.get("ptz", 0),
        "ptz_dif": params.get("ptz_dif", 0),
        "ptz_dur": params.get("ptz_dur", 10),
        "al": params.get("al", 0),
        "taux_al": params.get("taux_al", 1.0),
        "duree_al": params.get("duree_al", 18),
    })

    nb, princ, total, rows = simuler(p)
    valeur_immobiliere = prix
    resultats = []

    for i, row in enumerate(rows):
        mois = i + 1

        # Plus-value annuelle
        if mois > 1 and mois % 12 == 1:
            valeur_immobiliere *= (1 + plus_value_annuelle)

        # Charges mensuelles
        charges_mensuelles = charges_copro + (taxe_fonciere / 12) + assurance_habitation

        # Patrimoine net = valeur immobilière - capital restant dû
        patrimoine_net = valeur_immobiliere - row["bal"]

        resultats.append({
            "mois": mois,
            "mensualite": round(row["total"], 2),
            "charges": round(charges_mensuelles, 2),
            "cout_total": round(row["total"] + charges_mensuelles, 2),
            "capital_restant": round(row["bal"], 2),
            "valeur_immobiliere": round(valeur_immobiliere, 2),
            "patrimoine_net": round(patrimoine_net, 2),
        })

    return resultats


def calculer_breakpoint(loc, achat):
    """Calcule le breakpoint (année où l'achat devient plus rentable)."""
    breakpoint_annee = None
    breakpoint_mois = None

    for i in range(min(len(loc), len(achat))):
        if achat[i]["patrimoine_net"] > loc[i]["patrimoine_net"]:
            breakpoint_mois = i + 1
            breakpoint_annee = (i + 1) // 12
            break

    return {
        "breakpoint_mois": breakpoint_mois,
        "breakpoint_annee": breakpoint_annee,
        "existe": breakpoint_mois is not None,
    }


def comparer_location_achat(params):
    """Compare les scénarios location et achat."""
    loc = simulation_location(params)
    achat = simulation_achat(params)
    breakpoint = calculer_breakpoint(loc, achat)

    return {
        "location": loc,
        "achat": achat,
        "breakpoint": breakpoint,
    }


# ---------------------------------------------------------------- géocodage et POI (OpenStreetMap)
UA = {"User-Agent": "VEFA-Dashboard/1.0 (immobilier-simulation)"}


def geocoder_adresse(adresse):
    """Géocode via la Base Adresse Nationale (api-adresse.data.gouv.fr), repli sur Nominatim."""
    try:
        r = requests.get("https://api-adresse.data.gouv.fr/search/", params={"q": adresse, "limit": 1},
                         headers=UA, timeout=(5, 10))
        r.raise_for_status()
        feats = r.json().get("features") or []
        if feats:
            pr, (lon, lat) = feats[0]["properties"], feats[0]["geometry"]["coordinates"]
            return {"success": True, "lat": float(lat), "lon": float(lon), "display_name": pr.get("label", adresse),
                    "code_insee": pr.get("citycode"), "code_postal": pr.get("postcode"), "commune": pr.get("city"),
                    "source": "Base Adresse Nationale"}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", headers=UA, timeout=(5, 10),
                         params={"q": adresse, "format": "json", "limit": 1, "countrycodes": "fr",
                                 "addressdetails": 1})
        r.raise_for_status()
        data = r.json()
        if data:
            ad = data[0].get("address", {})
            return {"success": True, "lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]),
                    "display_name": data[0]["display_name"], "code_postal": ad.get("postcode"),
                    "code_insee": None, "commune": ad.get("city") or ad.get("town") or ad.get("village"),
                    "source": "OpenStreetMap"}
        return {"success": False, "error": "Adresse introuvable : vérifiez l'orthographe ou ajoutez la ville."}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return {"success": False, "error": "Service de géolocalisation indisponible, réessayez dans un instant."}


OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def rechercher_poi(lat, lon, rayon=500):
    """Recherche les POI (Points of Interest) autour d'une position via Overpass API."""
    lat, lon, rayon = nombre(lat, None), nombre(lon, None), nombre(rayon, 500)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"success": False, "error": "Coordonnées invalides"}
    rayon = int(min(max(rayon, 50), 5000))
    try:
        # Requête Overpass QL pour les commodités principales
        overpass_query = f"""
        [out:json][timeout:25];
        (
          node["railway"="station"](around:{rayon},{lat},{lon});
          node["highway"="bus_stop"](around:{rayon},{lat},{lon});
          node["amenity"="school"](around:{rayon},{lat},{lon});
          node["amenity"="kindergarten"](around:{rayon},{lat},{lon});
          node["amenity"="supermarket"](around:{rayon},{lat},{lon});
          node["amenity"="pharmacy"](around:{rayon},{lat},{lon});
          node["amenity"="hospital"](around:{rayon},{lat},{lon});
          node["amenity"="parking"](around:{rayon},{lat},{lon});
          node["leisure"="park"](around:{rayon},{lat},{lon});
          node["shop"="bakery"](around:{rayon},{lat},{lon});
          node["amenity"="restaurant"](around:{rayon},{lat},{lon});
        );
        out body;
        """
        headers = {
            "User-Agent": "VEFA-Dashboard/1.0 (immobilier-simulation)"
        }
        erreurs = []
        for url in OVERPASS_URLS:  # les serveurs publics sont souvent saturés
            try:
                response = requests.post(url, data={"data": overpass_query}, headers=headers, timeout=(5, 30))
                response.raise_for_status()
                data = response.json()
                break
            except (requests.RequestException, ValueError) as e:
                erreurs.append(f"{url.split('/')[2]}: {e.__class__.__name__}")
        else:
            return {"success": False, "error": "Serveurs Overpass indisponibles, réessayez dans quelques minutes (" + "; ".join(erreurs) + ")"}

        poi = []
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            poi_type = None

            # Déterminer le type de POI
            if tags.get("railway") == "station":
                poi_type = "Transport"
            elif tags.get("highway") == "bus_stop":
                poi_type = "Transport"
            elif tags.get("amenity") == "school":
                poi_type = "Éducation"
            elif tags.get("amenity") == "kindergarten":
                poi_type = "Éducation"
            elif tags.get("amenity") == "supermarket":
                poi_type = "Commerce"
            elif tags.get("shop") == "bakery":
                poi_type = "Commerce"
            elif tags.get("amenity") == "pharmacy":
                poi_type = "Santé"
            elif tags.get("amenity") == "hospital":
                poi_type = "Santé"
            elif tags.get("leisure") == "park":
                poi_type = "Loisir"
            elif tags.get("amenity") == "restaurant":
                poi_type = "Restaurant"

            if poi_type:
                poi.append({
                    "type": poi_type,
                    "nom": tags.get("name", "Sans nom"),
                    "lat": element.get("lat"),
                    "lon": element.get("lon"),
                })

        return {"success": True, "poi": poi}
    except Exception as e:
        return {"success": False, "error": str(e)}


def calculer_rating_quartier(poi):
    """Calcule un rating du quartier sur 5 basé sur les POI disponibles."""
    # Compter les POI par catégorie
    par_type = {}
    for p in poi:
        par_type[p.get("type")] = par_type.get(p.get("type"), 0) + 1

    # Score par catégorie (max 1 point par catégorie)
    score = 0
    criteres = {
        "Transport": 3,  # Besoin d'au moins 3 points de transport
        "Éducation": 1,  # Au moins 1 école
        "Commerce": 2,  # Au moins 2 commerces
        "Santé": 1,  # Au moins 1 pharmacie/hôpital
        "Loisir": 1,  # Au moins 1 parc
    }

    for categorie, min_requis in criteres.items():
        if par_type.get(categorie, 0) >= min_requis:
            score += 1

    # Bonus pour densité élevée
    total_poi = len(poi)
    if total_poi >= 20:
        score += 0.5
    elif total_poi >= 10:
        score += 0.25

    # Convertir en note sur 5
    note_sur_5 = min(5, round(score * 5 / 5.5, 1))

    return {
        "note": note_sur_5,
        "note_sur_5": f"{note_sur_5}/5",
        "etoiles": "★" * int(note_sur_5) + "☆" * (5 - int(note_sur_5)),
        "details": par_type,
        "total_poi": total_poi,
    }


with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zonage_abc.json"),
          encoding="utf-8") as _f:
    ZONAGE = json.load(_f)


def zone_commune(code_insee):
    """Zone A bis / A / B1 / B2 / C d'une commune (code INSEE), selon l'arrêté en vigueur."""
    code = str(code_insee or "").strip().upper()
    if code.startswith("751") and len(code) == 5:  # arrondissements de Paris
        code = "75056"
    return ZONAGE["zones"].get(code, ZONAGE["defaut"])


def communes_code_postal(code_postal):
    """Communes d'un code postal via geo.api.gouv.fr (liste vide si le service est indisponible)."""
    try:
        r = requests.get("https://geo.api.gouv.fr/communes", params={"codePostal": code_postal, "fields": "nom,code"},
                         headers=UA, timeout=(5, 10))
        r.raise_for_status()
        return [c for c in r.json() if isinstance(c, dict) and c.get("code")]
    except (requests.RequestException, ValueError):
        return []


def determiner_zone(code_insee=None, code_postal=None):
    if code_insee:
        z = zone_commune(code_insee)
        return {"zone": z, "communes": [{"code": str(code_insee), "zone": z}], "source": ZONAGE["source"]}
    cp = str(code_postal or "").strip()
    if not (len(cp) == 5 and cp.isdigit()):
        return {"zone": None, "error": "Code postal invalide (5 chiffres)."}
    if cp.startswith("75"):
        return {"zone": "Abis", "communes": [{"code": "75056", "nom": "Paris", "zone": "Abis"}], "source": ZONAGE["source"]}
    communes = [dict(c, zone=zone_commune(c["code"])) for c in communes_code_postal(cp)]
    if not communes:
        return {"zone": None, "error": "Zone introuvable pour ce code postal (service geo.api.gouv.fr indisponible ?)."}
    zones = {c["zone"] for c in communes}
    res = {"zone": communes[0]["zone"], "communes": communes, "source": ZONAGE["source"]}
    if len(zones) > 1:
        res["avertissement"] = "Ce code postal couvre plusieurs communes de zones différentes : choisissez la commune."
    return res


def fiscalite_locale(code_insee):
    """Taux votés de taxe foncière bâtie et de TEOM (REI, data.economie.gouv.fr), dernier exercice."""
    code = str(code_insee or "").strip().upper()
    if code.startswith("751") and len(code) == 5:
        code = "75056"
    if not (len(code) == 5 and code.isalnum()):
        return {"success": False, "error": "Code commune invalide."}
    try:
        r = requests.get("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
                         "fiscalite-locale-des-particuliers/records", headers=UA, timeout=(5, 15),
                         params={"where": f'insee_com="{code}"', "order_by": "exercice desc", "limit": 1})
        r.raise_for_status()
        rec = (r.json().get("results") or [None])[0]
    except (requests.RequestException, ValueError):
        return {"success": False, "error": "Données fiscales indisponibles pour le moment, réessayez plus tard."}
    if not rec or rec.get("taux_global_tfb") is None:
        return {"success": False, "error": "Aucun taux publié pour cette commune."}
    return {"success": True, "commune": rec.get("libcom"), "exercice": rec.get("exercice"),
            "taux_tfb": nombre(rec.get("taux_global_tfb"), 0), "taux_teom": nombre(rec.get("taux_plein_teom"), 0),
            "source": "DGFiP – Fiscalité locale des particuliers (REI), data.economie.gouv.fr"}


def calcul_taxe_fonciere(vlc, taux_tfb, taux_teom):
    """Base d'imposition = 50 % de la valeur locative cadastrale (art. 1388 et 1522 du CGI),
    majorée des frais de gestion de l'État : 3 % (taxe foncière) et 8 % (TEOM) (art. 1641 du CGI)."""
    base = max(vlc, 0) * 0.5
    return {"base": base, "tfpb": base * taux_tfb / 100 * 1.03, "teom": base * taux_teom / 100 * 1.08}


DEFAULTS = dict(
    prix=302000, apport=50000, date_achat="2027-01-01", date_liv="2029-07-01", fonds="vefa",
    taux=4.0, duree=18, ass=0.25,
    ptz=54000, ptz_dif=0, ptz_dur=10,
    al=30000, taux_al=1.0, duree_al=18,
    etat="neuf", frais_dossier=1000, garantie=1.0, courtage=0,
    tf=900, teom=120, tf_exo=40,
)

# Frais de notaire (service-public.fr F17701, vérifié le 05/08/2026) : émoluments proportionnels
# du notaire (barème réglementé, TVA 20 %), droits d'enregistrement / taxe de publicité foncière,
# contribution de sécurité immobilière (0,10 %, 15 € min.) et forfait débours et formalités.
TVA = 0.20
BAREME_EMOLUMENTS = [(6500, 0.03870), (17000, 0.01596), (60000, 0.01064), (math.inf, 0.00799)]
TAUX_DROITS = {"neuf": 0.00715, "ancien": 0.0580665}  # TPF sur le prix HT (VEFA) / DMTO sur le prix
TAUX_CSI, CSI_MIN, DEBOURS = 0.001, 15, 1200


def emoluments(base):
    """Émoluments proportionnels du notaire (hors TVA) pour une vente de prix « base »."""
    total, bas = 0.0, 0.0
    for haut, taux in BAREME_EMOLUMENTS:
        if base > bas:
            total += (min(base, haut) - bas) * taux
        bas = haut
    return total


def frais_notaire(prix, etat):
    """Décomposition des frais d'acquisition. En VEFA la base est le prix hors TVA (20 %)."""
    if prix <= 0:
        return dict(emoluments=0.0, tva=0.0, droits=0.0, csi=0.0, debours=0.0, total=0.0)
    base = prix / (1 + TVA) if etat == "neuf" else prix
    em = emoluments(base)
    d = dict(emoluments=em, tva=em * TVA, droits=base * TAUX_DROITS[etat],
             csi=max(CSI_MIN, prix * TAUX_CSI), debours=DEBOURS)
    d["total"] = sum(d.values())
    return d


def frais_initiaux(p):
    """Frais payés à la signature. La garantie (% de prêt principal + PTZ) se calcule sur un capital
    qui l'inclut lui-même si elle est financée : G = g × (B + G), soit G = g × B / (1 − g)."""
    notaire = frais_notaire(p["prix"], p["etat"])["total"]
    g = p["garantie"] / 100
    b = p["prix"] + notaire + p["frais_dossier"] + p["courtage"] - p["apport"] - p["al"]
    garantie = g * b / (1 - g) if b > 0 else 0.0
    return dict(notaire=notaire, dossier=p["frais_dossier"], garantie=garantie, courtage=p["courtage"],
                total=notaire + p["frais_dossier"] + garantie + p["courtage"])


def champ(id, label, mini, maxi, pas, unite):
    return dict(id=id, label=label, type="number", min=mini, max=maxi, step=pas, unit=unite)


# Source unique des paramètres : le formulaire HTML est généré depuis cette liste.
GROUPES = [
    ("Projet", [
        champ("prix", "Prix du bien", 100000, 800000, 1000, "€"),
        champ("apport", "Apport", 0, 300000, 1000, "€"),
        dict(id="date_achat", label="Date d'achat", type="date"),
        dict(id="date_liv", label="Date de livraison", type="date"),
        dict(id="fonds", label="Appels de fonds", type="select", options=[
            ("vefa", "VEFA standard (5/35/70/95 %)"),
            ("lin", "Linéaire jusqu'à la livraison"),
            ("tout", "Tout débloqué dès l'achat")]),
    ]),
    ("Prêt principal", [
        champ("taux", "Taux d'intérêt", 0, 8, 0.05, "%"),
        champ("duree", "Durée après livraison", 5, 30, 1, "ans"),
        champ("ass", "Assurance (par an)", 0, 1, 0.01, "%"),
    ]),
    ("PTZ", [
        champ("ptz", "Montant PTZ", 0, 150000, 500, "€"),
        champ("ptz_dif", "Différé", 0, 15, 1, "ans"),
        champ("ptz_dur", "Durée totale (différé inclus)", 5, 25, 1, "ans"),
    ]),
    ("Prêt Action Logement", [
        champ("al", "Montant", 0, 40000, 500, "€"),
        champ("taux_al", "Taux", 0, 3, 0.05, "%"),
        champ("duree_al", "Durée après livraison", 5, 25, 1, "ans"),
    ]),
    ("Frais d'acquisition et de crédit", [
        dict(id="etat", label="Type de bien (frais de notaire)", type="select", options=[
            ("neuf", "Neuf / VEFA (TVA 20 %)"), ("ancien", "Ancien")]),
        champ("frais_dossier", "Frais de dossier bancaire", 0, 5000, 50, "€"),
        champ("garantie", "Garantie (caution ou hypothèque)", 0, 3, 0.05, "%"),
        champ("courtage", "Frais de courtage", 0, 10000, 100, "€"),
    ]),
    ("Taxe foncière (après livraison)", [
        champ("tf", "Taxe foncière bâtie, année pleine", 0, 10000, 10, "€"),
        champ("teom", "Taxe d'ordures ménagères (TEOM)", 0, 3000, 10, "€"),
        champ("tf_exo", "Exonération neuf les 2 premières années", 0, 100, 10, "%"),
    ]),
]


# ---------------------------------------------------------------- calcul
def pmt(capital, taux_mensuel, n):
    if capital <= 0 or n <= 0:
        return 0.0
    if taux_mensuel == 0:
        return capital / n
    return capital * taux_mensuel / (1 - (1 + taux_mensuel) ** -n)


def mois_entre(a, b):
    ya, ma, _ = map(int, a.split("-"))
    yb, mb, _ = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def nettoyer(brut):
    """Force des valeurs valides : un paramètre absent ou illisible reprend sa valeur par défaut."""
    p = {}
    for k, defaut in DEFAULTS.items():
        v = brut.get(k, defaut)
        p[k] = v if isinstance(defaut, str) else nombre(v, defaut)
    for k in ("date_achat", "date_liv"):
        try:
            mois_entre(p[k], p[k])
        except (AttributeError, ValueError):
            p[k] = DEFAULTS[k]
    if p["fonds"] not in ("vefa", "lin", "tout"):
        p["fonds"] = "vefa"
    if p["etat"] not in TAUX_DROITS:
        p["etat"] = "neuf"
    for k, defaut in DEFAULTS.items():  # aucun montant, taux ou durée négatif
        if not isinstance(defaut, str):
            p[k] = max(0.0, p[k])
    p["garantie"] = min(p["garantie"], 50.0)
    p["tf_exo"] = min(p["tf_exo"], 100.0)
    return p


def part_appelee(fonds, m, nb):
    if fonds == "tout":
        return 1.0
    if fonds == "lin":
        return m / nb
    pct = 0.0
    for mois, palier in APPELS_VEFA:
        if mois < m:
            pct = palier
    return pct


def simuler(p):
    nb = max(0, mois_entre(p["date_achat"], p["date_liv"]))
    frais = frais_initiaux(p)["total"]  # payés à la signature, d'abord par l'apport
    princ = p["prix"] + frais - p["apport"] - p["ptz"] - p["al"]
    total = princ + p["ptz"] + p["al"]
    r, r_al, ia = p["taux"] / 1200, p["taux_al"] / 1200, p["ass"] / 1200
    m_princ = pmt(princ, r, p["duree"] * 12)
    m_al = pmt(p["al"], r_al, p["duree_al"] * 12)
    m_ptz = p["ptz"] / max(1, (p["ptz_dur"] - p["ptz_dif"]) * 12)
    rows = []

    # Phase 1 : chantier (intérêts + assurance sur les fonds débloqués)
    for m in range(1, nb + 1):
        du = p["prix"] * part_appelee(p["fonds"], m, nb) + frais
        debloque = min(max(du - p["apport"], 0), total)
        part = debloque / total if total > 0 else 0
        rows.append(dict(m=m, phase=1, interet=princ * part * r + p["al"] * part * r_al,
                         princ=0, al=0, ptz=0, ass=debloque * ia,
                         balP=princ * part, balAl=p["al"] * part, balPtz=p["ptz"] * part))

    # Phase 2 : remboursement à mensualité constante après livraison
    horizon = int(max(p["duree"], p["duree_al"], p["ptz_dur"]) * 12)
    fin_ass = max(p["duree"], p["duree_al"]) * 12
    b_p, b_al, b_ptz = princ, p["al"], p["ptz"]
    for k in range(1, horizon + 1):
        i_p = c_p = i_a = c_a = c_z = 0.0
        if k <= p["duree"] * 12 and b_p > 0.005:
            i_p = b_p * r
            c_p = min(m_princ - i_p, b_p)
            b_p -= c_p
        if k <= p["duree_al"] * 12 and b_al > 0.005:
            i_a = b_al * r_al
            c_a = min(m_al - i_a, b_al)
            b_al -= c_a
        if p["ptz_dif"] * 12 < k <= p["ptz_dur"] * 12 and b_ptz > 0.005:
            c_z = min(m_ptz, b_ptz)
            b_ptz -= c_z
        rows.append(dict(m=nb + k, phase=2, interet=i_p + i_a, princ=c_p, al=c_a, ptz=c_z,
                         ass=total * ia if k <= fin_ass else 0,
                         balP=b_p, balAl=b_al, balPtz=b_ptz))

    for x in rows:
        x["total"] = x["interet"] + x["princ"] + x["al"] + x["ptz"] + x["ass"]
        x["bal"] = x["balP"] + x["balAl"] + x["balPtz"]
    return nb, princ, total, rows


def libelle_periode(k, p):
    """Ce qui change au mois k après la livraison (k = 1 : premier mois de remboursement)."""
    if k == 1:
        return "Livraison : début des remboursements"
    evts = []
    if p["ptz_dif"] > 0 and k == p["ptz_dif"] * 12 + 1:
        evts.append("début du remboursement du PTZ")
    if k == p["ptz_dur"] * 12 + 1:
        evts.append("fin du PTZ")
    if k == p["duree_al"] * 12 + 1:
        evts.append("fin du prêt Action Logement")
    if k == p["duree"] * 12 + 1:
        evts.append("fin du prêt principal")
    txt = ", ".join(evts)
    return txt[0].upper() + txt[1:] if txt else "Mensualité modifiée"


def periodes(rows, nb, p):
    """Regroupe les mois consécutifs dont la mensualité réelle est identique (à 1 € près)."""
    segs = []
    for i, x in enumerate(rows):
        t = x["total"]
        s = segs[-1] if segs else None
        if s and s["phase"] == x["phase"] and abs(t - s["dernier"]) < 1:
            s.update(fin=i, dernier=t, mini=min(s["mini"], t), maxi=max(s["maxi"], t))
        else:
            segs.append(dict(phase=x["phase"], debut=i, fin=i, dernier=t, mini=t, maxi=t,
                             label="Chantier" if x["phase"] == 1 else libelle_periode(x["m"] - nb, p)))
    chantier = [s for s in segs if s["phase"] == 1]
    if len(chantier) > 6:  # appels de fonds très fréquents : une seule ligne
        un = dict(phase=1, debut=chantier[0]["debut"], fin=chantier[-1]["fin"], label="Chantier",
                  mini=min(s["mini"] for s in chantier), maxi=max(s["maxi"] for s in chantier))
        segs = [un] + [s for s in segs if s["phase"] == 2]
    elif len(chantier) > 1:
        for j, s in enumerate(chantier, 1):
            s["label"] = f"Chantier – palier {j}"
    n = len(rows)
    return [dict(label=s["label"], phase=s["phase"], debut=s["debut"], mois=s["fin"] - s["debut"] + 1,
                 mini=round(s["mini"], 2), maxi=round(s["maxi"], 2), restant=n - s["debut"]) for s in segs]


def taeg(rows, nb, total, frais_credit):
    """TAEG actuariel (directive 2014/17/UE) de l'ensemble des prêts : taux mensuel i tel que
    les fonds débloqués actualisés égalent échéances + frais de crédit (dossier, garantie, courtage)
    actualisés ; TAEG = (1 + i)^12 − 1. Déblocage du mois m en début de mois, échéance en fin."""
    if total <= 0:
        return 0.0
    flux = [0.0] * (len(rows) + 1)
    deja = 0.0
    for m in range(1, nb + 1):
        deb = rows[m - 1]["balP"] + rows[m - 1]["balAl"] + rows[m - 1]["balPtz"]
        flux[m - 1] += deb - deja
        deja = deb
    flux[nb] += total - deja
    flux[0] -= frais_credit
    for j, x in enumerate(rows, 1):
        flux[j] -= x["total"]

    def van(i):
        v, f = 0.0, 1.0
        for c in flux:
            v += c * f
            f /= 1 + i
        return v
    bas, haut = -0.0099, 0.05
    if van(bas) > 0 or van(haut) < 0:
        return None
    for _ in range(80):
        mil = (bas + haut) / 2
        if van(mil) < 0:
            bas = mil
        else:
            haut = mil
    return ((1 + (bas + haut) / 2) ** 12 - 1) * 100


def taxe_fonciere_annee(p, annee):
    """Taxe foncière due au titre d'une année (propriétaire au 1er janvier). Neuf : due à partir du
    1er janvier suivant l'achèvement, TFPB exonérée de tf_exo % les 2 premières années, TEOM due."""
    y_liv = int(p["date_liv"][:4]) if p["etat"] == "neuf" else int(p["date_achat"][:4])
    if annee <= y_liv:
        return 0.0
    exo = p["tf_exo"] / 100 if p["etat"] == "neuf" and annee <= y_liv + 2 else 0.0
    return p["tf"] * (1 - exo) + p["teom"]


def synthese(p):
    nb, princ, total, rows = simuler(p)
    fi = frais_initiaux(p)
    taux_eff = taeg(rows, nb, total, fi["dossier"] + fi["garantie"] + fi["courtage"])
    phase2 = [x for x in rows if x["phase"] == 2]
    dernier_ptz = max((i for i, x in enumerate(rows) if x["ptz"] > 0), default=None)
    apres_ptz = (rows[dernier_ptz + 1]["total"]
                 if dernier_ptz is not None and dernier_ptz + 1 < len(rows) else None)
    interets = sum(x["interet"] for x in rows)
    assurance = sum(x["ass"] for x in rows)

    alertes = []
    if princ < 0:
        alertes.append("L'apport et les aides dépassent le prix : le prêt principal est négatif.")
    if p["ptz_dur"] <= p["ptz_dif"]:
        alertes.append("La durée totale du PTZ doit dépasser le différé.")
    if princ >= 0 and p["apport"] < fi["total"]:
        alertes.append(f"L'apport ne couvre pas les frais ({fi['total']:,.0f} €) : "
                       f"{fi['total'] - p['apport']:,.0f} € sont financés par le prêt, ce que "
                       "beaucoup de banques refusent.".replace(",", " "))
    if p["ptz"] > max(princ, 0) + p["al"]:
        alertes.append("Le PTZ ne peut pas dépasser le montant des autres prêts.")
    if p["date_liv"] < p["date_achat"]:
        alertes.append("La date de livraison est antérieure à la date d'achat.")

    while rows and rows[-1]["total"] <= 0.005:  # on coupe les mois sans échéance
        rows.pop()

    annuel = {}
    y0, m0, _ = map(int, p["date_achat"].split("-"))
    for x in rows:
        annuel.setdefault(y0 + (m0 - 1 + x["m"]) // 12, []).append(x)

    return dict(
        kpis=dict(
            mensualite_finale=phase2[0]["total"] if phase2 else 0,
            max_avant=max([0.0] + [x["total"] for x in rows if x["phase"] == 1]),
            apres_ptz=apres_ptz,
            pret_principal=max(princ, 0),
            total_emprunte=total,
            interets=interets, assurance=assurance,
            cout_credit=interets + assurance,
            total_rembourse=sum(x["total"] for x in rows),
            duree_mois=len(rows),
            mois_chantier=nb,
            frais_notaire=fi["notaire"], frais_dossier=fi["dossier"], frais_garantie=fi["garantie"],
            courtage=fi["courtage"], frais_initiaux=fi["total"],
            cout_total=p["prix"] + fi["total"] + interets + assurance,
            taeg=taux_eff,
            tf_pleine=p["tf"] + p["teom"],
            mensualite_avec_tf=(phase2[0]["total"] if phase2 else 0) + (p["tf"] + p["teom"]) / 12,
            **{f"mf_{k}": (phase2[0][k] if phase2 else 0)
               for k in ("princ", "al", "ptz", "interet", "ass")},
        ),
        mensualites=[round(x["total"], 2) for x in rows],
        capital=[round(x["bal"]) for x in rows],
        detail={k: [round(x[k], 2) for x in rows] for k in ("princ", "al", "ptz", "interet", "ass")},
        periodes=periodes(rows, nb, p),
        annuel=[dict(annee=y, mois=len(a),
                     mensualite_moy=sum(x["total"] for x in a) / len(a),
                     mini=min(x["total"] for x in a), maxi=max(x["total"] for x in a),
                     interets=sum(x["interet"] for x in a),
                     assurance=sum(x["ass"] for x in a),
                     total=sum(x["total"] for x in a),
                     taxe_fonciere=taxe_fonciere_annee(p, y),
                     capital_fin=a[-1]["bal"])
                for y, a in annuel.items()],
        alertes=alertes,
    )


# ---------------------------------------------------------------- capacité d'emprunt
# Décision HCSF n° D-HCSF-2021-7 (contraignante depuis le 1er janvier 2022) : taux d'effort
# ≤ 35 % des revenus nets avant impôt, assurance comprise ; durée ≤ 25 ans, 27 ans pour le neuf
# ou la VEFA (différé d'amortissement de 2 ans maximum pendant la construction).
SOURCE_HCSF = "Décision HCSF n° D-HCSF-2021-7 : taux d'effort ≤ 35 % assurance comprise, durée ≤ 25 ans (27 ans dans le neuf)"
TAUX_EFFORT_MAX = 0.35


def mensualites_aides(ptz, al, p):
    """Mensualité maximale (cumul sans lissage) due au PTZ et au prêt Action Logement, assurance incluse."""
    ia = p["ass"] / 1200
    m_ptz = ptz / max(1, (p["ptz_dur"] - p["ptz_dif"]) * 12) if ptz > 0 else 0.0
    m_al = pmt(al, p["taux_al"] / 1200, p["duree_al"] * 12)
    return m_ptz + m_al + (ptz + al) * ia


def capacite_emprunt(params):
    """Capacité d'emprunt du foyer : capital maximal du prêt principal tel que la somme des
    mensualités (tous prêts, assurance comprise) et des crédits en cours ≤ 35 % des revenus nets,
    puis prix maximal du logement tel que prix + frais = apport + prêts."""
    revenus = max(0.0, nombre(params.get("revenus"), 0))
    charges = max(0.0, nombre(params.get("charges"), 0))
    apport = max(0.0, nombre(params.get("apport"), 0))
    taux = min(max(0.0, nombre(params.get("taux"), 3.5)), 20.0)
    ass = min(max(0.0, nombre(params.get("ass"), 0.30)), 5.0)
    duree = int(min(max(nombre(params.get("duree"), 25), 5), 30))
    etat = params.get("etat") if params.get("etat") in TAUX_DROITS else "neuf"
    type_logement = "vefa" if etat == "neuf" else "ancien"
    zone = params.get("zone") if params.get("zone") in ZONES_PTZ else "B1"
    rfr = max(0.0, nombre(params.get("revenu_fiscal"), 0))
    nb = min(20, max(1, int(nombre(params.get("nb_personnes"), 1))))
    salarie = bool(params.get("salarie", False))
    p = dict(prix=0.0, apport=apport, etat=etat, al=0.0, ass=ass, taux_al=1.0, duree_al=min(25, duree),
             ptz_dur=0, ptz_dif=0, frais_dossier=max(0.0, nombre(params.get("frais_dossier"), 1000)),
             garantie=min(max(0.0, nombre(params.get("garantie"), 1.0)), 50.0), courtage=0.0)

    budget = revenus * TAUX_EFFORT_MAX - charges
    duree_max = 27 if etat == "neuf" else 25
    alertes = []
    if revenus <= 0:
        alertes.append("Indiquez les revenus nets mensuels du foyer (avant impôt).")
    if duree > duree_max:
        alertes.append(f"Durée de {duree} ans supérieure au maximum HCSF de {duree_max} ans.")
    r, ia, n = taux / 1200, ass / 1200, duree * 12
    facteur = pmt(1.0, r, n) + ia  # mensualité (assurance incluse) pour 1 € de prêt principal
    al_ok = salarie and verifier_eligibilite_al(rfr, zone, nb, salarie)[0]

    def plan(prix):
        """Financement d'un logement de prix « prix » : aides auxquelles le foyer a droit + prêt principal."""
        q = dict(p, prix=prix)
        ptz_info = calculer_ptz(prix, type_logement, zone, rfr, nb, apport)
        ptz = float(ptz_info.get("montant_max", 0)) if ptz_info["eligible"] else 0.0
        if ptz:
            q["ptz_dur"], q["ptz_dif"] = ptz_info["duree_max"], ptz_info["differe"]
        al = float(min(30000, prix)) if al_ok else 0.0
        q["al"] = al
        fi = frais_initiaux(q)
        besoin = max(prix + fi["total"] - apport - ptz - al, 0.0)
        mensualite = besoin * facteur + mensualites_aides(ptz, al, q)
        return dict(prix=prix, ptz=ptz, al=al, principal=besoin, frais=fi, mensualite=mensualite,
                    ptz_dur=q["ptz_dur"], ptz_dif=q["ptz_dif"])

    if budget <= 0:
        prix_max, best = 0.0, plan(0.0)
        if revenus > 0:
            alertes.append("Les crédits en cours atteignent déjà 35 % des revenus : aucune capacité d'emprunt.")
    else:
        # La mensualité nécessaire croît avec le prix : recherche dichotomique du prix maximal finançable.
        bas, haut = 0.0, 1.0
        while plan(haut)["mensualite"] <= budget and haut < 1e8:
            bas, haut = haut, haut * 2
        for _ in range(60):
            mil = (bas + haut) / 2
            if plan(mil)["mensualite"] <= budget:
                bas = mil
            else:
                haut = mil
        prix_max = math.floor(bas)
        best = plan(prix_max)
    res = dict(
        revenus=revenus, charges=charges, budget_mensuel=max(budget, 0.0), taux_effort_max=TAUX_EFFORT_MAX * 100,
        duree_max=duree_max, prix_max=prix_max, pret_principal=best["principal"], ptz=best["ptz"], al=best["al"],
        ptz_duree=best["ptz_dur"], ptz_differe=best["ptz_dif"],
        total_emprunte=best["principal"] + best["ptz"] + best["al"], apport=apport,
        frais_notaire=best["frais"]["notaire"], frais_garantie=best["frais"]["garantie"],
        frais_dossier=best["frais"]["dossier"], frais_total=best["frais"]["total"],
        mensualite=best["mensualite"], taux_effort=(best["mensualite"] + charges) / revenus * 100 if revenus else 0.0,
        reste_a_vivre=revenus - charges - best["mensualite"], nb_personnes=nb,
        source=SOURCE_HCSF, alertes=alertes)
    prix_vise = max(0.0, nombre(params.get("prix_vise"), 0))
    if prix_vise > 0:
        v = plan(prix_vise)
        res["projet"] = dict(prix=prix_vise, pret_principal=v["principal"], ptz=v["ptz"], al=v["al"],
                             frais_total=v["frais"]["total"], mensualite=v["mensualite"],
                             taux_effort=(v["mensualite"] + charges) / revenus * 100 if revenus else None,
                             financable=revenus > 0 and v["mensualite"] + charges <= revenus * TAUX_EFFORT_MAX + 0.005)
    return res


# ---------------------------------------------------------------- routes
def corps_json():
    corps = request.get_json(silent=True)
    return corps if isinstance(corps, dict) else {}


@app.get("/")
def index():
    return render_template("index.html", groupes=GROUPES, defaults=DEFAULTS, lien_limites=True)


@app.get("/limites")
def limites():
    return render_template("limites.html")


@app.get("/eligibilite")
def eligibilite():
    return render_template("eligibilite.html")


@app.get("/analyse")
def analyse():
    return render_template("analyse.html")


@app.post("/api/simulate")
def api_simulate():
    corps = request.get_json(silent=True)
    scenarios = corps.get("scenarios") if isinstance(corps, dict) else None
    if not isinstance(scenarios, list):
        scenarios = []
    return jsonify([synthese(nettoyer(s if isinstance(s, dict) else {})) for s in scenarios[:4]])


@app.post("/api/eligibilite")
def api_eligibilite():
    """API pour tester l'éligibilité aux aides."""
    params = corps_json()
    return jsonify(eligibilite_aides(params))


@app.post("/api/capacite")
def api_capacite():
    """Capacité d'emprunt du foyer selon les règles du HCSF, aides comprises."""
    return jsonify(capacite_emprunt(corps_json()))


@app.post("/api/comparer")
def api_comparer():
    """API pour comparer location vs achat."""
    params = corps_json()
    return jsonify(comparer_location_achat(params))


@app.post("/api/geocoder")
def api_geocoder():
    """API pour géocoder une adresse."""
    params = corps_json()
    adresse = str(params.get("adresse") or "").strip()
    if not adresse:
        return jsonify({"success": False, "error": "Adresse vide"})
    return jsonify(geocoder_adresse(adresse))


@app.post("/api/poi")
def api_poi():
    """API pour rechercher les POI autour d'une position."""
    params = corps_json()
    lat = params.get("lat")
    lon = params.get("lon")
    rayon = params.get("rayon", 500)
    return jsonify(rechercher_poi(lat, lon, rayon))


@app.post("/api/rating")
def api_rating():
    """API pour calculer le rating du quartier."""
    params = corps_json()
    poi = params.get("poi")
    poi = [p for p in poi if isinstance(p, dict)] if isinstance(poi, list) else []
    return jsonify(calculer_rating_quartier(poi))


@app.post("/api/zone")
def api_zone():
    """Zone ABC officielle par code INSEE (prioritaire) ou code postal."""
    params = corps_json()
    return jsonify(determiner_zone(params.get("code_insee"), params.get("code_postal")))


@app.post("/api/fiscalite")
def api_fiscalite():
    """Taux de taxe foncière de la commune et, si la valeur locative est fournie, le montant."""
    params = corps_json()
    res = fiscalite_locale(params.get("code_insee"))
    vlc = nombre(params.get("vlc"), 0)
    if res["success"] and vlc > 0:
        res.update(calcul_taxe_fonciere(vlc, res["taux_tfb"], res["taux_teom"]))
    return jsonify(res)


@app.errorhandler(Exception)
def erreur_interne(e):
    """Jamais de page d'erreur brute : message lisible en JSON pour l'interface."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"success": False, "error": e.description}), e.code
    app.logger.exception(e)
    return jsonify({"success": False, "error": "Erreur inattendue, merci de réessayer."}), 500


if __name__ == "__main__":
    # Port 5050 : le 5000 est déjà pris par « Récepteur AirPlay » sur les Mac récents.
    # Pour le déploiement cloud (Render), utilise les variables d'environnement.
    import os
    port = int(os.environ.get("PORT", 5050))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
