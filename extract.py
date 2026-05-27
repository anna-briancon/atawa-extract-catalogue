import sys
import os
import json
import base64
import urllib.request
import urllib.error
import getpass
import random
import time
import gc
from pathlib import Path
import fitz  # PyMuPDF
import cv2
import numpy as np
from ultralytics import YOLO

def venv_py(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def find_venv() -> Path | None:
    """
    Premier interpréteur de venv utilisable, dans l'ordre :
    - EXTRACT_CATALOGUE_VENV : racine du venv, ou chemin direct vers python(.exe)
    - .venv puis venv à la racine du projet
    """
    here = Path(__file__).resolve().parent
    roots: list[Path] = []
    env = os.environ.get("EXTRACT_CATALOGUE_VENV", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and p.name.lower().startswith("python"):
            return p
        roots.append(p)
    roots.extend([here / ".venv", here / "venv"])
    for root in roots:
        cand = venv_py(root)
        if cand.is_file():
            return cand
    return None


def ensure_venv():
    """Relance ce script avec le venv si on ne l'utilise pas déjà."""
    venv_python = find_venv()
    if venv_python is None:
        return
    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return
    except OSError:
        return
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)


ensure_venv()

# region PROMPT
PRODUCT_SCHEMA = {
    "produits": [
        {
            "enseigne": "string | null",
            "date_debut": "string | null",
            "date_fin": "string | null",
            "rayon": "string | null",
            "nom_produit": "string | null",
            "prix": "number | null",
            "conditionnement": "string | null",
            "description": "string | null",
            "prix_unitaire": "number | null",
            "promo_type": "string | null",
            "promo_valeur": "string | null",
            "promo_description": "string | null",
            "_page": "integer | null"
        }
    ]
}

PROMO_TYPES_AUTORISES = [
    "w_dynamique_euro_offert_bon_utile",
    "w_dynamique_x_pourcent_remise_immediate",
    "w_dynamique_pourcentage_economie",
    "urgence_pouvoir_achat",
    "w_dynamique_x_pourcent_deconomies",
    "w_dynamique_x_euro_de_remise_immediate",
    "w_dynamique_remise_immediate_pourcentage+++x_eme_produit",
    "w_dynamique_x_achete+++x_offert",
    "w_dynamique_le_xe_+++a_x_pourcent",
    "w_dynamique_pourcentage_carte_u",
    "w_dynamique_pourcentage_remise_immediate_piece",
    "w_dynamique_pourcentage_rembourse_bons_dachat",
    "w_dynamique_remise_immediate_euro+++x_eme_produit",
    "w_dynamique_euro_simple",
    "w_dynamique_remise_immediate_carte_u",
    "prix_coutant_u",
    "w_dynamique_X+++X_offert",
    "defi_anti_inflation",
    "w_dynamique_X+++X_euro",
    "w_dynamique_pourcentage_carte",
    "w_dynamique_remise_dynamique",
    "w_dynamique_pourcentage_offert",
    "w_dynamique_X",
    "w_dynamique_pourcentage",
    "w_dynamique_euro_carte",
    "w_dynamique_euro_offert_bon_achat",
    "w_dynamique_euro_remise_immediate",
    "su_prix_bas",
    "promo",
    "bon_plan",
    "w_dynamique_pourcentage_2eme_produit",
    "w_dynamique_avantage_carte_pourcentage",
    "w_dynamique_a_partir_de",
    "w_dynamique_avantage_carte_euro",
    "w_dynamique_remise_immediate_pourcentage",
    "w_dynamique_carte_fid_euro",
    "w_dynamique_remise_immediate_euro",
    "w_dynamique_carte_fid_pourcentage",
    "w_dynamique_pourcentage_le_2eme",
    "w_dynamique_remise_immediate",
    "w_dynamique_X_plus+++X_offert",
    "w_dynamique_avantage_carte",
    "20ans",
    "priximbattable",
    "offrejour",
    "bonplanleclerc",
    "2plus1",
    "prixchoc",
    "quantiteslimitees",
    "toppromo",
    "3pour2",
    "prixflash",
    "w_dynamique_X_acheté+++X_offert",
    "offensive",
    "alertediscount",
    "destockage",
    "100percentcaseras",
    "LECToulonSelectionjour",
    "troispourdeux",
    "epuisementstock",
    "w_dynamique_ticketpromo",
    "bombazo",
    "venteflash",
    "w_dynamique_Avantage_carte",
    "w_dynamique_Point_Bonus",
    "pastille40j",
    "pastille25",
    "pastille10",
    "pastille10j",
    "pastillec50",
    "pastillec50j",
    "pastilleri50j",
    "maxieconomie",
    "pastilleri50b",
    "pastilleri50l",
    "pastille40",
    "pastilleri50k",
    "moinscheres",
    "w_dynamique_Vente_Unique",
    "w_dynamique_Prix_Barré",
    "petitprix",
    "reductionmoitieprix",
    "w_dynamique_Remise_ZL",
    "w_dynamique_Remise_Pourcentage",
    "prixcoutant",
    "prixbaisse",
    "prixleplusbas",
    "cretifiemoinscher",
    "quantitelimitee",
    "1prix",
    "prixpromo",
    "prixanniversaire",
    "produitstar",
    "pouvoir",
    "prixbas",
    "moinscher"
]

PROMPT = f"""Tu extrais les produits d’un catalogue PDF complet.

Retour obligatoire :
- enseigne
- date_debut
- date_fin
- rayon
- nom_produit
- prix
- conditionnement
- description
- prix_unitaire
- promo_type
- promo_valeur
- promo_description
- _page

Format de sortie obligatoire :
- Retourne obligatoirement un objet JSON racine
- Ne retourne jamais directement une liste JSON
- Format exact :
{{"produits": [...]}}

Règle absolue de fiabilité :
- Mieux vaut mettre null qu’inventer ou recopier un champ ambigu
- Ne jamais conserver dans les champs finaux un texte qui appartient à une autre règle de sortie
- Chaque champ doit être nettoyé AVANT retour JSON

Règles globales :
- L’enseigne, la date_debut et la date_fin sont à récupérer sur la première page et à répéter sur chaque produit
- date_debut et date_fin doivent être au format YYYY-MM-DD si possible, sinon null

Règle d’exclusion absolue :
- Ne jamais extraire un produit si le bloc contient un renvoi du type :
  "Vendu en page X", "Voir page X", "Retrouvez en page X", "En page X"
- Un teaser, une couverture ou un renvoi ne doivent jamais produire une ligne produit
- Seule la vraie page détaillée du produit doit être extraite

Règles sur les champs :
- "conditionnement" = uniquement le libellé de vente collé au prix principal :
  "LE KG", "LE PRODUIT", "LA PIÈCE", "L'UNITÉ", "LA BARQUETTE", "LE LOT"
- Si aucun de ces libellés n’est explicitement collé au prix principal, alors conditionnement = null
- Les informations comme "bouteille de 75 cl", "barquette de 500 g", "720 g", "400 g" vont dans "description"
- Si une mention du type "Le kg : X €", "Le L : X €", "À l’unité : X €", "Le lot : X €" apparaît dans le bloc produit,
  alors "prix_unitaire" = X, obligatoirement
- Ne jamais calculer "prix_unitaire"
- Toute mention utilisée pour remplir "prix_unitaire" doit être supprimée de "description"
- Dans "description", ne garder que les informations du produit principal extrait
- Ne pas inclure les variantes alternatives introduites par :
  "Également disponible", "Existe aussi", "Au même prix", "Autres variétés", "également disponible au même prix"

Cas prioritaire : bloc avec "LE 1er PRODUIT" et "LE 2e PRODUIT"

- Si un bloc contient à la fois "LE 1er PRODUIT" et "LE 2e PRODUIT", alors :
  - prix = le montant situé immédiatement après "LE 1er PRODUIT"
  - ne jamais utiliser un autre prix du bloc

- Si le bloc contient aussi :
  "Le kg : X €", alors prix_unitaire = X pour le produit principal

- Ignorer toutes les informations suivantes pour prix, prix_unitaire et description :
  - "Par 2", "Par X", "Lot"
  - "au lieu de"
  - les prix recalculés
  - les seconds "Le kg"

- Dans ce cas :
  description = uniquement le format principal du produit
  exemple : "720 g"

Nettoyage obligatoire :
- Supprimer de "nom_produit" et "description" tous les appels de note :
  "(1)", "(2)", "(3)", "(10)", "(A)", "*", "**"
- Supprimer aussi toute séquence initiale ou résiduelle composée uniquement d’appels de note, par exemple :
  "(1)(2)", "(1) (2)", "*(2)", "**(1)"
- Après suppression, normaliser les espaces et la ponctuation
- Si "description" commence encore par une note ou un symbole résiduel, le supprimer jusqu’à obtenir du texte produit propre
- Supprimer les mentions légales et génériques non utiles :
  "Offre disponible dans les magasins...",
  "Voir détails en points de vente",
  "Ticket ... compris",
  "Prix payé en caisse",
  "sur la carte",
  "Vendu en page X"
- Ne jamais mettre d’information de prix, de prix unitaire ou de promo dans "description"
- "description" doit contenir uniquement les informations produit utiles :
  poids, volume, format, variante, origine commerciale utile

Règle forte sur description :
- "description" ne doit contenir que le produit principal
- Dès qu’un segment commence par :
  "Également disponible", "Existe aussi", "Au même prix", "Autres variétés"
  alors couper la description avant ce segment
- Exemple :
  "400 g. Le kg : 6,05 €. Également disponible au même prix : Au beurre..."
  devient :
  description = "400 g"
  prix_unitaire = 6.05

Rayon :
- "rayon" doit être UNE seule valeur parmi :
  "animalerie", "batiment", "boucherie", "boucherie_ls", "boulangerie",
  "bricolage", "cafeteria", "catalogue", "caveavins", "charcuterie",
  "decoration", "espaceculturel", "fleuriste", "fromagerie", "fruits",
  "fruits_bio", "jardinage", "legumes", "legumes_bio", "patisserie",
  "plantes", "poissonnerie", "snack", "sushi", "traiteur",
  "viennoiserie", "voyage", "vrac_bio"
- Choisir le rayon produit par produit
- Ne jamais inventer une autre valeur
- Si hésitation entre plusieurs rayons autorisés :
  choisir le plus spécifique
- Si aucun rayon autorisé ne correspond de façon fiable, retourner rayon = null
- Toujours mapper vers la liste autorisée la plus proche :

Exemples de mapping :
- produits surgelés préparés → "traiteur"
- plats cuisinés → "traiteur"
- légumes frais → "legumes"
- fruits frais → "fruits"
- poisson → "poissonnerie"
- viande → "boucherie"
- charcuterie → "charcuterie"
- fromage → "fromagerie"
- pain → "boulangerie"
- pâtisserie → "patisserie"

Promotions :
- Si une promo est clairement rattachée au produit :
  - renseigner "promo_type", "promo_valeur", "promo_description"
- Sinon :
  - promo_type = null
  - promo_valeur = null
  - promo_description = null
- promo_type doit être EXACTEMENT l’une des valeurs autorisées ci-dessous
- ne jamais inventer un autre type
- si aucun picto promo fiable n’est identifiable, retourner :
  promo_type = null
  promo_valeur = null
  promo_description = null

Liste blanche promo_type autorisée :
[... liste PROMO_TYPES_AUTORISES ...]

Règle de désambiguïsation promo_type :
- Si le texte promo contient explicitement "2e", "2ème", "second", "sur le 2e produit", "sur le 2ème produit",
  alors ne pas choisir un promo_type de pourcentage générique
  et choisir le promo_type spécifique du 2e produit correspondant
- Exemple :
  "-60% SUR LE 2e PRODUIT ACHETÉ" => promo_type = "w_dynamique_pourcentage_2eme_produit"

Règle d’identification :
- Se baser d’abord sur le visuel/picto promo présent dans le bloc produit
- Puis sur le texte promo lisible à l’intérieur de ce picto ou juste à côté
- Ne jamais choisir un promo_type seulement parce qu’un mot promo apparaît dans le texte

Règle promo_valeur :
- promo_valeur est une chaîne de caractères normalisée
- ne jamais mettre le symbole % ni le symbole €
- ne jamais mettre de phrase complète
- si la promo exprime un pourcentage : promo_valeur = le nombre seul
  exemples : 34%, 60% => "34", "60"
- si la promo exprime un montant en euros : promo_valeur = le nombre seul
  exemples : 3€, 10€ => "3", "10"
- si la promo exprime une mécanique de quantité :
  "2 acheté + 1 offert" => "2_1"
  "3 pour 2" => "3_2"
  "2+1" => "2_1"
- si le picto n’a pas de valeur numérique exploitable, promo_valeur = null

Règle promo_description :
- recopier le texte promo utile le plus informatif visible sur le visuel
- exemple :
  "34% avec la carte"
  "2 acheté + 1 offert"
  "-60% sur le 2e produit"
  "10€ sur la carte"
- ne pas recopier des textes génériques non informatifs

Règle de priorité sur les informations produit :

- Toujours extraire les informations du produit principal uniquement
- Ignorer toutes les informations liées à :
  - "Par X", "Par 2", "Lot de X"
  - prix recalculés sur plusieurs produits
  - variantes ou alternatives

Prix unitaire :
- Si plusieurs prix unitaires sont présents :
  - prendre uniquement celui associé au produit principal
  - ignorer ceux liés à des offres type "Par 2", "lot", ou variantes

Description :
- Ne doit contenir QUE le produit principal
- Supprimer toute information liée à :
  - promotions ("au lieu de", réduction, etc.)
  - multi-achat ("Par 2", "lot de")
  - variantes ("Également disponible", etc.)

Priorité d’extraction :
1. Identifier le vrai produit principal du bloc
2. Extraire le prix principal
3. Extraire le conditionnement uniquement s’il est collé au prix principal
4. Extraire le prix_unitaire s’il est explicitement affiché
5. Extraire la promo du produit principal
6. Nettoyer nom_produit et description
7. Couper toutes les variantes alternatives et textes génériques
8. Retourner uniquement le JSON final propre

Schéma attendu :
{json.dumps(PRODUCT_SCHEMA, ensure_ascii=False, indent=2)}

Validation finale obligatoire avant retour JSON :
- vérifier que "rayon" appartient exactement à la liste autorisée, sinon null
- vérifier que "promo_type" appartient exactement à la liste blanche autorisée, sinon null
- vérifier que "description" ne contient ni prix, ni promo, ni renvoi, ni variante alternative
- vérifier que "prix_unitaire" a été extrait si une mention explicite "Le kg", "Le L", "À l’unité", "Le lot" était présente pour le produit principal
- si "LE 1er PRODUIT" et "LE 2e PRODUIT" sont présents, vérifier que prix = prix du 1er produit

Exemple 1
Texte :
"SAUMON ENTIER(1)(2)"
"9,47 € LE KG"
"(1)(2) ÉLEVÉ EN NORVÈGE ET/OU ÉCOSSE ET/OU ISLANDE"
"(2) Offre disponible dans les magasins disposant d'un rayon poissonnerie."

Résultat :
{{
  "enseigne": "E.Leclerc",
  "date_debut": "2026-03-31",
  "date_fin": "2026-04-04",
  "rayon": "poissonnerie",
  "nom_produit": "SAUMON ENTIER",
  "prix": 9.47,
  "conditionnement": "LE KG",
  "description": "ÉLEVÉ EN NORVÈGE ET/OU ÉCOSSE ET/OU ISLANDE",
  "prix_unitaire": null,
  "promo_type": null,
  "promo_valeur": null,
  "promo_description": null,
  "_page": 1
}}  

Pourquoi :
- supprimer "(1)(2)" du nom_produit
- supprimer aussi "(1)(2)" au début de la description
- supprimer la mention légale non utile
- garder uniquement l’information produit utile dans description

Exemple 2
Texte :
"ÉCRASÉ DE POMMES DE TERRE 2 CAROTTES(2)"
"FLORETTE"
"2,42 €"
"PRIX PAYÉ EN CAISSE"
"34% avec la carte"
"Ticket E.Leclerc compris"
"soit 0,82 € sur la carte"
"400 g"
"Le kg : 6,05 €"
"Également disponible au même prix : Au beurre et sel de Guérande ou au Fromage."

Résultat :
{{
  "enseigne": "E.Leclerc",
  "date_debut": "2026-03-31",
  "date_fin": "2026-04-04",
  "rayon": "legumes",
  "nom_produit": "ÉCRASÉ DE POMMES DE TERRE 2 CAROTTES \"FLORETTE\"",
  "prix": 2.42,
  "conditionnement": null,
  "description": "400 g",
  "prix_unitaire": 6.05,
  "promo_type": "w_dynamique_carte_fid_pourcentage",
  "promo_valeur": "34",
  "promo_description": "34% avec la carte",
  "_page": 8
}}

Pourquoi :
- pas de conditionnement car aucun libellé autorisé n’est collé au prix principal
- "400 g" va dans description
- "Le kg : 6,05 €" remplit prix_unitaire puis doit être supprimé de description
- couper la description avant "Également disponible..."
- ne jamais copier "Prix payé en caisse", "Ticket E.Leclerc compris" ou "soit 0,82 € sur la carte" dans description
- choisir le texte promo le plus informatif visible
- promo_valeur doit être "34" et non null

Exemple 3
Texte :
"LE 1er PRODUIT 3,16 €"
"LE 2e PRODUIT 1,26 €"
"-60% SUR LE 2e PRODUIT ACHETÉ"
"POMMES DUCHESSE SURGELÉES FINDUS"
"720 g"
"Le kg : 4,39 €"

Résultat :
{{
  "enseigne": "E.Leclerc",
  "date_debut": "2026-03-31",
  "date_fin": "2026-04-04",
  "rayon": "traiteur",
  "nom_produit": "POMMES DUCHESSE SURGELÉES FINDUS",
  "prix": 3.16,
  "conditionnement": null,
  "description": "720 g",
  "prix_unitaire": 4.39,
  "promo_type": "w_dynamique_pourcentage_2eme_produit",
  "promo_valeur": "60",
  "promo_description": "-60% SUR LE 2e PRODUIT ACHETÉ",
  "_page": 11
}}

Pourquoi :
- produit autorisé car c’est une vraie page détaillée
- prix = montant du "LE 1er PRODUIT"
- "LE 1er PRODUIT" et "LE 2e PRODUIT" ne sont jamais des conditionnements
- "720 g" va dans description
- "Le kg : 4,39 €" remplit prix_unitaire puis doit disparaître de description
- la promo est une promo 2e produit, pas une promo générique

Contre-exemple d’exclusion
Texte :
"Vendu en page 11"
"LE 1er PRODUIT 3,16 €"
"LE 2e PRODUIT 1,26 €"
"POMMES DUCHESSE SURGELÉES FINDUS"

Résultat :
ne pas extraire de produit

Pourquoi :
- présence d’un renvoi "Vendu en page 11"
- c’est un teaser, pas une vraie fiche produit
"""
# endregion

# gemini-2.5-flash-lite
# gemini-2.5-flash
# gemini-2.5-pro
# gemini-2.0-flash
# gemini-2.0-flash-lite
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
DEFAULT_MAX_OUTPUT_TOKENS = 32768
DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS = 240
DEFAULT_RENDER_DPI = 220
DEFAULT_YOLO_MODEL = "yolov8n.pt"
DEFAULT_YOLO_CONF = 0.15
DEFAULT_GEMINI_RETRY_MAX_ATTEMPTS = 4
DEFAULT_FITZ_MIN_IMAGE_BYTES = 15000
DEFAULT_FITZ_MIN_DECODED_SIDE_PX = 32
DEFAULT_FITZ_MIN_GRAY_STD = 8.0

# region CONFIG / HELPERS

def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_on(name: str) -> bool | None:
    """Retourne None si la variable d'environnement n'est pas définie."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def render_dpi() -> int:
    return env_int("EXTRACT_RENDER_DPI", DEFAULT_RENDER_DPI, minimum=100, maximum=300)


def fitz_min_bytes() -> int:
    return env_int("FITZ_MIN_IMAGE_BYTES", DEFAULT_FITZ_MIN_IMAGE_BYTES, minimum=0)


def fitz_min_std() -> float:
    return env_float("FITZ_MIN_GRAY_STD", DEFAULT_FITZ_MIN_GRAY_STD, minimum=0.0)


def valid_pages(page_numbers: list) -> list[int]:
    return sorted({int(p) for p in page_numbers if isinstance(p, int) and p >= 1})


def group_page(items: list[tuple[int, dict]]) -> dict[int, list[tuple[int, dict]]]:
    """Regroupe des produits (index, dict) par numéro de page (_page)."""
    out: dict[int, list[tuple[int, dict]]] = {}
    for idx, produit in items:
        pg = produit.get("_page")
        if pg is None:
            continue
        try:
            page_num = int(pg)
        except (TypeError, ValueError):
            continue
        out.setdefault(page_num, []).append((idx, produit))
    return out


def rel_path(output_dir: Path, img_path: Path) -> str:
    try:
        return str(img_path.relative_to(output_dir)).replace("\\", "/")
    except ValueError:
        return str(img_path)


def product_pages(produits: list) -> list[int]:
    return sorted({
        int(p["_page"])
        for p in produits
        if isinstance(p, dict) and p.get("_page") is not None
    })


def max_page(by_page: dict) -> int | None:
    pages = [
        int(key.split("_", 1)[1])
        for key in by_page
        if key.startswith("page_") and key.split("_", 1)[1].isdigit()
    ]
    return max(pages) if pages else None


def parse_gemini(result) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        produits = result.get("produits", [])
        return produits if isinstance(produits, list) else []
    return []


def retry_wait(attempt: int) -> float:
    return min(20.0, (2 ** (attempt - 1)) + random.uniform(0.0, 0.7))


def get_key() -> str:
    api_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    if api_key:
        return api_key
    try:
        api_key = getpass.getpass("\nClé API Gemini : ").strip()
    except (EOFError, OSError):
        api_key = ""
    if not api_key:
        api_key = input("\nClé API Gemini (saisie visible) : ").strip().strip('"')
    return api_key


def cli_pdf(script_dir: Path) -> Path | None:
    frontend_pdfs = sorted(script_dir.glob("frontend/*.pdf"))
    if frontend_pdfs:
        return frontend_pdfs[0]
    legacy = script_dir / "catalogue_SU.pdf"
    return legacy if legacy.exists() else None


# endregion

# region YOLO / IMAGES

def ok_image(image_bytes: bytes) -> bool:
    """
    Rejette masques 1x1, placeholders noirs et mini-pictos embarqués dans le PDF.
    (Les filtres géométriques sur la page ne suffisent pas : le rectangle affiché
    peut être grand alors que le flux image est minuscule.)
    """
    if not image_bytes:
        return False
    min_bytes = fitz_min_bytes()
    if min_bytes > 0 and len(image_bytes) < min_bytes:
        return False

    min_side = env_int("FITZ_MIN_DECODED_SIDE_PX", DEFAULT_FITZ_MIN_DECODED_SIDE_PX, minimum=1)

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return False
    h, w = img.shape[:2]
    if w < min_side or h < min_side:
        return False

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < fitz_min_std():
        return False
    return True


def is_fitz_banner(w_pt: float, h_pt: float, rect, page_w: float, page_h: float) -> bool:
    """
    Rejette les bandeaux promo / en-têtes catalogue (ex. « LES PRIX COÛTANTS »).
    Ces images sont larges, peu hautes, et collées en haut de page.
    """
    if w_pt <= 0 or h_pt <= 0 or page_w <= 0 or page_h <= 0:
        return True

    aspect = w_pt / h_pt
    width_ratio = w_pt / page_w
    height_ratio = h_pt / page_h
    top_ratio = rect.y0 / page_h

    banner_aspect = env_float("FITZ_BANNER_MAX_ASPECT", 2.5, minimum=1.0)
    if aspect >= banner_aspect and width_ratio >= 0.55:
        return True

    if top_ratio <= 0.15 and width_ratio >= 0.65 and height_ratio <= 0.22:
        return True

    return False


def is_fitz_logo_badge(w_pt: float, h_pt: float, rect, page_w: float, page_h: float) -> bool:
    """
    Rejette les pictos / logos (ex. « Soutien à la production française »).
    Ce sont des visuels petits, souvent carrés, en marge de page.
    """
    if w_pt <= 0 or h_pt <= 0 or page_w <= 0 or page_h <= 0:
        return False

    page_area = page_w * page_h
    area_ratio = (w_pt * h_pt) / page_area
    aspect = w_pt / h_pt
    max_badge_ratio = env_float("FITZ_MAX_BADGE_AREA_RATIO", 0.035, minimum=0.0)

    if area_ratio > max_badge_ratio:
        return False

    if 0.75 <= aspect <= 1.35:
        return True

    left_margin = rect.x1 <= page_w * 0.22
    right_margin = rect.x0 >= page_w * 0.78
    return area_ratio <= max_badge_ratio * 1.5 and (left_margin or right_margin)


def fitz_image_rank_key(item: dict, page_w: float, page_h: float) -> tuple:
    """Priorise les photos produit (grandes surfaces) plutôt que les visuels décoratifs."""
    page_area = page_w * page_h if page_w > 0 and page_h > 0 else 1.0
    area_ratio = item["area"] / page_area
    x0, y0, x1, y1 = item["bbox_pt"]
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)
    aspect = w / h if h > 0 else 99.0
    cy_ratio = item["cy"] / page_h if page_h > 0 else 0.5
    aspect_penalty = min(abs(aspect - 1.0), abs(aspect - 0.8))
    return (-area_ratio, aspect_penalty, cy_ratio)


def is_local() -> bool:
    flask_env = os.environ.get("FLASK_ENV", "").strip().lower()
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if flask_env == "development" or app_env == "local":
        return True
    return os.environ.get("PYTHON_ENV", "").strip().lower() in {"development", "local"}


def use_yolo() -> bool:
    """
    Active YOLO par défaut en local, désactivé hors local.
    Possibilité de forcer via ENABLE_YOLO=true/false.
    """
    explicit = env_on("ENABLE_YOLO")
    if explicit is not None:
        return explicit
    return is_local()


def render_pages(pdf_path: Path, output_dir: Path, page_numbers: list[int], dpi: int | None = None) -> dict[int, Path]:
    """
    Rend uniquement les pages demandées du PDF en PNG.
    Retourne {page_num: path_png}.
    """
    if dpi is None:
        dpi = render_dpi()
    pages_dir = output_dir / "pages_png"
    pages_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    page_map = {}
    for page_num in valid_pages(page_numbers):
        i = page_num - 1
        if i < 0 or i >= len(doc):
            continue
        out_path = pages_dir / f"page_{page_num:03d}.png"
        if not out_path.exists():
            pix = doc.load_page(i).get_pixmap(matrix=matrix, alpha=False)
            pix.save(str(out_path))
            del pix
            gc.collect()
        page_map[page_num] = out_path

    doc.close()
    return page_map


def fitz_images(pdf_path: Path, output_dir: Path, page_numbers: list[int]) -> dict[int, list[dict]]:
    """
    Extrait les images réellement embarquées dans le PDF (rapide, pas de rendu).
    Retourne {page_num: [ {xref, bbox_pt, bbox_px, cy, area, image_path}, ... ]}
    triés du haut vers le bas.
    """
    images_dir = output_dir / "images_fitz"
    images_dir.mkdir(parents=True, exist_ok=True)

    dpi = render_dpi()
    zoom = dpi / 72.0

    min_side_pt = env_float("FITZ_MIN_IMAGE_SIDE_PT", 30.0, minimum=0.0)
    min_area_ratio = env_float("FITZ_MIN_IMAGE_AREA_RATIO", 0.005, minimum=0.0)

    doc = fitz.open(pdf_path)
    page_images: dict[int, list[dict]] = {}
    xref_cache: dict[int, Path | None] = {}

    for page_num in valid_pages(page_numbers):
        page_index = page_num - 1
        if page_index < 0 or page_index >= len(doc):
            continue
        page = doc.load_page(page_index)
        page_w = page.rect.width
        page_h = page.rect.height
        page_area = page_w * page_h if page_w > 0 and page_h > 0 else 0.0

        items: list[dict] = []
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                rects = page.get_image_rects(xref, transform=False)
            except Exception:
                rects = []

            if not rects:
                continue

            for rect_idx, rect in enumerate(rects):
                w_pt = rect.width
                h_pt = rect.height
                if w_pt < min_side_pt or h_pt < min_side_pt:
                    continue
                if is_fitz_banner(w_pt, h_pt, rect, page_w, page_h):
                    continue
                if is_fitz_logo_badge(w_pt, h_pt, rect, page_w, page_h):
                    continue
                if page_area > 0:
                    area_ratio = (w_pt * h_pt) / page_area
                    if area_ratio < min_area_ratio:
                        continue
                    # éviter de prendre un fond pleine page
                    if area_ratio > 0.95:
                        continue

                if xref not in xref_cache:
                    try:
                        base_image = doc.extract_image(xref)
                    except Exception:
                        xref_cache[xref] = None
                        continue
                    image_bytes = base_image.get("image") if isinstance(base_image, dict) else None
                    ext = base_image.get("ext", "png") if isinstance(base_image, dict) else "png"
                    if not image_bytes or not ok_image(image_bytes):
                        xref_cache[xref] = None
                        continue
                    out_path = images_dir / f"x{xref}.{ext}"
                    if out_path.exists():
                        try:
                            if not ok_image(out_path.read_bytes()):
                                out_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    if not out_path.exists():
                        with open(out_path, "wb") as f:
                            f.write(image_bytes)
                    xref_cache[xref] = out_path

                cached_path = xref_cache.get(xref)
                if cached_path is None:
                    continue

                bbox_px = [
                    int(rect.x0 * zoom),
                    int(rect.y0 * zoom),
                    int(rect.x1 * zoom),
                    int(rect.y1 * zoom),
                ]
                items.append({
                    "xref": xref,
                    "rect_index": rect_idx,
                    "bbox_pt": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
                    "bbox_px": bbox_px,
                    "cy": (rect.y0 + rect.y1) / 2.0,
                    "area": float(w_pt * h_pt),
                    "image_path": cached_path,
                })

        items.sort(key=lambda d: fitz_image_rank_key(d, page_w, page_h))
        page_images[page_num] = items

    doc.close()
    return page_images


def assign_fitz(produits: list, page_fitz_images: dict[int, list[dict]], output_dir: Path) -> list:
    """
    Assigne les images embarquées (fitz) aux produits, page par page,
    en suivant l'ordre haut->bas.
    Si une page contient moins d'images embarquées que de produits,
    on n'assigne rien sur cette page : YOLO prendra le relais.
    """
    if not produits:
        return produits

    indexed = [
        (idx, p)
        for idx, p in enumerate(produits)
        if isinstance(p, dict) and p.get("_page") is not None
    ]
    for page_num, items in group_page(indexed).items():
        available = page_fitz_images.get(page_num, [])
        if len(available) < len(items):
            if available:
                print(
                    f"[fitz] page {page_num}: {len(available)} image(s) embarquée(s) "
                    f"pour {len(items)} produit(s) → fallback YOLO sur cette page"
                )
            else:
                print(f"[fitz] page {page_num}: aucune image embarquée exploitable → fallback YOLO")
            continue

        selected = available[: len(items)]
        skip_page = False
        for det in selected:
            img_path = Path(det["image_path"])
            try:
                if not ok_image(img_path.read_bytes()):
                    print(
                        f"[fitz] page {page_num}: image rejetée ({img_path.name}) "
                        f"→ fallback YOLO sur cette page"
                    )
                    skip_page = True
                    break
            except OSError:
                print(f"[fitz] page {page_num}: image illisible → fallback YOLO sur cette page")
                skip_page = True
                break
        if skip_page:
            continue

        print(
            f"[fitz] page {page_num}: {len(selected)} image(s) embarquée(s) "
            f"pour {len(items)} produit(s) → assignées via fitz"
        )
        for i, (_prod_idx, produit) in enumerate(items):
            det = selected[i]
            img_path = Path(det["image_path"])
            produit["image_bbox_px"] = det["bbox_px"]
            produit["image_source"] = "fitz"
            produit["image_path"] = rel_path(output_dir, img_path)

    return produits


def load_yolo():
    model_name = os.environ.get("YOLO_MODEL", DEFAULT_YOLO_MODEL).strip()
    print(f"[yolo] Chargement du modèle : {model_name}")
    return YOLO(model_name)


def detect_boxes(page_img_path: Path, yolo_model, conf_threshold: float = DEFAULT_YOLO_CONF) -> list[dict]:
    """
    Détecte des bbox candidates sur une page.
    Retourne une liste triée de dicts :
    {
        "bbox": [x1, y1, x2, y2],
        "conf": float,
        "cls": int,
        "label": str,
        "area": int,
        "cy": float
    }
    """
    img = cv2.imread(str(page_img_path))
    if img is None:
        return []

    h, w = img.shape[:2]
    results = yolo_model.predict(
        source=img,
        conf=conf_threshold,
        verbose=False
    )

    detections = []
    for r in results:
        if r.boxes is None:
            continue

        names = r.names if hasattr(r, "names") else {}
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])

            bw = max(0, x2 - x1)
            bh = max(0, y2 - y1)
            area = bw * bh
            if area <= 0:
                continue

            # filtres simples : éliminer les boîtes absurdes
            if bw < 40 or bh < 40:
                continue
            if bw > 0.98 * w and bh > 0.98 * h:
                continue

            cls_id = int(b.cls[0].item()) if b.cls is not None else -1
            conf = float(b.conf[0].item()) if b.conf is not None else 0.0
            label = names.get(cls_id, str(cls_id)) if isinstance(names, dict) else str(cls_id)

            detections.append({
                "bbox": [x1, y1, x2, y2],
                "conf": conf,
                "cls": cls_id,
                "label": label,
                "area": area,
                "cy": (y1 + y2) / 2.0,
            })

    # tri principal : haut vers bas, puis plus grand d'abord
    detections.sort(key=lambda d: (d["cy"], -d["area"]))
    return detections


def iou(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    iw = max(0, inter_x2 - inter_x1)
    ih = max(0, inter_y2 - inter_y1)
    inter = iw * ih
    if inter == 0:
        return 0.0

    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def dedupe(detections: list[dict], iou_threshold: float = 0.7) -> list[dict]:
    """
    Supprime les bbox très redondantes.
    """
    kept = []
    for det in sorted(detections, key=lambda d: d["conf"], reverse=True):
        overlap = any(iou(det["bbox"], k["bbox"]) >= iou_threshold for k in kept)
        if not overlap:
            kept.append(det)

    kept.sort(key=lambda d: (d["cy"], -d["area"]))
    return kept


def crop(page_img_path: Path, bbox: list[int], out_path: Path, pad: int = 8) -> bool:
    img = cv2.imread(str(page_img_path))
    if img is None:
        return False

    h, w = img.shape[:2]
    x1, y1, x2, y2 = bbox

    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w, x2 + pad)
    y2 = min(h, y2 + pad)

    if x2 <= x1 or y2 <= y1:
        return False

    crop = img[y1:y2, x1:x2]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return cv2.imwrite(str(out_path), crop)


def assign_yolo(produits: list, page_map: dict[int, Path], output_dir: Path) -> list:
    """
    V1 simple :
    - détecte des bbox candidates sur chaque page
    - les trie de haut en bas
    - assigne aux produits de la page (qui n'ont pas déjà une image fitz) dans cet ordre
    """
    if not produits:
        return produits

    products_needing_image = [
        (idx, p)
        for idx, p in enumerate(produits)
        if isinstance(p, dict) and not p.get("image_path")
    ]
    if not products_needing_image:
        print("[yolo] Aucun produit sans image - modèle non chargé.")
        return produits

    if not use_yolo():
        print("[yolo] Désactivé (runtime non local ou ENABLE_YOLO=false).")
        return produits

    yolo_model = load_yolo()
    images_dir = output_dir / "images_yolo"

    for page_num, items in group_page(products_needing_image).items():
        page_img_path = page_map.get(page_num)
        if page_img_path is None:
            continue

        print(f"[yolo] Détection page {page_num}...")
        detections = detect_boxes(page_img_path, yolo_model)
        detections = dedupe(detections)

        # on garde les bbox assez grandes pour être plausibles comme photo produit
        plausible = []
        img = cv2.imread(str(page_img_path))
        if img is None:
            continue
        H, W = img.shape[:2]

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            bw = x2 - x1
            bh = y2 - y1
            area_ratio = (bw * bh) / float(W * H)

            # heuristiques V1
            if area_ratio < 0.01:
                continue
            if bw < 70 or bh < 70:
                continue

            plausible.append(det)

        # tri haut -> bas
        plausible.sort(key=lambda d: (d["cy"], -d["area"]))

        # produits page triés par ordre d'apparition
        page_products = items

        for i, (prod_idx, produit) in enumerate(page_products):
            if i >= len(plausible):
                break

            det = plausible[i]
            bbox = det["bbox"]
            produit["image_bbox_px"] = bbox
            produit["image_detection_label"] = det["label"]
            produit["image_detection_conf"] = round(det["conf"], 4)
            produit["image_source"] = "yolo"

            slug = f"page_{page_num:03d}_{prod_idx+1:04d}"
            crop_path = images_dir / f"{slug}.png"
            ok = crop(page_img_path, bbox, crop_path)

            if ok:
                produit["image_path"] = rel_path(output_dir, crop_path)

    del yolo_model
    gc.collect()
    return produits

# endregion


def to_b64(pdf_path: Path) -> str:
    return base64.b64encode(pdf_path.read_bytes()).decode("utf-8")


def normalize(produits: list) -> list:
    """Copie page → _page pour compatibilité avec les exports existants."""
    for p in produits:
        if not isinstance(p, dict):
            continue
        pg = p.get("page")
        if pg is not None and "_page" not in p:
            p["_page"] = pg
    return produits


def split_page(produits: list) -> dict:
    out = {}
    for p in produits:
        if not isinstance(p, dict):
            continue
        pg = p.get("_page") if p.get("_page") is not None else p.get("page")
        if pg is None:
            pg = 0
        key = f"page_{int(pg)}"
        out.setdefault(key, []).append(p)
    return dict(sorted(out.items(), key=lambda x: int(x[0].split("_", 1)[1]) if x[0].split("_", 1)[1].isdigit() else 0))


def gemini_err(http_code: int, error_body: str) -> str:
    try:
        parsed = json.loads(error_body)
    except json.JSONDecodeError:
        return f"Erreur Gemini HTTP {http_code}: {error_body}"

    error_obj = parsed.get("error", {}) if isinstance(parsed, dict) else {}
    message = error_obj.get("message", "Erreur inconnue Gemini")
    status = error_obj.get("status")
    details = error_obj.get("details", [])

    hint = ""
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            detail_type = detail.get("@type", "")
            if "QuotaFailure" in detail_type:
                hint = "Quota API Gemini atteint (free tier)."
                break
            if "RetryInfo" in detail_type and detail.get("retryDelay"):
                hint = f"Réessaie après {detail.get('retryDelay')}."
                break

    status_part = f" [{status}]" if status else ""
    hint_part = f" {hint}" if hint else ""
    return f"Erreur Gemini HTTP {http_code}{status_part}: {message}.{hint_part}".strip()


def ask_gemini(api_key: str, pdf_b64: str) -> dict:
    primary_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", "").strip()
    models_to_try = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models_to_try.append(fallback_model)

    max_tokens = env_int(
        "EXTRACT_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, minimum=1024, maximum=65536
    )
    http_timeout = env_int(
        "GEMINI_HTTP_TIMEOUT_SECONDS",
        DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS,
        minimum=30,
        maximum=300,
    )
    max_attempts = env_int(
        "GEMINI_RETRY_MAX_ATTEMPTS", DEFAULT_GEMINI_RETRY_MAX_ATTEMPTS, minimum=1, maximum=8
    )

    retryable_http_codes = {429, 500, 502, 503, 504}
    last_error_message = ""

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        for attempt in range(1, max_attempts + 1):
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": PROMPT},
                            {
                                "inline_data": {
                                    "mime_type": "application/pdf",
                                    "data": pdf_b64
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": max_tokens
                }
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            try:
                with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8")
                print(f"  [erreur HTTP {e.code}] {error_body}")
                formatted = gemini_err(e.code, error_body)
                last_error_message = formatted

                if e.code in retryable_http_codes and attempt < max_attempts:
                    sleep_seconds = retry_wait(attempt)
                    print(
                        f"  [retry] modèle={model} tentative {attempt}/{max_attempts} "
                        f"après erreur HTTP {e.code}, pause {sleep_seconds:.1f}s"
                    )
                    time.sleep(sleep_seconds)
                    continue

                break
            except urllib.error.URLError as e:
                last_error_message = f"Erreur réseau Gemini: {e}"
                if attempt < max_attempts:
                    sleep_seconds = retry_wait(attempt)
                    print(
                        f"  [retry] modèle={model} tentative {attempt}/{max_attempts} "
                        f"après erreur réseau, pause {sleep_seconds:.1f}s"
                    )
                    time.sleep(sleep_seconds)
                    continue
                break
            except TimeoutError:
                last_error_message = f"Timeout Gemini après {http_timeout}s."
                if attempt < max_attempts:
                    sleep_seconds = retry_wait(attempt)
                    print(
                        f"  [retry] modèle={model} tentative {attempt}/{max_attempts} "
                        f"après timeout, pause {sleep_seconds:.1f}s"
                    )
                    time.sleep(sleep_seconds)
                    continue
                break

            try:
                cand = result["candidates"][0]
                if cand.get("finishReason") and cand["finishReason"] != "STOP":
                    print(f"  [attention] finishReason={cand['finishReason']} - la réponse peut être incomplète.")
                text = cand["content"]["parts"][0]["text"].strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1]
                    text = text.rsplit("```", 1)[0].strip()
                if os.environ.get("EXTRACT_DEBUG"):
                    print(f"\n  RÉPONSE BRUTE GEMINI :\n{text}\n")
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return {"produits": parsed}
                if isinstance(parsed, dict):
                    return parsed
                return {"produits": []}
            except (KeyError, json.JSONDecodeError, IndexError) as e:
                print(f"  [erreur parsing] {e}")
                print(f"  Réponse brute : {result}")
                return {"produits": []}

    if not last_error_message:
        last_error_message = "Erreur inconnue Gemini."
    return {
        "error": last_error_message,
        "produits": []
    }

def extract_catalogue(pdf_path: str, api_key: str, output_dir: str):
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/2] Lecture du PDF et encodage...")
    pdf_b64 = to_b64(pdf_path)
    size_mb = len(pdf_b64) * 3 / 4 / (1024 * 1024)
    print(f"    → {pdf_path.name} (~{size_mb:.2f} Mo données base64)")

    model_used = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    max_tok = env_int("EXTRACT_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)
    print(f"\n[2/2] Extraction via Gemini (modèle : {model_used}, maxOutputTokens : {max_tok})...")
    result = ask_gemini(api_key, pdf_b64)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])

    produits = parse_gemini(result)
    normalize(produits)
    pages_with_products = product_pages(produits)
    print(f"\n[images] Extraction des images embarquées via fitz ({len(pages_with_products)} pages)...")
    page_fitz_images = fitz_images(pdf_path, output_dir, pages_with_products)
    produits = assign_fitz(produits, page_fitz_images, output_dir)

    missing_after_fitz = [
        p for p in produits if isinstance(p, dict) and not p.get("image_path")
    ]
    if missing_after_fitz:
        print(
            f"[images] {len(missing_after_fitz)}/{len(produits)} produit(s) sans image embarquée "
            f"→ rendu des pages + fallback YOLO..."
        )
        page_map = render_pages(pdf_path, output_dir, pages_with_products)
        produits = assign_yolo(produits, page_map, output_dir)
    else:
        print(f"[images] Toutes les images extraites via fitz ({len(produits)} produits) - YOLO non nécessaire.")
    all_results_by_page = split_page(produits)
    total_pages_hint = max_page(all_results_by_page)

    print(f"\n[export] Écriture des JSON...")
    output_json = output_dir / "produits.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump({
            "source": pdf_path.name,
            "mode": "pdf_inline",
            "total_pages_estime": total_pages_hint,
            "total_produits": len(produits),
            "produits": produits
        }, f, ensure_ascii=False, indent=2)

    output_json_pages = output_dir / "produits_par_page.json"
    with open(output_json_pages, "w", encoding="utf-8") as f:
        json.dump(all_results_by_page, f, ensure_ascii=False, indent=2)

    print(f"\n{'─'*50}")
    print(f"  PDF         : {pdf_path.name}")
    print(f"  Pages       : {total_pages_hint if total_pages_hint is not None else '- (voir champ page par produit)'}")
    print(f"  Produits    : {len(produits)}")
    print(f"  Résultats   : {output_dir}/")
    print(f"{'─'*50}")

    return produits

def main() -> None:
    script_dir = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1])
    else:
        default_pdf = cli_pdf(script_dir)
        if default_pdf is not None:
            pdf_path = default_pdf
            print(f"\nPDF par défaut : {pdf_path}")
        else:
            pdf_path = Path(input("\nChemin vers le PDF : ").strip().strip('"'))

    if not pdf_path.exists():
        print(f"[ERREUR] Fichier introuvable : {pdf_path}")
        sys.exit(1)

    api_key = get_key()
    if not api_key:
        print("[ERREUR] Clé API vide. Définissez GEMINI_API_KEY ou saisissez la clé.")
        sys.exit(1)

    output_dir = script_dir / "resultats" / "cli"
    extract_catalogue(pdf_path=str(pdf_path), api_key=api_key, output_dir=str(output_dir))


if __name__ == "__main__":
    main()
