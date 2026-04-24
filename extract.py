import sys
import os
import json
import base64
import urllib.request
import urllib.error
import getpass
from pathlib import Path

def _python_inside_venv(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _venv_python_path() -> Path | None:
    """
    Premier interpréteur de venv utilisable, dans l'ordre :
    - EXTRACT_CATALOGUE_VENV : racine du venv, ou chemin direct vers python(.exe)
    - extraction_pdf_ia/.venv
    - extraction_pdf_ia/venv
    - flux_UPF/venv (partagé)
    """
    here = Path(__file__).resolve().parent
    roots: list[Path] = []
    env = os.environ.get("EXTRACT_CATALOGUE_VENV", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and p.name.lower().startswith("python"):
            return p
        roots.append(p)
    roots.extend(
        [
            here / ".venv",
            here / "venv",
            here.parent / "flux_UPF" / "venv",
        ]
    )
    for root in roots:
        cand = _python_inside_venv(root)
        if cand.is_file():
            return cand
    return None


def ensure_venv():
    """Relance ce script avec le venv si on ne l'utilise pas déjà."""
    venv_py = _venv_python_path()
    if venv_py is None:
        return
    try:
        if Path(sys.executable).resolve() == venv_py.resolve():
            return
    except OSError:
        return
    os.execv(str(venv_py), [str(venv_py)] + sys.argv)


ensure_venv()

import math
import fitz  # PyMuPDF
import cv2
import numpy as np
from ultralytics import YOLO

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
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS = 240
DEFAULT_RENDER_DPI = 220
DEFAULT_YOLO_MODEL = "yolov8n.pt"
DEFAULT_YOLO_CONF = 0.15

# region YOLO / IMAGES

def render_pdf_pages(pdf_path: Path, output_dir: Path, dpi: int = DEFAULT_RENDER_DPI) -> dict[int, Path]:
    """
    Rend chaque page du PDF en PNG.
    Retourne {page_num: path_png}.
    """
    pages_dir = output_dir / "pages_png"
    pages_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    page_map = {}
    for i in range(len(doc)):
        page_num = i + 1
        out_path = pages_dir / f"page_{page_num:03d}.png"
        if not out_path.exists():
            pix = doc[i].get_pixmap(matrix=matrix, alpha=False)
            pix.save(str(out_path))
        page_map[page_num] = out_path

    doc.close()
    return page_map


def load_yolo_model():
    model_name = os.environ.get("YOLO_MODEL", DEFAULT_YOLO_MODEL).strip()
    print(f"[yolo] Chargement du modèle : {model_name}")
    return YOLO(model_name)


def detect_candidate_bboxes(page_img_path: Path, yolo_model, conf_threshold: float = DEFAULT_YOLO_CONF) -> list[dict]:
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


def _bbox_iou(box_a, box_b) -> float:
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


def dedupe_bboxes(detections: list[dict], iou_threshold: float = 0.7) -> list[dict]:
    """
    Supprime les bbox très redondantes.
    """
    kept = []
    for det in sorted(detections, key=lambda d: d["conf"], reverse=True):
        overlap = any(_bbox_iou(det["bbox"], k["bbox"]) >= iou_threshold for k in kept)
        if not overlap:
            kept.append(det)

    kept.sort(key=lambda d: (d["cy"], -d["area"]))
    return kept


def crop_bbox_from_page(page_img_path: Path, bbox: list[int], out_path: Path, pad: int = 8) -> bool:
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


def assign_yolo_images_to_products(produits: list, page_map: dict[int, Path], output_dir: Path) -> list:
    """
    V1 simple :
    - détecte des bbox candidates sur chaque page
    - les trie de haut en bas
    - assigne aux produits de la page dans cet ordre
    """
    if not produits:
        return produits

    yolo_model = load_yolo_model()
    images_dir = output_dir / "images_yolo"

    # grouper les produits par page
    produits_par_page = {}
    for idx, p in enumerate(produits):
        if not isinstance(p, dict):
            continue
        pg = p.get("_page")
        if pg is None:
            continue
        try:
            pg = int(pg)
        except Exception:
            continue
        produits_par_page.setdefault(pg, []).append((idx, p))

    for page_num, items in produits_par_page.items():
        page_img_path = page_map.get(page_num)
        if page_img_path is None:
            continue

        print(f"[yolo] Détection page {page_num}...")
        detections = detect_candidate_bboxes(page_img_path, yolo_model)
        detections = dedupe_bboxes(detections)

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

            slug = f"page_{page_num:03d}_{prod_idx+1:04d}"
            crop_path = images_dir / f"{slug}.png"
            ok = crop_bbox_from_page(page_img_path, bbox, crop_path)

            if ok:
                produit["image_path"] = str(crop_path.relative_to(output_dir)).replace("\\", "/")

    return produits

# endregion

# Convertit le PDF en base64
def pdf_to_base64(pdf_path: Path) -> str:
    return base64.b64encode(pdf_path.read_bytes()).decode("utf-8")

# Normalise les produits pour la compatibilité avec les exports existants
def _normalize_products(produits: list) -> list:
    """Copie page → _page pour compatibilité avec les exports existants."""
    for p in produits:
        if not isinstance(p, dict):
            continue
        pg = p.get("page")
        if pg is not None and "_page" not in p:
            p["_page"] = pg
    return produits

# Groupe les produits par page
def _group_by_page(produits: list) -> dict:
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

# Appel API Gemini
def _format_gemini_http_error(http_code: int, error_body: str) -> str:
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


def call_gemini_pdf(api_key: str, pdf_b64: str) -> dict:
    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    try:
        max_tokens = int(os.environ.get("EXTRACT_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)))
    except ValueError:
        max_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    max_tokens = max(1024, min(max_tokens, 65536))
    try:
        http_timeout = int(os.environ.get("GEMINI_HTTP_TIMEOUT_SECONDS", str(DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS)))
    except ValueError:
        http_timeout = DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS
    http_timeout = max(30, min(http_timeout, 300))

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
        return {
            "error": _format_gemini_http_error(e.code, error_body),
            "produits": []
        }
    except urllib.error.URLError as e:
        return {
            "error": f"Erreur réseau Gemini: {e}",
            "produits": []
        }
    except TimeoutError:
        return {
            "error": f"Timeout Gemini après {http_timeout}s.",
            "produits": []
        }

    try:
        cand = result["candidates"][0]
        if cand.get("finishReason") and cand["finishReason"] != "STOP":
            print(f"  [attention] finishReason={cand['finishReason']} — la réponse peut être incomplète.")
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

# Fonction principale qui extrait les produits du catalogue
def extract_catalogue(pdf_path: str, api_key: str, output_dir: str):
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/2] Lecture du PDF et encodage...")
    pdf_b64 = pdf_to_base64(pdf_path)
    size_mb = len(pdf_b64) * 3 / 4 / (1024 * 1024)
    print(f"    → {pdf_path.name} (~{size_mb:.2f} Mo données base64)")

    model_used = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    try:
        max_tok = int(os.environ.get("EXTRACT_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)))
    except ValueError:
        max_tok = DEFAULT_MAX_OUTPUT_TOKENS
    print(f"\n[2/2] Extraction via Gemini (modèle : {model_used}, maxOutputTokens : {max_tok})...")
    result = call_gemini_pdf(api_key, pdf_b64)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])

    if isinstance(result, list):
        produits = result
    elif isinstance(result, dict):
        produits = result.get("produits", [])
    else:
        produits = []
    if not isinstance(produits, list):
        produits = []
    _normalize_products(produits)
    print(f"\n[images] Rendu des pages PDF...")
    page_map = render_pdf_pages(pdf_path, output_dir)

    print(f"[images] Détection YOLO + crops...")
    produits = assign_yolo_images_to_products(produits, page_map, output_dir)
    all_results_by_page = _group_by_page(produits)

    pages_with_products = [
        int(k.split("_", 1)[1]) for k in all_results_by_page if k.startswith("page_") and k.split("_", 1)[1].isdigit()
    ]
    total_pages_hint = max(pages_with_products) if pages_with_products else None

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
    print(f"  Pages       : {total_pages_hint if total_pages_hint is not None else '— (voir champ page par produit)'}")
    print(f"  Produits    : {len(produits)}")
    print(f"  Résultats   : {output_dir}/")
    print(f"{'─'*50}")

    return produits

if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    default_pdf = script_dir / "catalogue_SU.pdf"
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    elif default_pdf.exists():
        pdf_path = str(default_pdf)
        print(f"\nPDF par défaut : {pdf_path}")
    else:
        pdf_path = input("\nChemin vers le PDF : ").strip().strip('"')

    if not Path(pdf_path).exists():
        print(f"[ERREUR] Fichier introuvable : {pdf_path}")
        sys.exit(1)

    api_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    if not api_key:
        try:
            api_key = getpass.getpass("\nClé API Gemini : ").strip()
        except (EOFError, OSError):
            api_key = ""
    # Sous Windows / terminaux intégrés, getpass renvoie souvent "" même si la saisie semble affichée.
    if not api_key:
        api_key = input("\nClé API Gemini (saisie visible) : ").strip().strip('"')
    if not api_key:
        print("[ERREUR] Clé API vide. Définissez GEMINI_API_KEY ou saisissez la clé.")
        sys.exit(1)

    output_dir = Path(pdf_path).parent / "resultats_extraction_pdf_7"

    extract_catalogue(pdf_path=pdf_path, api_key=api_key, output_dir=str(output_dir))
