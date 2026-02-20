"""Analyse des décalages de colonnes et scoring de conformité.

Compare chaque ligne au profil de référence pour détecter les décalages,
identifier leur emplacement et proposer des corrections.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult, ColumnProfile
from utils.patterns import detect_cell_type, get_type_category, CellType
from utils.chunked_reader import read_chunks, read_lines_raw

logger = logging.getLogger(__name__)


@dataclass
class ShiftInfo:
    """Information sur un décalage détecté."""
    line_number: int
    raw_line: str
    cells: list[str]
    actual_columns: int
    shift_start_column: int  # Index de la colonne où le décalage commence
    shift_type: str  # 'extra_column', 'missing_column', 'type_mismatch'
    details: str = ""


@dataclass
class AnalysisResult:
    """Résultat complet de l'analyse."""
    total_lines: int
    conforming_lines: int
    non_conforming_lines: int
    shifts: list[ShiftInfo] = field(default_factory=list)
    type_mismatches: list[ShiftInfo] = field(default_factory=list)
    conformity_percentage: float = 0.0
    shift_columns_summary: dict[int, int] = field(default_factory=dict)

    @property
    def lines_to_fix(self) -> int:
        return len(self.shifts) + len(self.type_mismatches)

    @property
    def all_issues(self) -> list[ShiftInfo]:
        return sorted(
            self.shifts + self.type_mismatches,
            key=lambda s: s.line_number
        )


def find_shift_start(
    cells: list[str],
    profiles: list[ColumnProfile],
    expected_columns: int
) -> tuple[int, str]:
    """Trouve la colonne où commence le décalage.

    Compare les types des cellules avec le profil attendu.
    Le décalage commence là où le type ne correspond plus.

    Args:
        cells: Cellules de la ligne.
        profiles: Profils de référence des colonnes.
        expected_columns: Nombre de colonnes attendu.

    Returns:
        Tuple (index de la colonne de décalage, type de décalage).
    """
    n_cells = len(cells)

    if n_cells == expected_columns:
        # Même nombre de colonnes mais types différents → type_mismatch
        for i, (cell, profile) in enumerate(zip(cells, profiles)):
            cell_type = detect_cell_type(cell)
            cell_cat = get_type_category(cell_type)
            expected_cat = profile.dominant_category

            # Ignorer les vides et les colonnes texte (trop flexibles)
            if cell_cat == 'empty' or expected_cat == 'empty':
                continue
            if expected_cat == 'text':
                continue

            if cell_cat != expected_cat:
                return i, 'type_mismatch'
        return -1, 'ok'

    elif n_cells > expected_columns:
        # Plus de colonnes que prévu → colonne en trop quelque part
        # On parcourt en comparant les types
        shift_col = _find_extra_column(cells, profiles, expected_columns)
        return shift_col, 'extra_column'

    else:
        # Moins de colonnes → colonne manquante
        shift_col = _find_missing_column(cells, profiles, expected_columns)
        return shift_col, 'missing_column'


def _find_extra_column(
    cells: list[str],
    profiles: list[ColumnProfile],
    expected_columns: int
) -> int:
    """Trouve où se situe la colonne supplémentaire.

    Stratégie : avancer en parallèle sur cells et profiles.
    Quand le type ne match plus, on a trouvé l'insertion.
    """
    j = 0  # Index dans cells
    for i in range(expected_columns):
        if j >= len(cells):
            return i

        cell_type = detect_cell_type(cells[j])
        cell_cat = get_type_category(cell_type)
        expected_cat = profiles[i].dominant_category

        if expected_cat in ('empty', 'text'):
            j += 1
            continue

        if cell_cat != expected_cat:
            # Vérifier si la cellule suivante match mieux
            if j + 1 < len(cells):
                next_type = detect_cell_type(cells[j + 1])
                next_cat = get_type_category(next_type)
                if next_cat == expected_cat:
                    return j  # La colonne j est en trop
            return j

        j += 1

    return expected_columns  # Extra colonne à la fin


