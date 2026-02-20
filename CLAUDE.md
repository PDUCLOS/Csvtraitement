# CSV Traitement - Claude Code Project

## Objectif
Outil Python avec GUI (Tkinter dark theme) et CLI pour détecter et corriger les anomalies dans des fichiers tabulaires volumineux (500K+ lignes). Supporte en entrée : CSV, XLSX, XLS, ODS, JSON, Parquet. En sortie : XLSX, CSV, JSON, ODS, Parquet. Inclut : score de qualité, fuzzy dedup, comparaison/diff, rapprochement, fusion, anonymisation, normalisation texte, gestion colonnes et connexion cloud.

## Architecture

```
Csvtraitement/
├── CLAUDE.md                  # Instructions Claude Code
├── README.md
├── requirements.txt
├── setup.py
├── main.py                    # Entrée : GUI (défaut) ou CLI
├── core/
│   ├── __init__.py
│   ├── detector.py            # Détection séparateur + profiling (multi-format)
│   ├── analyzer.py            # Analyse décalages + conformité
│   ├── fixer.py               # Corrections dates/décimales/réalignement
│   ├── exporter.py            # Export XLSX/CSV/JSON/ODS/Parquet + rapport
│   ├── statistics.py          # Stats colonnes (min/max/outliers/doublons)
│   ├── quality.py             # Score de qualité des données (0-100, note A-F)
│   ├── dedup.py               # Détection doublons (exact + fuzzy matching)
│   ├── comparator.py          # Diff/comparaison entre deux fichiers CSV
│   ├── reconciler.py          # Rapprochement / réconciliation entre fichiers
│   ├── merger.py              # Fusion multi-fichiers + contrôle compatibilité
│   ├── columns.py             # Réordonnancement / suppression / renommage colonnes
│   ├── transformer.py         # Règles de transformation (regex, split, merge, mapping)
│   ├── normalizer.py          # Normalisation texte (casse, accents, invisibles, guillemets)
│   ├── anonymizer.py          # Anonymisation données sensibles (email, tel, IBAN)
│   └── cli.py                 # Mode CLI + batch multi-fichiers
├── ui/
│   ├── __init__.py
│   ├── dashboard.py           # Dashboard Tkinter dark theme + 8 onglets
│   └── preview.py             # Preview avec recherche/filtre/pagination
├── utils/
│   ├── __init__.py
│   ├── patterns.py            # Regex + types (date/nombre/booléen/masqué)
│   ├── chunked_reader.py      # Streaming + BOM + guillemets CSV + writer
│   ├── excel_reader.py        # Lecteur XLSX (openpyxl) + XLS (xlrd)
│   ├── ods_reader.py          # Lecteur ODS (odfpy)
│   ├── json_reader.py         # Lecteur JSON tabulaire
│   ├── parquet_reader.py      # Lecteur Parquet (pyarrow)
│   ├── universal_reader.py    # Dispatch automatique par extension
│   ├── validators.py          # Validation par colonne + validation croisée inter-colonnes
│   ├── templates.py           # Templates profil JSON réutilisables
│   ├── history.py             # Undo/redo historique corrections
│   └── cloud.py               # Connecteurs cloud (OneDrive, SharePoint, pCloud, etc.)
├── tests/
│   ├── fixtures/
│   ├── test_detector.py
│   ├── test_analyzer.py
│   └── test_fixer.py
└── docs/
    └── specs.md
```

## Commandes

```bash
# GUI
python main.py
python main.py --file data.csv

# CLI — Analyse et correction (tous formats : CSV, XLSX, XLS, ODS, JSON, Parquet)
python main.py cli --file data.csv                          # Analyser un CSV
python main.py cli --file data.xlsx                         # Analyser un Excel
python main.py cli --file data.xlsx --sheet "Feuil2"        # Choisir la feuille
python main.py cli --file data.csv --stats                  # + Statistiques
python main.py cli --file data.csv --fix -o out.xlsx        # Export XLSX (défaut)
python main.py cli --file data.csv --fix -o out.json --format json    # Export JSON
python main.py cli --file data.csv --fix -o out.ods --format ods      # Export ODS
python main.py cli --file data.csv --fix -o out.parquet --format parquet  # Export Parquet
python main.py cli --file data.csv --fix -o out.csv --format csv --csv-separator "|"  # CSV avec pipe
python main.py cli --file data.csv --fix -o out.sql --format sql              # Export SQL
python main.py cli --file data.csv --fix -o out.db --format sqlite            # Export SQLite
python main.py cli --file data.csv --fix --format postgres --pg-host localhost --pg-database mydb  # PostgreSQL
python main.py cli --batch /dossier/ --fix -o /sortie/      # Batch
python main.py cli --file data.csv --save-template sap_export  # Sauver template
python main.py cli --file data.csv --report rapport.json    # Rapport JSON

# CLI — Qualité et doublons
python main.py cli --file data.csv --quality                # Score de qualité 0-100
python main.py cli --file data.csv --dedup                  # Détection doublons

# CLI — Comparaison et rapprochement
python main.py cli --file v1.csv --compare v2.csv           # Diff entre deux fichiers
python main.py cli --file factures.csv --reconcile paiements.csv --join-columns 0,1

# CLI — Fusion et cloud
python main.py cli --merge f1.csv f2.csv f3.csv -o fusionne.csv
python main.py cli --cloud                                  # Lister emplacements cloud

# Tests
pytest tests/ -v
```

