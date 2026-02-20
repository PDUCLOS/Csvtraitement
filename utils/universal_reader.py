"""Lecteur universel de fichiers tabulaires.

Dispatche automatiquement vers le bon lecteur selon l'extension :
- .csv, .txt, .tsv → chunked_reader (CSV natif)
- .xlsx, .xlsm      → excel_reader (openpyxl)
- .xls              → excel_reader (xlrd)
- .ods              → ods_reader (odfpy)
- .json             → json_reader (json natif)
- .parquet, .pq     → parquet_reader (pyarrow)

L'objectif est de convertir tout format en (header, rows) pour alimenter
le pipeline de détection/analyse/correction existant.
"""

import csv
import io
import logging
import tempfile
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# Extensions supportées par catégorie
CSV_EXTENSIONS = {'.csv', '.txt', '.tsv'}
EXCEL_EXTENSIONS = {'.xls', '.xlsx', '.xlsm'}
ODS_EXTENSIONS = {'.ods'}
JSON_EXTENSIONS = {'.json'}
PARQUET_EXTENSIONS = {'.parquet', '.pq'}

ALL_SUPPORTED_EXTENSIONS = (
    CSV_EXTENSIONS | EXCEL_EXTENSIONS | ODS_EXTENSIONS
    | JSON_EXTENSIONS | PARQUET_EXTENSIONS
)

# Labels pour les dialogues de fichiers
FILETYPES_ALL = [
    ("Tous les formats supportés", "*.csv *.txt *.tsv *.xlsx *.xlsm *.xls *.ods *.json *.parquet *.pq"),
    ("CSV / TSV", "*.csv *.txt *.tsv"),
    ("Excel", "*.xlsx *.xlsm *.xls"),
    ("ODS (LibreOffice)", "*.ods"),
    ("JSON", "*.json"),
    ("Parquet", "*.parquet *.pq"),
    ("Tous", "*.*"),
]


def get_file_format(filepath: Path) -> str:
    """Retourne le format du fichier basé sur l'extension.

    Returns:
        'csv', 'excel', 'ods', 'json', 'parquet' ou 'unknown'.
    """
    ext = filepath.suffix.lower()
    if ext in CSV_EXTENSIONS:
        return 'csv'
    if ext in EXCEL_EXTENSIONS:
        return 'excel'
    if ext in ODS_EXTENSIONS:
        return 'ods'
    if ext in JSON_EXTENSIONS:
        return 'json'
    if ext in PARQUET_EXTENSIONS:
        return 'parquet'
    return 'unknown'


def is_supported(filepath: Path) -> bool:
    """Vérifie si le format du fichier est supporté."""
    return filepath.suffix.lower() in ALL_SUPPORTED_EXTENSIONS


def read_any(
    filepath: Path,
    sheet_name: Optional[str] = None,
    sheet_index: int = 0,
    data_key: Optional[str] = None,
) -> tuple[list[str], list[list[str]], int]:
    """Lit n'importe quel fichier tabulaire supporté.

    Args:
        filepath: Chemin du fichier.
        sheet_name: Nom de la feuille (Excel/ODS uniquement).
        sheet_index: Index de la feuille (Excel/ODS).
        data_key: Clé JSON pour les données (JSON uniquement).

    Returns:
        Tuple (header, rows, total_lines).
    """
    filepath = Path(filepath)
    fmt = get_file_format(filepath)

    if fmt == 'csv':
        return _read_csv_as_rows(filepath)
    elif fmt == 'excel':
        from utils.excel_reader import read_excel
        return read_excel(filepath, sheet_name, sheet_index)
    elif fmt == 'ods':
        from utils.ods_reader import read_ods
        return read_ods(filepath, sheet_name, sheet_index)
    elif fmt == 'json':
        from utils.json_reader import read_json
        return read_json(filepath, data_key)
    elif fmt == 'parquet':
        from utils.parquet_reader import read_parquet
        return read_parquet(filepath)
    else:
        raise ValueError(
            f"Format non supporté : {filepath.suffix}. "
            f"Formats acceptés : {', '.join(sorted(ALL_SUPPORTED_EXTENSIONS))}"
        )