def _find_missing_column(
    cells: list[str],
    profiles: list[ColumnProfile],
    expected_columns: int
) -> int:
    """Trouve où se situe la colonne manquante.

    Stratégie : quand le type d'une cellule correspond au profil
    de la colonne suivante plutôt que la colonne courante.
    """
    j = 0  # Index dans cells
    for i in range(expected_columns):
        if j >= len(cells):
            return i  # Colonnes manquantes à partir d'ici

        cell_type = detect_cell_type(cells[j])
        cell_cat = get_type_category(cell_type)
        expected_cat = profiles[i].dominant_category

        if expected_cat in ('empty', 'text'):
            j += 1
            continue

        if cell_cat != expected_cat:
            # Vérifier si cette cellule correspond à la colonne SUIVANTE
            if i + 1 < expected_columns:
                next_expected = profiles[i + 1].dominant_category
                if cell_cat == next_expected:
                    return i  # Colonne i est manquante
            return i

        j += 1

    return len(cells)


def analyze_file(
    detection: DetectionResult,
    max_issues: int = 50_000,
    progress_callback=None
) -> AnalysisResult:
    """Analyse complète du fichier pour détecter les décalages.

    Args:
        detection: Résultat de la détection préalable.
        max_issues: Nombre maximum d'anomalies à collecter en détail.
        progress_callback: Fonction(current, total, message) pour le suivi.

    Returns:
        AnalysisResult avec toutes les anomalies détectées.
    """
    fp = detection.filepath
    sep = detection.separator
    enc = detection.encoding
    expected = detection.expected_columns
    profiles = detection.column_profiles

    conforming = 0
    non_conforming = 0
    shifts: list[ShiftInfo] = []
    type_mismatches: list[ShiftInfo] = []
    shift_col_summary: dict[int, int] = {}

    total = detection.total_lines
    chunk_num = 0

    for chunk in read_chunks(fp, sep, enc, chunk_size=10_000, skip_header=True):
        chunk_num += 1
        base_line = (chunk_num - 1) * 10_000 + 2  # +2 car header + 1-indexed

        for i, cells in enumerate(chunk):
            line_num = base_line + i
            n_cells = len(cells)

            if n_cells == expected:
                # Vérifier aussi les types
                shift_col, shift_type = find_shift_start(cells, profiles, expected)
                if shift_type == 'ok':
                    conforming += 1
                else:
                    non_conforming += 1
                    if len(type_mismatches) < max_issues:
                        type_mismatches.append(ShiftInfo(
                            line_number=line_num,
                            raw_line=sep.join(cells),
                            cells=cells,
                            actual_columns=n_cells,
                            shift_start_column=shift_col,
                            shift_type=shift_type,
                            details=f"Type attendu col {shift_col}: {profiles[shift_col].dominant_category}"
                        ))
                    shift_col_summary[shift_col] = shift_col_summary.get(shift_col, 0) + 1
            else:
                non_conforming += 1
                shift_col, shift_type = find_shift_start(cells, profiles, expected)

                if len(shifts) < max_issues:
                    shifts.append(ShiftInfo(
                        line_number=line_num,
                        raw_line=sep.join(cells),
                        cells=cells,
                        actual_columns=n_cells,
                        shift_start_column=shift_col,
                        shift_type=shift_type,
                        details=(
                            f"Attendu {expected} colonnes, trouvé {n_cells}. "
                            f"Décalage à partir de la colonne {shift_col}"
                        )
                    ))
                shift_col_summary[shift_col] = shift_col_summary.get(shift_col, 0) + 1

        # Progress
        processed = min(chunk_num * 10_000, total)
        if progress_callback:
            progress_callback(processed, total, f"Analyse: {processed}/{total} lignes")

    total_analyzed = conforming + non_conforming
    pct = (conforming / total_analyzed * 100) if total_analyzed > 0 else 0.0

    result = AnalysisResult(
        total_lines=total_analyzed,
        conforming_lines=conforming,
        non_conforming_lines=non_conforming,
        shifts=shifts,
        type_mismatches=type_mismatches,
        conformity_percentage=pct,
        shift_columns_summary=shift_col_summary,
    )

    logger.info(
        f"Analyse terminée: {conforming}/{total_analyzed} conformes "
        f"({pct:.1f}%), {len(shifts)} décalages, "
        f"{len(type_mismatches)} erreurs de type"
    )
    return result