## Règles de développement

### Performance (CRITIQUE)
- TOUJOURS streaming par chunks (10K lignes) pour les 500K+
- Ne JAMAIS charger un fichier entier en RAM
- xlsxwriter avec constant_memory=True pour l'écriture
- Profiling sur échantillon (1000 premières + 1000 aléatoires)

### CSV parsing (CRITIQUE)
- TOUJOURS utiliser csv.reader ou parse_csv_line() pour gérer les guillemets
- Ne JAMAIS faire line.split(sep) sans passer par le parser CSV
- Gérer les retours à la ligne dans les champs quotés
- Gérer le BOM UTF-8/UTF-16 via detect_encoding()

### Types détectés (utils/patterns.py)
DATE, INTEGER, DECIMAL, TEXT_SHORT, TEXT_LONG, EMPTY, DATE_INVALID, MASKED_EMPTY, BOOLEAN

### Formats supportés
- Dates : DD.MM.YYYY, DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY + datetime
- Décimales FR : 1.234.567,89 | Décimales US : 1,234,567.89
- Parenthèses comptables : (1.234,56) → -1234.56
- Masqués : N/A, NULL, #REF!, -, ??, etc.
- Booléens : TRUE/FALSE, OUI/NON, Y/N

### Interface
- Thème sombre (palette Catppuccin Mocha)
- 8 onglets : Diagnostic, Colonnes, Anomalies, Statistiques, Qualité, Doublons, Corrections, Historique
- Boutons : Charger, Analyser, Stats, Preview, Corriger, Undo, Export, Qualité, Doublons, Comparer, Fusionner, Anonymiser, Cloud
- Drag & drop (si tkinterdnd2 installé, sinon double-clic)
- Preview avec pagination, recherche, filtre (conformes/non conformes/corrigées)
- Undo/redo sur les corrections

### Formats d'entrée
- CSV/TSV : natif via csv.reader (chunked_reader.py)
- XLSX/XLSM : openpyxl en mode read_only (streaming), conversion auto → CSV temporaire
- XLS : xlrd (optionnel), conversion auto → CSV temporaire
- ODS : odfpy (optionnel), conversion auto → CSV temporaire
- JSON : json stdlib, détection structure (array d'objets, array d'arrays, objet avec clé data)
- Parquet : pyarrow (optionnel), streaming par batch via iter_batches
- Dispatch automatique par extension via universal_reader.py

### Formats de sortie
- XLSX : xlsxwriter avec constant_memory (streaming), typage et highlighting
- CSV : séparateur configurable (§, |, tab, ;, , ou custom)
- JSON : array d'objets ou array d'arrays
- ODS : odfpy avec style header bold
- Parquet : pyarrow.parquet.write_table
- SQL : fichier .sql avec CREATE TABLE + INSERT INTO par batch
- SQLite : sqlite3 stdlib, zéro dépendance externe
- PostgreSQL : psycopg2 (optionnel), connexion directe, INSERT par batch

### Modules data
- `quality.py` : Score 0-100 pondéré (remplissage 30%, types 25%, formats 20%, outliers 15%, diversité 10%)
- `dedup.py` : Hash exact + fuzzy via difflib.SequenceMatcher + blocking par préfixe
- `comparator.py` : Indexation fichier A par clé, parcours fichier B, diff cellule par cellule
- `reconciler.py` : Jointure sur colonnes-clés, comparaison montants avec tolérance
- `merger.py` : Contrôle compatibilité (noms, nombre colonnes), réalignement auto, fusion
- `columns.py` : Opérations sur colonnes (reorder, delete, rename) et lignes (filtrage)
- `transformer.py` : Regex replace, split, merge, value map — sérialisable JSON
- `normalizer.py` : Casse, accents (unicodedata), invisibles, guillemets typographiques
- `anonymizer.py` : Détection (email, tel, IBAN, CB, sécu) + masquage (partiel/complet/hash/pseudonyme)
- `validators.py` : Validation croisée (dates, arithmétique, conditionnel, exclusion mutuelle)
- `cloud.py` : Détection auto des chemins sync (OneDrive, SharePoint, pCloud, Google Drive, Dropbox)

### Templates JSON (~/.csvtraitement/templates/)
- Sauvegarde/rechargement de profils pour exports récurrents
- Inclut les règles de validation custom et de transformation

### Style de code
- Python 3.10+, type hints, docstrings Google, logging (pas de print sauf CLI)
- Tests pytest
