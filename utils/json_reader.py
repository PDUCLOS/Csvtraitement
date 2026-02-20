"""Lecteur de fichiers JSON tabulaires.

Supporte plusieurs structures JSON :
- Array d'objets : [{"col1": "val1", "col2": "val2"}, ...]
- Array d'arrays : [["col1", "col2"], ["val1", "val2"], ...]
- Objet avec clé data : {"data": [...], "columns": [...]}

Pas de dépendance externe (json standard).
"""

import json
import logging
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)


def _flatten_value(value) -> str:
    """Convertit une valeur JSON en string pour le pipeline CSV."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _detect_structure(data) -> str:
    """Détecte la structure du JSON.

    Returns:
        'array_of_objects', 'array_of_arrays', 'object_with_data', ou 'unknown'.
    """
    if isinstance(data, list):
        if not data:
            return 'array_of_arrays'
        if isinstance(data[0], dict):
            return 'array_of_objects'
        if isinstance(data[0], list):
            return 'array_of_arrays'
    if isinstance(data, dict):
        # Chercher une clé contenant un array
        for key in ('data', 'rows', 'records', 'items', 'results'):
            if key in data and isinstance(data[key], list):
                return 'object_with_data'
    return 'unknown'


def read_json(
    filepath: Path,
    data_key: Optional[str] = None,
) -> tuple[list[str], list[list[str]], int]:
    """Lit un fichier JSON tabulaire et retourne header + lignes.

    Args:
        filepath: Chemin du fichier JSON.
        data_key: Clé de l'objet contenant les données (auto-détecté si None).

    Returns:
        Tuple (header, rows, total_lines).
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    structure = _detect_structure(data)
    logger.info(f"JSON structure détectée : {structure}")

    if structure == 'object_with_data':
        # Extraire le tableau depuis l'objet
        if data_key and data_key in data:
            records = data[data_key]
        else:
            # Auto-détecter la clé
            for key in ('data', 'rows', 'records', 'items', 'results'):
                if key in data and isinstance(data[key], list):
                    records = data[key]
                    data_key = key
                    break
            else:
                # Prendre la première clé qui contient un array
                for key, val in data.items():
                    if isinstance(val, list):
                        records = val
                        data_key = key
                        break
                else:
                    raise ValueError("Impossible de trouver un tableau de données dans le JSON")
        logger.info(f"Clé de données utilisée : '{data_key}'")

        # Vérifier si les colonnes sont fournies
        columns_key = None
        for k in ('columns', 'headers', 'fields', 'schema'):
            if k in data and isinstance(data[k], list):
                columns_key = k
                break

        if columns_key and records and isinstance(records[0], dict):
            header = [str(c) for c in data[columns_key]]
        elif records and isinstance(records[0], dict):
            header = list(records[0].keys())
        elif records and isinstance(records[0], list) and columns_key:
            header = [str(c) for c in data[columns_key]]
            # records est déjà un array d'arrays
        elif records and isinstance(records[0], list):
            header = [f"Col_{i}" for i in range(len(records[0]))]
        else:
            header = []

        data = records  # Continuer avec le tableau extrait

    if structure == 'array_of_objects' or (isinstance(data, list) and data and isinstance(data[0], dict)):
        # Collecter toutes les clés (pour gérer les objets hétérogènes)
        all_keys = []
        seen = set()
        for record in data:
            if isinstance(record, dict):
                for key in record:
                    if key not in seen:
                        seen.add(key)
                        all_keys.append(key)

        if structure != 'object_with_data':
            header = all_keys

        rows = []
        for record in data:
            if isinstance(record, dict):
                row = [_flatten_value(record.get(key, '')) for key in all_keys]
            else:
                row = [_flatten_value(record)]
            rows.append(row)

    elif structure == 'array_of_arrays' or (isinstance(data, list) and data and isinstance(data[0], list)):
        if structure != 'object_with_data':
            if data:
                header = [_flatten_value(v) for v in data[0]]
                rows = [[_flatten_value(v) for v in row] for row in data[1:]]
            else:
                header = []
                rows = []
        else:
            rows = [[_flatten_value(v) for v in row] for row in data]

    else:
        raise ValueError(
            f"Structure JSON non supportée. "
            f"Attendu : array d'objets, array d'arrays, ou objet avec clé 'data'."
        )

    total_lines = len(rows) + 1
    logger.info(f"JSON lu : {total_lines} lignes, {len(header)} colonnes")
    return header, rows, total_lines


def read_json_chunks(
    filepath: Path,
    chunk_size: int = 10_000,
    data_key: Optional[str] = None,
    skip_header: bool = False,
) -> Generator[list[list[str]], None, None]:
    """Lit un JSON par chunks pour uniformité avec le pipeline.

    Note: Le JSON est chargé entièrement en mémoire (limitation du format),
    mais les chunks permettent un traitement progressif.
    """
    header, rows, _ = read_json(filepath, data_key)

    start = 0
    if not skip_header:
        # Inclure le header dans le premier chunk comme première ligne
        pass  # Le header est retourné séparément

    for i in range(0, len(rows), chunk_size):
        yield rows[i:i + chunk_size]
