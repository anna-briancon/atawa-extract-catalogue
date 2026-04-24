# Extract Catalogue

Application pour extraire automatiquement des produits depuis un catalogue PDF.

## Prerequis

- Python 3.10+ recommande
- `pip`
- (Optionnel mais conseillé) environnement virtuel `venv`

## 1) Telecharger le projet

```bash
git clone <URL_DU_REPO>
cd atawa-extract-catalogue
```

## 2) Creer et activer l'environnement virtuel

### macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

### Windows (PowerShell)

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

## 3) Installer les dependances

```bash
pip install -r requirements.txt
```

## 4) Configurer les variables d'environnement

Cree un fichier `.env` a partir de `.env.example` :

```bash
cp .env.example .env
```

Puis remplace la valeur de `GEMINI_API_KEY` par ta cle API.

Exemple :

```env
GEMINI_API_KEY=ta_cle_api_ici
```

## 5) Lancer le projet

```bash
python3 app.py
```

Ensuite ouvre ton navigateur sur :

- http://127.0.0.1:5000

## Utilisation

1. Ouvre l'interface web.
2. Soit upload un fichier PDF, soit clique sur **Utiliser le PDF par defaut**.
3. Attends la fin du traitement.
4. Consulte les resultats extraits et l'aperçu du PDF utilisé.

## Structure du projet

- `app.py` : serveur Flask et routes API
- `extract.py` : logique d'extraction
- `templates/` : pages HTML
- `static/` : fichiers CSS/JS
- `uploads/` : PDF envoyes (runtime)
- `resultats/` : resultats d'extraction (runtime)