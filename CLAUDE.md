# CSV Traitement - Claude Code Project

## Objectif
Outil Python avec GUI (Tkinter dark theme) et CLI pour détecter et corriger les anomalies dans des fichiers CSV volumineux (500K+ lignes) : décalages de colonnes, formats de dates incohérents, séparateurs décimaux mixtes (FR/US), valeurs masquées, doublons.

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
│   ├── detector.py            # Détection séparateur + profiling colonnes
│   ├── analyzer.py            # Analyse décalages + conformité
│   ├── fixer.py               # Corrections dates/décimales/réalignement
│   ├── exporter.py            # Export XLSX typé + highlight + rapport
│   ├── statistics.py          # Stats colonnes (min/max/outliers/doublons)
│   └── cli.py                 # Mode CLI + batch multi-fichiers
├── ui/
│   ├── __init__.py
│   ├── dashboard.py           # Dashboard Tkinter dark theme + 6 onglets
│   └── preview.py             # Preview avec recherche/filtre/pagination
├── utils/
│   ├── __init__.py
│   ├── patterns.py            # Regex + types (date/nombre/booléen/masqué)
│   ├── chunked_reader.py      # Streaming + BOM + guillemets CSV + writer
│   ├── validators.py          # Règles validation custom par colonne
│   ├── templates.py           # Templates profil JSON réutilisables
│   └── history.py             # Undo/redo historique corrections
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

# CLI
python main.py cli --file data.csv                          # Analyser
python main.py cli --file data.csv --stats                  # + Statistiques
python main.py cli --file data.csv --fix -o out.xlsx        # Corriger + export
python main.py cli --batch /dossier/ --fix -o /sortie/      # Batch
python main.py cli --file data.csv --save-template sap_export  # Sauver template
python main.py cli --file data.csv --report rapport.json    # Rapport JSON

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
- 6 onglets : Diagnostic, Colonnes, Anomalies, Statistiques, Corrections, Historique
- Drag & drop (si tkinterdnd2 installé, sinon double-clic)
- Preview avec pagination, recherche, filtre (conformes/non conformes/corrigées)
- Undo/redo sur les corrections

### Templates JSON (~/.csvtraitement/templates/)
- Sauvegarde/rechargement de profils pour exports récurrents
- Inclut les règles de validation custom

### Style de code
- Python 3.10+, type hints, docstrings Google, logging (pas de print sauf CLI)
- Tests pytest
