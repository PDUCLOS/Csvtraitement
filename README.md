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
python main.py cli --file export.csv                    # Analyser
python main.py cli --file export.csv --stats            # Statistiques détaillées
python main.py cli --file export.csv --fix -o out.xlsx  # Corriger + exporter
python main.py cli --batch /exports/ --fix -o /sortie/  # Traitement batch
python main.py cli --file export.csv --report rapport.json  # Rapport JSON
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

### Interface
- Thème sombre
- 6 onglets : Diagnostic, Colonnes, Anomalies, Statistiques, Corrections, Historique
- Preview avec pagination, recherche par contenu, filtre par statut
- Drag & drop de fichier
- Undo/redo des corrections
- Templates de profil réutilisables (JSON)
- Mode CLI + batch multi-fichiers

### Technique
- Streaming par chunks — gère les fichiers 500K+ lignes sans saturer la RAM
- Support des guillemets CSV (champs contenant le séparateur)
- Gestion BOM UTF-8/UTF-16 + encodages exotiques
- Règles de validation custom par colonne

## Architecture

```
core/         → Moteur : détection, analyse, correction, export, stats, CLI
ui/           → Interface Tkinter : dashboard + preview
utils/        → Outils : patterns regex, reader streaming, templates, validators, historique
```

## Dépendances

- Python 3.10+
- xlsxwriter
- chardet
- tkinterdnd2 (optionnel, pour le drag & drop)

## Licence

MIT