def _read_csv_as_rows(filepath: Path) -> tuple[list[str], list[list[str]], int]:
    """Lit un CSV et retourne (header, rows, total_lines) pour uniformité."""
    from utils.chunked_reader import detect_encoding, read_chunks_quoted

    encoding = detect_encoding(filepath)

    # Détecter le séparateur
    from core.detector import detect_separator
    separator, _ = detect_separator(filepath, encoding)

    header = []
    rows = []
    for chunk in read_chunks_quoted(filepath, separator, encoding):
        for row in chunk:
            if not header:
                header = row
            else:
                rows.append(row)

    total_lines = len(rows) + 1
    return header, rows, total_lines


def convert_to_csv(
    filepath: Path,
    output_path: Optional[Path] = None,
    separator: str = ';',
    sheet_name: Optional[str] = None,
    sheet_index: int = 0,
    data_key: Optional[str] = None,
) -> Path:
    """Convertit un fichier tabulaire en CSV temporaire pour le pipeline.

    Cette fonction est la clé de l'intégration : elle produit un CSV
    que le détecteur/analyseur/fixer existant peut traiter nativement.

    Args:
        filepath: Chemin du fichier source.
        output_path: Chemin CSV de sortie (auto si None).
        separator: Séparateur CSV de sortie.
        sheet_name: Nom de la feuille (Excel/ODS).
        sheet_index: Index de la feuille.
        data_key: Clé JSON.

    Returns:
        Chemin du fichier CSV (temporaire ou spécifié).
    """
    filepath = Path(filepath)
    fmt = get_file_format(filepath)

    # Si c'est déjà un CSV, retourner tel quel
    if fmt == 'csv':
        logger.info(f"Fichier déjà au format CSV : {filepath.name}")
        return filepath

    # Lire les données
    header, rows, total_lines = read_any(
        filepath, sheet_name, sheet_index, data_key
    )

    # Déterminer le chemin de sortie
    if output_path is None:
        fd, tmp_path = tempfile.mkstemp(suffix='.csv', prefix='csvtraitement_conv_')
        import os
        os.close(fd)
        output_path = Path(tmp_path)

    # Écrire le CSV
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=separator, quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)

    logger.info(
        f"Conversion {fmt.upper()} → CSV : {filepath.name} → {output_path.name} "
        f"({total_lines} lignes, sep='{separator}')"
    )
    return output_path


def list_sheets_any(filepath: Path) -> list[str]:
    """Liste les feuilles d'un fichier Excel ou ODS.

    Returns:
        Liste de noms de feuilles, ou liste vide pour les autres formats.
    """
    fmt = get_file_format(filepath)
    if fmt == 'excel':
        from utils.excel_reader import list_sheets
        return list_sheets(filepath)
    elif fmt == 'ods':
        from utils.ods_reader import list_sheets_ods
        return list_sheets_ods(filepath)
    return []


def get_format_info(filepath: Path) -> dict:
    """Retourne des informations sur le format du fichier.

    Returns:
        Dict avec 'format', 'extension', 'has_sheets', 'description'.
    """
    filepath = Path(filepath)
    fmt = get_file_format(filepath)
    ext = filepath.suffix.lower()

    descriptions = {
        'csv': 'Fichier CSV / texte délimité',
        'excel': 'Classeur Microsoft Excel',
        'ods': 'Classeur LibreOffice / OpenOffice',
        'json': 'Fichier JSON tabulaire',
        'parquet': 'Fichier Apache Parquet (columnar)',
        'unknown': 'Format non reconnu',
    }

    return {
        'format': fmt,
        'extension': ext,
        'has_sheets': fmt in ('excel', 'ods'),
        'description': descriptions.get(fmt, 'Inconnu'),
        'supported': fmt != 'unknown',
    }
