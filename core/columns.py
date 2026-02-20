"""Opérations sur les colonnes : réordonnancement, suppression, renommage.

Fonctionnalités :
- Réordonner les colonnes (drag & drop ou indices)
- Supprimer des colonnes
- Renommer des colonnes
- Supprimer des lignes (vides, en erreur, par sélection)
- Filtrer les lignes par condition
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.detector import DetectionResult
from utils.patterns import detect_cell_type, CellType

logger = logging.getLogger(__name__)


@dataclass
class ColumnOperation:
    """Opération sur les colonnes."""
    operation: str       # 'reorder', 'delete', 'rename', 'duplicate'
    column_index: int
    new_index: Optional[int] = None     # Pour reorder
    new_name: Optional[str] = None      # Pour rename


@dataclass
class RowFilter:
    """Filtre pour la suppression de lignes."""
    filter_type: str     # 'empty', 'error', 'duplicate', 'condition', 'selection'
    column_index: Optional[int] = None
    condition: Optional[str] = None     # 'empty', 'non_empty', 'equals', 'contains', 'regex'
    value: str = ""
    selected_lines: set[int] = field(default_factory=set)


@dataclass
class ColumnOpResult:
    """Résultat des opérations sur les colonnes."""
    new_headers: list[str] = field(default_factory=list)
    new_rows: list[list[str]] = field(default_factory=list)
    columns_deleted: int = 0
    columns_renamed: int = 0
    columns_reordered: bool = False
    rows_deleted: int = 0
    original_row_count: int = 0


def reorder_columns(
    headers: list[str],
    rows: list[list[str]],
    new_order: list[int]
) -> tuple[list[str], list[list[str]]]:
    """Réordonne les colonnes selon un nouvel ordre.

    Args:
        headers: En-têtes actuels.
        rows: Lignes de données.
        new_order: Liste d'indices dans le nouvel ordre.

    Returns:
        Tuple (headers réordonnés, lignes réordonnées).
    """
    new_headers = [headers[i] for i in new_order if i < len(headers)]
    new_rows = []
    for row in rows:
        new_row = [row[i] if i < len(row) else '' for i in new_order]
        new_rows.append(new_row)

    logger.info(f"Colonnes réordonnées: {new_order}")
    return new_headers, new_rows


def delete_columns(
    headers: list[str],
    rows: list[list[str]],
    columns_to_delete: list[int]
) -> tuple[list[str], list[list[str]]]:
    """Supprime des colonnes.

    Args:
        headers: En-têtes actuels.
        rows: Lignes de données.
        columns_to_delete: Indices des colonnes à supprimer.

    Returns:
        Tuple (headers réduits, lignes réduites).
    """
    delete_set = set(columns_to_delete)
    keep_indices = [i for i in range(len(headers)) if i not in delete_set]

    new_headers = [headers[i] for i in keep_indices]
    new_rows = []
    for row in rows:
        new_row = [row[i] if i < len(row) else '' for i in keep_indices]
        new_rows.append(new_row)

    logger.info(f"Colonnes supprimées: {columns_to_delete}")
    return new_headers, new_rows


def rename_columns(
    headers: list[str],
    renames: dict[int, str]
) -> list[str]:
    """Renomme des colonnes.

    Args:
        headers: En-têtes actuels.
        renames: Dict {index: nouveau_nom}.

    Returns:
        Headers renommés.
    """
    new_headers = list(headers)
    for idx, new_name in renames.items():
        if 0 <= idx < len(new_headers):
            old_name = new_headers[idx]
            new_headers[idx] = new_name
            logger.info(f"Colonne {idx} renommée: '{old_name}' → '{new_name}'")
    return new_headers


def delete_rows(
    rows: list[list[str]],
    row_filter: RowFilter,
    expected_columns: int = 0
) -> tuple[list[list[str]], int]:
    """Supprime des lignes selon un filtre.

    Args:
        rows: Lignes de données.
        row_filter: Filtre de suppression.
        expected_columns: Nombre de colonnes attendu (pour le filtre 'error').

    Returns:
        Tuple (lignes filtrées, nombre de lignes supprimées).
    """
    kept = []
    deleted = 0

    for i, row in enumerate(rows):
        should_delete = False
        line_num = i + 2  # +2 car header + 0-indexé

        if row_filter.filter_type == 'empty':
            if all(not c.strip() for c in row):
                should_delete = True

        elif row_filter.filter_type == 'error':
            if expected_columns > 0 and len(row) != expected_columns:
                should_delete = True

        elif row_filter.filter_type == 'selection':
            if line_num in row_filter.selected_lines:
                should_delete = True

        elif row_filter.filter_type == 'condition':
            if row_filter.column_index is not None and row_filter.column_index < len(row):
                val = row[row_filter.column_index].strip()
                if row_filter.condition == 'empty' and not val:
                    should_delete = True
                elif row_filter.condition == 'non_empty' and val:
                    should_delete = True
                elif row_filter.condition == 'equals' and val == row_filter.value:
                    should_delete = True
                elif row_filter.condition == 'contains' and row_filter.value in val:
                    should_delete = True
                elif row_filter.condition == 'masked':
                    if detect_cell_type(val) == CellType.MASKED_EMPTY:
                        should_delete = True

        if should_delete:
            deleted += 1
        else:
            kept.append(row)

    logger.info(f"Lignes supprimées: {deleted}/{len(rows)} ({row_filter.filter_type})")
    return kept, deleted


def apply_column_operations(
    headers: list[str],
    rows: list[list[str]],
    operations: list[ColumnOperation],
    row_filters: Optional[list[RowFilter]] = None,
    expected_columns: int = 0
) -> ColumnOpResult:
    """Applique une séquence d'opérations sur les colonnes et lignes.

    Args:
        headers: En-têtes actuels.
        rows: Lignes de données.
        operations: Opérations à appliquer dans l'ordre.
        row_filters: Filtres de suppression de lignes.
        expected_columns: Nombre de colonnes attendu.

    Returns:
        ColumnOpResult.
    """
    result = ColumnOpResult(original_row_count=len(rows))
    current_headers = list(headers)
    current_rows = [list(r) for r in rows]

    for op in operations:
        if op.operation == 'reorder' and op.new_index is not None:
            # Déplacer une colonne de sa position vers la nouvelle
            order = list(range(len(current_headers)))
            old_idx = op.column_index
            new_idx = op.new_index
            if 0 <= old_idx < len(order) and 0 <= new_idx < len(order):
                order.pop(old_idx)
                order.insert(new_idx, old_idx)
                current_headers, current_rows = reorder_columns(
                    current_headers, current_rows, order
                )
                result.columns_reordered = True

        elif op.operation == 'delete':
            current_headers, current_rows = delete_columns(
                current_headers, current_rows, [op.column_index]
            )
            result.columns_deleted += 1

        elif op.operation == 'rename' and op.new_name:
            current_headers = rename_columns(
                current_headers, {op.column_index: op.new_name}
            )
            result.columns_renamed += 1

    # Appliquer les filtres de lignes
    if row_filters:
        for rf in row_filters:
            current_rows, deleted = delete_rows(
                current_rows, rf, expected_columns
            )
            result.rows_deleted += deleted

    result.new_headers = current_headers
    result.new_rows = current_rows
    return result
