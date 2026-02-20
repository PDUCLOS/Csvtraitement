"""Comparaison / Diff entre deux fichiers CSV.

Compare deux versions d'un même export pour identifier :
- Lignes ajoutées (présentes dans B, absentes de A)
- Lignes supprimées (présentes dans A, absentes de B)
- Lignes modifiées (même clé, valeurs différentes)
- Diff cellule par cellule avec détail des changements
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult, run_detection
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)


class RowStatus:
    """Statut d'une ligne dans la comparaison."""
    UNCHANGED = 'unchanged'
    ADDED = 'added'
    REMOVED = 'removed'
    MODIFIED = 'modified'


@dataclass
class CellDiff:
    """Différence sur une cellule."""
    column_index: int
    column_name: str
    value_a: str
    value_b: str


@dataclass
class RowDiff:
    """Différence sur une ligne."""
    status: str                           # RowStatus
    key: str
    line_a: Optional[int] = None          # Numéro de ligne dans fichier A
    line_b: Optional[int] = None          # Numéro de ligne dans fichier B
    cells_a: list[str] = field(default_factory=list)
    cells_b: list[str] = field(default_factory=list)
    cell_diffs: list[CellDiff] = field(default_factory=list)

    @property
    def n_changes(self) -> int:
        return len(self.cell_diffs)


@dataclass
class ComparisonResult:
    """Résultat de la comparaison entre deux fichiers."""
    file_a: str
    file_b: str
    total_a: int = 0
    total_b: int = 0
    unchanged: int = 0
    added: int = 0
    removed: int = 0
    modified: int = 0
    total_cell_changes: int = 0
    diffs: list[RowDiff] = field(default_factory=list)
    header_a: list[str] = field(default_factory=list)
    header_b: list[str] = field(default_factory=list)
    header_changes: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def total_differences(self) -> int:
        return self.added + self.removed + self.modified

    @property
    def similarity_percentage(self) -> float:
        total = max(self.total_a, self.total_b)
        if total == 0:
            return 100.0
        return self.unchanged / total * 100


def _build_key(cells: list[str], key_columns: list[int]) -> str:
    """Construit une clé de comparaison."""
    parts = []
    for col in key_columns:
        val = cells[col].strip() if col < len(cells) else ''
        parts.append(val)
    return '|'.join(parts)


def compare_files(
    detection_a: DetectionResult,
    detection_b: DetectionResult,
    key_columns: Optional[list[int]] = None,
    ignore_columns: Optional[list[int]] = None,
    max_diffs: int = 50_000,
    progress_callback=None
) -> ComparisonResult:
    """Compare deux fichiers CSV et retourne les différences.

    Args:
        detection_a: Détection du fichier A (référence).
        detection_b: Détection du fichier B (nouveau).
        key_columns: Colonnes servant de clé primaire (None = toutes).
        ignore_columns: Colonnes à ignorer dans la comparaison.
        max_diffs: Nombre max de différences à collecter.
        progress_callback: Fonction de suivi.

    Returns:
        ComparisonResult avec toutes les différences.
    """
    n_cols = max(detection_a.expected_columns, detection_b.expected_columns)
    if key_columns is None:
        key_columns = list(range(n_cols))

    ignore_set = set(ignore_columns) if ignore_columns else set()
    compare_cols = [i for i in range(n_cols) if i not in ignore_set and i not in key_columns]

    result = ComparisonResult(
        file_a=str(detection_a.filepath.name),
        file_b=str(detection_b.filepath.name),
        header_a=[h.strip() for h in detection_a.header],
        header_b=[h.strip() for h in detection_b.header],
    )

    # Détecter les changements d'en-têtes
    max_h = max(len(result.header_a), len(result.header_b))
    for i in range(max_h):
        ha = result.header_a[i] if i < len(result.header_a) else '(absent)'
        hb = result.header_b[i] if i < len(result.header_b) else '(absent)'
        if ha != hb:
            result.header_changes.append((i, ha, hb))

    # Charger fichier A en index
    index_a: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
    rows_a = 0
    for chunk in read_chunks(
        detection_a.filepath, detection_a.separator,
        detection_a.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            rows_a += 1
            line_num = rows_a + 1
            padded = list(cells[:n_cols])
            while len(padded) < n_cols:
                padded.append('')
            key = _build_key(padded, key_columns)
            index_a[key].append((line_num, padded))

        if progress_callback:
            progress_callback(rows_a, detection_a.total_lines + detection_b.total_lines,
                              f"Comparaison: indexation A ({rows_a} lignes)")

    result.total_a = rows_a

    # Parcourir fichier B et comparer
    matched_keys_a: set[str] = set()
    rows_b = 0

    for chunk in read_chunks(
        detection_b.filepath, detection_b.separator,
        detection_b.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            rows_b += 1
            line_num_b = rows_b + 1
            padded_b = list(cells[:n_cols])
            while len(padded_b) < n_cols:
                padded_b.append('')
            key = _build_key(padded_b, key_columns)

            if key in index_a:
                # Ligne trouvée dans A — comparer les valeurs
                entries_a = index_a[key]
                line_num_a, padded_a = entries_a[0]  # Prendre la première occurrence
                matched_keys_a.add(key)

                # Comparer cellule par cellule
                cell_diffs = []
                for col in compare_cols:
                    val_a = padded_a[col].strip() if col < len(padded_a) else ''
                    val_b = padded_b[col].strip() if col < len(padded_b) else ''
                    if val_a != val_b:
                        col_name = result.header_a[col] if col < len(result.header_a) else f'Col {col}'
                        cell_diffs.append(CellDiff(
                            column_index=col,
                            column_name=col_name,
                            value_a=val_a,
                            value_b=val_b,
                        ))

                if cell_diffs:
                    result.modified += 1
                    result.total_cell_changes += len(cell_diffs)
                    if len(result.diffs) < max_diffs:
                        result.diffs.append(RowDiff(
                            status=RowStatus.MODIFIED,
                            key=key,
                            line_a=line_num_a,
                            line_b=line_num_b,
                            cells_a=padded_a,
                            cells_b=padded_b,
                            cell_diffs=cell_diffs,
                        ))
                else:
                    result.unchanged += 1
            else:
                # Ligne absente de A → ajoutée
                result.added += 1
                if len(result.diffs) < max_diffs:
                    result.diffs.append(RowDiff(
                        status=RowStatus.ADDED,
                        key=key,
                        line_b=line_num_b,
                        cells_b=padded_b,
                    ))

        if progress_callback:
            progress_callback(
                rows_a + rows_b,
                detection_a.total_lines + detection_b.total_lines,
                f"Comparaison: {rows_b} lignes de B traitées"
            )

    result.total_b = rows_b

    # Lignes de A non trouvées dans B → supprimées
    for key, entries in index_a.items():
        if key not in matched_keys_a:
            for line_num_a, padded_a in entries:
                result.removed += 1
                if len(result.diffs) < max_diffs:
                    result.diffs.append(RowDiff(
                        status=RowStatus.REMOVED,
                        key=key,
                        line_a=line_num_a,
                        cells_a=padded_a,
                    ))

    # Trier les diffs par numéro de ligne
    result.diffs.sort(key=lambda d: (d.line_a or 0, d.line_b or 0))

    logger.info(
        f"Comparaison: {result.unchanged} inchangées, {result.added} ajoutées, "
        f"{result.removed} supprimées, {result.modified} modifiées "
        f"({result.total_cell_changes} cellules)"
    )
    return result
