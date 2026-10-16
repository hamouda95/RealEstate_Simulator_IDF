"""Comparateur de scénarios de prêt immobilier en VEFA.

Lancer :  pip install -r requirements.txt && python app.py
Ouvrir :  http://127.0.0.1:5050
"""
import math

from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)

# Appels de fonds VEFA standard : (mois écoulé après lequel le palier s'applique, % du prix appelé)
APPELS_VEFA = [(0, 0.05), (3, 0.35), (9, 0.70), (18, 0.95)]

# ---------------------------------------------------------------- données officielles aides 2024
# Plafonds de ressources PTZ 2024 (revenu fiscal de référence N-2)
PLAFONDS_PTZ = {
    "A": {1: 49000, 2: 73500, 3: 88200, 4: 102900, 5: 117600, 6: 132300, 7: 147000, 8: 161700},
    "Abis": {1: 49000, 2: 73500, 3: 88200, 4: 102900, 5: 117600, 6: 132300, 7: 147000, 8: 161700},
    "B1": {1: 34500, 2: 51750, 3: 62100, 4: 72450, 5: 82800, 6: 93150, 7: 103500, 8: 113850},
    "B2": {1: 31500, 2: 47250, 3: 56700, 4: 66150, 5: 75600, 6: 85050, 7: 94500, 8: 103950},
    "C": {1: 28500, 2: 42750, 3: 51300, 4: 59850, 5: 68400, 6: 76950, 7: 85500, 8: 94050},
}

# Tranches PTZ et quotité finançable (2024-2027)
TRANCHES_PTZ = [
    {"max_ratio": 0.5, "quotite": 0.50, "duree": 25},  # Tranche 1: 50% finançable, 25 ans
    {"max_ratio": 0.7, "quotite": 0.40, "duree": 22},  # Tranche 2: 40% finançable, 22 ans
    {"max_ratio": 0.9, "quotite": 0.30, "duree": 20},  # Tranche 3: 30% finançable, 20 ans
    {"max_ratio": 1.0, "quotite": 0.20, "duree": 17},  # Tranche 4: 20% finançable, 17 ans
]

# Montants max PTZ selon type d'opération et zone (2024-2027)
MONTANT_MAX_PTZ = {
    "neuf_collectif": {"A": 360000, "Abis": 360000, "B1": 360000, "B2": 0, "C": 0},
    "neuf_individuel": {"A": 360000, "Abis": 360000, "B1": 360000, "B2": 360000, "C": 360000},
    "ancien_travaux": {"A": 0, "Abis": 0, "B1": 0, "B2": 360000, "C": 360000},
}

# Plafonds Action Logement 2024 (revenu fiscal de référence N-2)
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


def determiner_tranche_ptz(revenu, plafond_zone):
    """Détermine la tranche PTZ selon le revenu par rapport au plafond de zone."""
    ratio = revenu / plafond_zone if plafond_zone > 0 else 1.0
    for tranche in TRANCHES_PTZ:
        if ratio <= tranche["max_ratio"]:
            return tranche
    return TRANCHES_PTZ[-1]

def calculer_montant_ptz_max(prix, type_operation, zone, revenu, nb_personnes):
    """Calcule le montant maximal de PTZ éligible selon les règles 2024-2027."""
    # Déterminer le type d'opération pour le PTZ
    if type_operation == "vefa":
        type_ptz = "neuf_collectif"
    elif type_operation == "neuf":
        type_ptz = "neuf_individuel"
    elif type_operation == "ancien":
        type_ptz = "ancien_travaux"
    else:
        type_ptz = "neuf_individuel"

    # Vérifier éligibilité zone selon type d'opération
    montant_max_zone = MONTANT_MAX_PTZ.get(type_ptz, {}).get(zone, 0)
    if montant_max_zone == 0:
        return {"eligible": False, "raison": f"Type {type_operation} non éligible en zone {zone}"}

    # Récupérer plafond selon zone et nombre de personnes
    plafond = PLAFONDS_PTZ.get(zone, {}).get(min(nb_personnes, 8), 0)
    if revenu > plafond:
        return {"eligible": False, "raison": f"Revenu {revenu}€ dépasse le plafond {plafond}€"}

    # Déterminer tranche et quotité
    tranche = determiner_tranche_ptz(revenu, plafond)
    quotite = tranche["quotite"]
    duree_max = tranche["duree"]

    # Calculer montant selon quotité
    montant_quotite = prix * quotite

    # Appliquer le montant max selon zone
    montant_final = min(montant_quotite, montant_max_zone)

    # Arrondir à l'euro inférieur
    montant_final = int(montant_final)

    return {
        "eligible": True,
        "montant_max": montant_final,
        "quotite": int(quotite * 100),
        "duree_max": duree_max,
        "tranche": TRANCHES_PTZ.index(tranche) + 1,
        "plafond_zone": plafond,
        "ratio_revenu": round(revenu / plafond, 2),
    }

