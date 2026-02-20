# CSV Traitement

Outil de détection et correction automatique des anomalies dans les fichiers CSV volumineux (500K+ lignes).

## Problème résolu

Les exports CSV issus de systèmes ERP (SAP, etc.) présentent souvent des défauts :
- **Décalages de colonnes** — une cellule vide ou un texte contenant le séparateur décale toutes les colonnes
- **Formats incohérents** — dates en `00.00.0000`, décimales mixtes FR/US, valeurs masquées (N/A, NULL, #REF!)
- **Séparateurs ambigus** — point-virgule, virgule, tabulation selon la source

CSV Traitement détecte ces problèmes, les visualise et propose des corrections que vous validez avant export.

## Installation

```bash
git clone https://github.com/PDUCLOS/Csvtraitement.git
cd Csvtraitement
pip install -e .
```

## Utilisation

### Interface graphique
```bash
python main.py                    # Lancer le dashboard
python main.py --file export.csv  # Charger directement un fichier
```

### Ligne de commande
```bash
# Analyse et correction
python main.py cli --file export.csv                    # Analyser
python main.py cli --file export.csv --stats            # Statistiques détaillées
python main.py cli --file export.csv --fix -o out.xlsx  # Corriger + exporter XLSX
python main.py cli --file export.csv --fix -o out.csv --format csv                  # Exporter CSV (séparateur § par défaut)
python main.py cli --file export.csv --fix -o out.csv --format csv --csv-separator "|"  # CSV avec séparateur pipe
python main.py cli --batch /exports/ --fix -o /sortie/  # Traitement batch
python main.py cli --file export.csv --report rapport.json  # Rapport JSON

# Qualité et doublons
python main.py cli --file data.csv --quality            # Score de qualité 0-100
python main.py cli --file data.csv --dedup              # Détection doublons (exact + fuzzy)

# Comparaison et rapprochement
python main.py cli --file v1.csv --compare v2.csv       # Diff entre deux fichiers
python main.py cli --file factures.csv --reconcile paiements.csv --join-columns 0,1  # Rapprochement

# Fusion de fichiers
python main.py cli --merge fichier1.csv fichier2.csv fichier3.csv -o fusionne.csv  # Fusion multi-fichiers

# Cloud
python main.py cli --cloud                              # Lister les emplacements cloud détectés
```

## Fonctionnalités

### Core
- Détection automatique : encodage (BOM), séparateur, nombre de colonnes
- Profiling de colonnes : 9 types (date, entier, décimal, texte, booléen, masqué...)
- Support CSV complet : guillemets, retours à la ligne dans les champs
- Détection et localisation des décalages colonne par colonne
- Nettoyage : dates invalides, décimales FR/US, parenthèses comptables, valeurs masquées
- Statistiques : min/max/moyenne/médiane, outliers IQR, doublons, taux de remplissage
- Export XLSX typé avec cellules corrigées en surbrillance + onglet rapport
- Export CSV avec séparateur configurable (§ par défaut — rare, évite les conflits avec les données)
- Séparateurs CSV disponibles : `§`, `|`, tabulation, `;`, `,` ou caractère personnalisé
- Écriture via fichier temporaire local (compatible lecteurs réseau/cloud : pCloud, Google Drive, etc.)

### Qualité des données
- **Score de qualité 0-100** par colonne et global (note A-F)
- Composantes : remplissage, cohérence de type, outliers, homogénéité des formats, diversité
- Pénalités pour doublons et lignes vides
- Détection des formats mixtes (dates DD/MM vs MM/DD, décimales FR/US)

### Doublons
- **Doublons exacts** par hash sur colonnes-clés configurables
- **Fuzzy matching** (similarité > 85%) via difflib — détecte "Düpont" vs "Dupont"
- Normalisation préalable (accents, casse, espaces)
- Groupement des doublons avec affichage des différences
- Stratégie de sélection : premier, dernier, ou plus complet

### Comparaison de fichiers
- Diff entre deux versions d'un export (ajoutées, supprimées, modifiées)
- Comparaison cellule par cellule avec détail des changements
- Colonne de clé primaire configurable
- Score de similarité global

### Rapprochement / Réconciliation
- Jointure sur une ou plusieurs colonnes-clés
- Comparaison de montants avec tolérance configurable
- Statut par ligne : Matché, Écart, Non trouvé (gauche/droite)
- Calcul de l'écart total

### Fusion de fichiers
- Fusion verticale de N fichiers CSV
- Contrôle de compatibilité avant fusion (colonnes, noms, types)
- Détection des colonnes permutées et réalignement automatique
- Option de suppression des lignes vides et en erreur

### Gestion des colonnes
- Réordonnancement des colonnes
- Suppression de colonnes ou de lignes (vides, erreurs, par condition)
- Renommage de colonnes
- Suppression de lignes par filtre (vides, masquées, condition)

### Transformations personnalisées
- Regex find/replace par colonne
- Split colonne ("Nom Prénom" → Nom + Prénom)
- Merge colonnes (Ville + CP → Adresse)
- Mapping de valeurs (OUI/NON → 1/0)
- Règles sauvegardables dans les templates JSON

### Validation croisée inter-colonnes
- Date début < Date fin
- Vérification arithmétique (HT × TVA = TTC, avec tolérance)
- Champ requis conditionnellement (si A rempli → B requis)
- Exclusion mutuelle entre colonnes
- Cohérence entre colonnes (pays ↔ format code postal)

### Anonymisation
- Détection automatique : emails, téléphones, IBAN, cartes bancaires, numéro sécu
- Masquage partiel (j***@m***.com), complet, hash SHA-256 ou pseudonyme
- Configurable par colonne

### Normalisation du texte
- Casse uniforme (UPPER, lower, Title Case) par colonne
- Suppression des accents (unicodedata)
- Nettoyage des caractères invisibles (zero-width, soft hyphen, BOM)
- Uniformisation des apostrophes et guillemets typographiques
- Normalisation des espaces exotiques (insécables, etc.)

### Connexion cloud
- Détection automatique des clients de synchronisation installés
- Support : OneDrive, SharePoint, pCloud, Google Drive, Dropbox
- Navigation dans les dossiers cloud depuis l'interface
- Compatible Windows, macOS, Linux

### Interface
- Thème sombre (Catppuccin Mocha)
- 8 onglets : Diagnostic, Colonnes, Anomalies, Statistiques, Qualité, Doublons, Corrections, Historique
- Preview avec pagination, recherche par contenu, filtre par statut
- Drag & drop de fichier
- Undo/redo des corrections
- Dialogue d'export : choix du format (XLSX/CSV) et du séparateur CSV
- Ouverture automatique du fichier exporté (optionnel)
- Templates de profil réutilisables (JSON)
- Mode CLI + batch multi-fichiers

### Technique
- Streaming par chunks — gère les fichiers 500K+ lignes sans saturer la RAM
- Support des guillemets CSV (champs contenant le séparateur)
- Gestion BOM UTF-8/UTF-16 + encodages exotiques
- Règles de validation custom par colonne
- Aucune dépendance externe pour le fuzzy matching (difflib standard)

## Architecture

```
core/
├── detector.py       → Détection séparateur + profiling colonnes
├── analyzer.py       → Analyse décalages + conformité
├── fixer.py          → Corrections dates/décimales/réalignement
├── exporter.py       → Export XLSX/CSV typé + rapport
├── statistics.py     → Stats colonnes (min/max/outliers/doublons)
├── quality.py        → Score de qualité des données (0-100)
├── dedup.py          → Détection doublons (exact + fuzzy)
├── comparator.py     → Diff entre deux fichiers CSV
├── reconciler.py     → Rapprochement / réconciliation
├── merger.py         → Fusion multi-fichiers + contrôle compatibilité
├── columns.py        → Réordonnancement / suppression / renommage colonnes
├── transformer.py    → Règles de transformation personnalisées
├── normalizer.py     → Normalisation avancée du texte
├── anonymizer.py     → Anonymisation données sensibles
└── cli.py            → Mode CLI + batch

ui/
├── dashboard.py      → Dashboard Tkinter dark theme + 8 onglets
└── preview.py        → Preview avec recherche/filtre/pagination

utils/
├── patterns.py       → Regex + types (date/nombre/booléen/masqué)
├── chunked_reader.py → Streaming + BOM + guillemets CSV
├── validators.py     → Validation par colonne + validation croisée inter-colonnes
├── templates.py      → Templates profil JSON réutilisables
├── history.py        → Undo/redo historique corrections
└── cloud.py          → Connecteurs cloud (OneDrive, SharePoint, pCloud, etc.)
```

## Dépendances

- Python 3.10+
- xlsxwriter
- chardet
- tkinterdnd2 (optionnel, pour le drag & drop)

## Licence

MIT
