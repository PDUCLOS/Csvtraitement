# CSV Traitement

Outil de détection et correction automatique des anomalies dans les fichiers tabulaires volumineux (500K+ lignes).

## Problème résolu

Les exports issus de systèmes ERP (SAP, etc.) présentent souvent des défauts :
- **Décalages de colonnes** — une cellule vide ou un texte contenant le séparateur décale toutes les colonnes
- **Formats incohérents** — dates en `00.00.0000`, décimales mixtes FR/US, valeurs masquées (N/A, NULL, #REF!)
- **Séparateurs ambigus** — point-virgule, virgule, tabulation selon la source

CSV Traitement détecte ces problèmes, les visualise et propose des corrections que vous validez avant export.

## Formats supportés

### En entrée (lecture)
| Format | Extensions | Bibliothèque | Requis |
|--------|-----------|--------------|--------|
| CSV / TSV | `.csv`, `.txt`, `.tsv` | csv (stdlib) | Inclus |
| Excel (nouveau) | `.xlsx`, `.xlsm` | openpyxl | Inclus |
| Excel (ancien) | `.xls` | xlrd | Optionnel |
| ODS (LibreOffice) | `.ods` | odfpy | Optionnel |
| JSON tabulaire | `.json` | json (stdlib) | Inclus |
| Apache Parquet | `.parquet`, `.pq` | pyarrow | Optionnel |

### En sortie (export)
| Format | Description | Requis |
|--------|------------|--------|
| **XLSX** | Excel typé avec cellules corrigées en surbrillance (défaut) | Inclus |
| **CSV** | Texte délimité avec séparateur configurable | Inclus |
| **JSON** | Array d'objets — pour intégration web/API | Inclus |
| **ODS** | LibreOffice / OpenOffice | Optionnel (odfpy) |
| **Parquet** | Format columnar Apache — performant pour Big Data | Optionnel (pyarrow) |
| **SQL** | Fichier `.sql` — CREATE TABLE + INSERT INTO (PostgreSQL/MySQL) | Inclus |
| **SQLite** | Base locale `.db` — zéro configuration | Inclus (sqlite3) |
| **PostgreSQL** | Insertion directe sur serveur PostgreSQL | Optionnel (psycopg2) |
| **Snowflake** | Export vers cloud data warehouse Snowflake | Optionnel (snowflake-connector-python) |
| **Databricks** | Export vers Databricks SQL warehouse | Optionnel (databricks-sql-connector) |

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
# Analyse et correction (tous formats : CSV, XLSX, XLS, ODS, JSON, Parquet)
python main.py cli --file export.csv                    # Analyser un CSV
python main.py cli --file rapport.xlsx                  # Analyser un Excel
python main.py cli --file rapport.xlsx --sheet "Feuil2" # Choisir la feuille
python main.py cli --file data.ods                      # Analyser un fichier ODS
python main.py cli --file data.json                     # Analyser un JSON tabulaire
python main.py cli --file data.parquet                  # Analyser un Parquet
python main.py cli --file export.csv --stats            # Statistiques détaillées

# Correction + export multi-format
python main.py cli --file export.csv --fix -o out.xlsx                # Export XLSX (défaut)
python main.py cli --file export.csv --fix -o out.csv --format csv    # Export CSV
python main.py cli --file export.csv --fix -o out.json --format json  # Export JSON
python main.py cli --file export.csv --fix -o out.ods --format ods    # Export ODS
python main.py cli --file export.csv --fix -o out.parquet --format parquet  # Export Parquet
python main.py cli --file export.csv --fix -o out.csv --format csv --csv-separator "|"  # CSV avec pipe

# Export SQL / bases de données
python main.py cli --file data.csv --fix -o out.sql --format sql              # Fichier SQL
python main.py cli --file data.csv --fix -o out.sql --format sql --table-name clients  # Table nommée
python main.py cli --file data.csv --fix -o out.db --format sqlite            # SQLite local
python main.py cli --file data.csv --fix --format postgres --pg-host localhost --pg-database mydb  # PostgreSQL direct
python main.py cli --file data.csv --fix --format snowflake --sf-account xy12345 --sf-user usr --sf-password pwd  # Snowflake
python main.py cli --file data.csv --fix --format databricks --db-host adb-xxx.net --db-http-path /sql/1.0/warehouses/x --db-token tok  # Databricks
python main.py cli --batch /exports/ --fix -o /sortie/  # Traitement batch
python main.py cli --file export.csv --report rapport.json  # Rapport JSON

# Import depuis bases de données cloud
python main.py cli --sf-query "SELECT * FROM table" --sf-account xy12345 --sf-user usr --sf-password pwd -o data.csv
python main.py cli --db-query "SELECT * FROM table" --db-host adb-xxx.net --db-http-path /path --db-token tok -o data.csv

# Qualité et doublons
python main.py cli --file data.csv --quality            # Score de qualité 0-100
python main.py cli --file data.csv --dedup              # Détection doublons (exact + fuzzy)

# Comparaison et rapprochement (tous formats)
python main.py cli --file v1.csv --compare v2.xlsx      # Diff CSV vs Excel
python main.py cli --file factures.csv --reconcile paiements.csv --join-columns 0,1  # Rapprochement

# Fusion de fichiers (tous formats)
python main.py cli --merge fichier1.csv fichier2.xlsx fichier3.ods -o fusionne.csv

# Cloud
python main.py cli --cloud                              # Lister les emplacements cloud détectés
```

## Fonctionnalités

### Core
- **Lecture multi-format** : CSV, XLSX, XLS, ODS, JSON, Parquet — conversion automatique
- **Sélection de feuille** pour Excel et ODS (multi-feuilles)
- Détection automatique : encodage (BOM), séparateur, nombre de colonnes
- Profiling de colonnes : 9 types (date, entier, décimal, texte, booléen, masqué...)
- Support CSV complet : guillemets, retours à la ligne dans les champs
- Détection et localisation des décalages colonne par colonne
- Nettoyage : dates invalides, décimales FR/US, parenthèses comptables, valeurs masquées
- Statistiques : min/max/moyenne/médiane, outliers IQR, doublons, taux de remplissage
- **Export multi-format** : XLSX, CSV, JSON, ODS, Parquet, SQL, SQLite, PostgreSQL, Snowflake, Databricks
- **Import depuis bases de données** : Snowflake, Databricks (requête SQL → CSV)
- Export XLSX typé avec cellules corrigées en surbrillance + onglet rapport
- Export CSV avec séparateur configurable (§ par défaut — rare, évite les conflits)
- Écriture via fichier temporaire local (compatible lecteurs réseau/cloud)

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
- **Éditeur de données** : grille éditable comme Excel (double-clic, Tab, Entrée)
- Preview avec pagination, recherche par contenu, filtre par statut
- Drag & drop de fichier
- Undo/redo des corrections
- Dialogue d'export : choix du format (10 formats) et du séparateur CSV
- Import depuis Snowflake/Databricks (dialogue de connexion intégré)
- Ouverture automatique du fichier exporté (optionnel)
- Templates de profil réutilisables (JSON)
- Mode CLI + batch multi-fichiers

### Technique
- Streaming par chunks — gère les fichiers 500K+ lignes sans saturer la RAM
- Support des guillemets CSV (champs contenant le séparateur)
- Gestion BOM UTF-8/UTF-16 + encodages exotiques
- Règles de validation custom par colonne
- Aucune dépendance externe pour le fuzzy matching (difflib standard)
- **Sécurité** : validation des identifiants SQL, requêtes import en lecture seule, requêtes paramétrées

## Architecture

```
core/
├── detector.py       → Détection séparateur + profiling (multi-format)
├── analyzer.py       → Analyse décalages + conformité
├── fixer.py          → Corrections dates/décimales/réalignement
├── exporter.py       → Export XLSX/CSV/JSON/ODS/Parquet + rapport
├── statistics.py     → Stats colonnes (min/max/outliers/doublons)
├── quality.py        → Score de qualité des données (0-100)
├── dedup.py          → Détection doublons (exact + fuzzy)
├── comparator.py     → Diff entre deux fichiers
├── reconciler.py     → Rapprochement / réconciliation
├── merger.py         → Fusion multi-fichiers + contrôle compatibilité
├── columns.py        → Réordonnancement / suppression / renommage colonnes
├── transformer.py    → Règles de transformation personnalisées
├── normalizer.py     → Normalisation avancée du texte
├── anonymizer.py     → Anonymisation données sensibles
└── cli.py            → Mode CLI + batch

ui/
├── dashboard.py      → Dashboard Tkinter dark theme + 8 onglets
├── preview.py        → Preview avec recherche/filtre/pagination
└── editable_grid.py  → Grille éditable (modification des données)

utils/
├── patterns.py       → Regex + types (date/nombre/booléen/masqué)
├── chunked_reader.py → Streaming + BOM + guillemets CSV
├── excel_reader.py   → Lecteur XLSX/XLSM (openpyxl) + XLS (xlrd)
├── ods_reader.py     → Lecteur ODS (odfpy)
├── json_reader.py    → Lecteur JSON tabulaire
├── parquet_reader.py → Lecteur Parquet (pyarrow)
├── universal_reader.py → Dispatch automatique par extension
├── validators.py     → Validation par colonne + validation croisée inter-colonnes
├── templates.py      → Templates profil JSON réutilisables
├── history.py        → Undo/redo historique corrections
├── cloud.py          → Connecteurs cloud (OneDrive, SharePoint, pCloud, etc.)
└── db_connectors.py  → Connecteurs Snowflake + Databricks (import/export)
```

## Dépendances

**Requises :**
- Python 3.10+
- xlsxwriter (export XLSX)
- chardet (détection encodage)
- openpyxl (lecture XLSX/XLSM)

**Optionnelles :**
- xlrd (lecture XLS ancien format)
- odfpy (lecture/écriture ODS)
- pyarrow (lecture/écriture Parquet)
- psycopg2-binary (export PostgreSQL direct)
- snowflake-connector-python (import/export Snowflake)
- databricks-sql-connector (import/export Databricks)
- tkinterdnd2 (drag & drop GUI)

```bash
# Installation complète
pip install xlsxwriter chardet openpyxl xlrd odfpy pyarrow psycopg2-binary snowflake-connector-python databricks-sql-connector
```

## Licence

MIT