def verifier_eligibilite_al(revenu, zone, nb_personnes, salarie):
    """Vérifie l'éligibilité au prêt Action Logement."""
    if not salarie:
        return False, "Non salarié d'entreprise éligible"

    plafond = PLAFONDS_AL.get(zone, {}).get(min(nb_personnes, 6), 0)
    if nb_personnes > 6:
        plafond += PLAFONDS_AL.get(zone, {}).get("sup", 0) * (nb_personnes - 6)

    if revenu > plafond:
        return False, f"Revenu {revenu}€ dépasse le plafond {plafond}€"

    return True, "Éligible"

def calculer_montant_al_max(cout_operation):
    """Calcule le montant maximal du prêt Action Logement."""
    return min(30000, int(cout_operation * 0.4))

def eligibilite_aides(params):
    """Calcule l'éligibilité à toutes les aides selon les paramètres."""
    revenu = nombre(params.get("revenu_fiscal"), 0)
    nb_personnes = max(1, int(nombre(params.get("nb_personnes"), 1)))
    zone = params.get("zone", "B1")
    if zone not in PLAFONDS_PTZ:
        zone = "B1"
    type_logement = params.get("type_logement", "neuf")  # neuf, ancien, vefa
    salarie = bool(params.get("salarie", False))
    prix = nombre(params.get("prix"), 0)

    aides = {}

    # PTZ
    resultat_ptz = calculer_montant_ptz_max(prix, type_logement, zone, revenu, nb_personnes)
    if resultat_ptz["eligible"]:
        aides["PTZ"] = {
            "eligible": True,
            "montant_max": resultat_ptz["montant_max"],
            "quotite": resultat_ptz["quotite"],
            "duree_max": resultat_ptz["duree_max"],
            "tranche": resultat_ptz["tranche"],
            "zone": zone,
            "plafond_zone": resultat_ptz["plafond_zone"],
            "ratio_revenu": resultat_ptz["ratio_revenu"],
        }
        if type_logement == "ancien":
            aides["PTZ"]["condition"] = "Avec travaux énergétiques (min 25% du coût)"
    else:
        aides["PTZ"] = {"eligible": False, "raison": resultat_ptz["raison"]}

    # Action Logement
    eligible_al, raison_al = verifier_eligibilite_al(revenu, zone, nb_personnes, salarie)
    if eligible_al:
        montant_al = calculer_montant_al_max(prix)
        aides["Action Logement"] = {
            "eligible": True,
            "montant_max": montant_al,
            "taux": 1.0,
            "duree_max": 25,
        }
    else:
        aides["Action Logement"] = {"eligible": False, "raison": raison_al}

    # PAS (Prêt d'Accession Sociale) - simplifié
    # Plafonds PAS similaires à PTZ mais plus bas
    plafond_pas = PLAFONDS_PTZ.get(zone, {}).get(min(nb_personnes, 8), 0) * 0.9
    if revenu <= plafond_pas:
        aides["PAS"] = {
            "eligible": True,
            "avantages": ["Financement à 100% possible", "Durée 5-30 ans", "Réduction frais notaire"],
        }
    else:
        aides["PAS"] = {"eligible": False, "raison": "Revenu trop élevé"}

    # PSLA (Location-Accession)
    if type_logement in ["neuf", "vefa"]:
        aides["PSLA"] = {
            "eligible": True,
            "avantages": ["TVA réduite (5.5%)", "Exonération taxe foncière 15 ans", "Minoration prix 1%/an"],
        }
    else:
        aides["PSLA"] = {"eligible": False, "raison": "Réservé au neuf"}

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
def geocoder_adresse(adresse):
    """Géocode une adresse via OpenStreetMap Nominatim (gratuit)."""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": adresse,
            "format": "json",
            "limit": 1,
            "countrycodes": "fr",
        }
        headers = {
            "User-Agent": "VEFA-Dashboard/1.0 (immobilier-simulation)"
        }
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data and len(data) > 0:
            return {
                "success": True,
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "display_name": data[0]["display_name"],
            }
        else:
            return {"success": False, "error": "Adresse non trouvée"}
    except Exception as e:
        return {"success": False, "error": str(e)}


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


def determiner_zone_par_code_postal(code_postal):
    """Détermine la zone géographique (A/A bis/B1/B2/C) selon le code postal."""
    # Zonage plus précis pour l'Île-de-France
    zones_par_code_postal = {
        # Paris (75) - Zone A bis
        "75001": "Abis", "75002": "Abis", "75003": "Abis", "75004": "Abis", "75005": "Abis",
        "75006": "Abis", "75007": "Abis", "75008": "Abis", "75009": "Abis", "75010": "Abis",
        "75011": "Abis", "75012": "Abis", "75013": "Abis", "75014": "Abis", "75015": "Abis",
        "75016": "Abis", "75017": "Abis", "75018": "Abis", "75019": "Abis", "75020": "Abis",
        # Petite couronne - Zone A bis (communes limitrophes de Paris et Arcueil)
        "92000": "Abis", "92100": "Abis", "92200": "Abis", "92300": "Abis", "92400": "Abis",
        "93000": "Abis", "93100": "Abis", "93200": "Abis", "93300": "Abis", "93400": "Abis",
        "93500": "Abis", "94000": "Abis", "94100": "Abis", "94200": "Abis", "94220": "Abis",  # Arcueil
        "94300": "Abis", "94400": "Abis", "94500": "Abis", "94600": "Abis", "94700": "Abis", "94800": "Abis",
        # Autres communes IDF - Zone A
        "75": "Abis", "77": "A", "78": "A", "91": "A", "92": "A", "93": "A", "94": "A", "95": "A",
        # Grandes métropoles - Zone A
        "06": "A", "13": "A", "69": "A", "59": "A", "31": "A", "33": "A", "44": "A",
        # Autres - Zone B1
        "default": "B1",
    }

    code_postal = str(code_postal or "").strip()
    # Vérifier d'abord le code postal complet
    if code_postal in zones_par_code_postal:
        return zones_par_code_postal[code_postal]

    # Sinon, vérifier par département
    dept = code_postal[:2] if len(code_postal) >= 2 else code_postal
    if dept in zones_par_code_postal:
        return zones_par_code_postal[dept]

    return "B2"  # Défaut B2

DEFAULTS = dict(
    prix=302000, apport=50000, date_achat="2027-01-01", date_liv="2029-07-01", fonds="vefa",
    taux=4.0, duree=18, ass=0.25,
    ptz=54000, ptz_dif=0, ptz_dur=10,
    al=30000, taux_al=1.0, duree_al=18,
)


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
    princ = p["prix"] - p["apport"] - p["ptz"] - p["al"]
    total = princ + p["ptz"] + p["al"]
    r, r_al, ia = p["taux"] / 1200, p["taux_al"] / 1200, p["ass"] / 1200
    m_princ = pmt(princ, r, p["duree"] * 12)
    m_al = pmt(p["al"], r_al, p["duree_al"] * 12)
    m_ptz = p["ptz"] / max(1, (p["ptz_dur"] - p["ptz_dif"]) * 12)
    rows = []

    # Phase 1 : chantier (intérêts + assurance sur les fonds débloqués)
    for m in range(1, nb + 1):
        du = p["prix"] * part_appelee(p["fonds"], m, nb)
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


def synthese(p):
    nb, princ, total, rows = simuler(p)
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
                     capital_fin=a[-1]["bal"])
                for y, a in annuel.items()],
        alertes=alertes,
    )


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
    """API pour déterminer la zone géographique par code postal."""
    params = corps_json()
    code_postal = params.get("code_postal", "")
    return jsonify({"zone": determiner_zone_par_code_postal(code_postal)})


if __name__ == "__main__":
    # Port 5050 : le 5000 est déjà pris par « Récepteur AirPlay » sur les Mac récents.
    # Pour le déploiement cloud (Render), utilise les variables d'environnement.
    import os
    port = int(os.environ.get("PORT", 5050))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
