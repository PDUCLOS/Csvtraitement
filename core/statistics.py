"""Statistiques détaillées par colonne.

Calcule min, max, moyenne, médiane, valeurs uniques, outliers,
distribution de longueurs, etc.
"""

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult, ColumnProfile
from utils.patterns import (
    CellType, detect_cell_type, normalize_decimal,
    normalize_integer, get_type_category
)
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)


@dataclass
class ColumnStats:
    """Statistiques d'une colonne."""
    index: int
    header: str
    cell_type: CellType
    total_rows: int = 0
    non_empty: int = 0
    empty_count: int = 0
    masked_empty_count: int = 0
    unique_count: int = 0

    # Numériques
    numeric_min: Optional[float] = None
    numeric_max: Optional[float] = None
    numeric_mean: Optional[float] = None
    numeric_median: Optional[float] = None
    numeric_std: Optional[float] = None
    numeric_sum: Optional[float] = None
    outliers_count: int = 0
    outliers_iqr: list[tuple[int, float]] = field(default_factory=list)  # (line, value)

    # Texte
    text_min_length: Optional[int] = None
    text_max_length: Optional[int] = None
    text_avg_length: Optional[float] = None
    top_values: list[tuple[str, int]] = field(default_factory=list)  # (value, count)

    # Dates
    date_min: Optional[str] = None
    date_max: Optional[str] = None
    date_invalid_count: int = 0

    # Qualité
    fill_rate: float = 0.0  # % non vide

    @property
    def completeness(self) -> float:
        """Taux de complétude (0-100%)."""
        if self.total_rows == 0:
            return 0.0
        return (self.non_empty / self.total_rows) * 100


@dataclass
class FileStats:
    """Statistiques globales du fichier."""
    total_rows: int
    total_columns: int
    column_stats: list[ColumnStats]
    duplicate_rows: int = 0
    near_duplicate_rows: int = 0
    empty_rows: int = 0
    overall_fill_rate: float = 0.0


def compute_statistics(
    detection: DetectionResult,
    max_unique_values: int = 100,
    max_outliers: int = 20,
    progress_callback=None
) -> FileStats:
    """Calcule les statistiques complètes pour toutes les colonnes.

    Args:
        detection: Résultat de détection.
        max_unique_values: Nombre max de valeurs uniques à tracker.
        max_outliers: Nombre max d'outliers à conserver.
        progress_callback: Fonction de suivi.

    Returns:
        FileStats avec toutes les statistiques.
    """
    n_cols = detection.expected_columns
    profiles = detection.column_profiles

    # Initialiser les collecteurs par colonne
    col_values: list[list[float]] = [[] for _ in range(n_cols)]  # Pour numériques
    col_lengths: list[list[int]] = [[] for _ in range(n_cols)]   # Pour texte
    col_dates: list[list[str]] = [[] for _ in range(n_cols)]     # Pour dates
    col_counters: list[Counter] = [Counter() for _ in range(n_cols)]  # Valeurs fréquentes
    col_empty: list[int] = [0] * n_cols
    col_masked: list[int] = [0] * n_cols
    col_date_invalid: list[int] = [0] * n_cols

    total_rows = 0
    empty_rows = 0
    row_hashes: Counter = Counter()  # Pour détecter les doublons

    for chunk in read_chunks(
        detection.filepath, detection.separator,
        detection.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            total_rows += 1

            # Dupliquer détection : hash de la ligne
            row_key = tuple(c.strip() for c in cells[:n_cols])
            row_hashes[row_key] += 1

            # Ligne vide ?
            if all(not c.strip() for c in cells[:n_cols]):
                empty_rows += 1
                for i in range(min(len(cells), n_cols)):
                    col_empty[i] += 1
                continue

            for i in range(min(len(cells), n_cols)):
                value = cells[i].strip() if i < len(cells) else ''
                cell_type = detect_cell_type(value)

                if cell_type == CellType.EMPTY:
                    col_empty[i] += 1
                    continue

                if cell_type == CellType.MASKED_EMPTY:
                    col_masked[i] += 1
                    continue

                if cell_type == CellType.DATE_INVALID:
                    col_date_invalid[i] += 1
                    continue

                # Tracker les valeurs fréquentes (limité)
                if len(col_counters[i]) < max_unique_values * 10:
                    col_counters[i][value] += 1

                # Numériques
                if cell_type in (CellType.INTEGER, CellType.DECIMAL):
                    num = normalize_decimal(value)
                    if num is None:
                        num_int = normalize_integer(value)
                        if num_int is not None:
                            num = float(num_int)
                    if num is not None:
                        col_values[i].append(num)

                # Dates
                elif cell_type == CellType.DATE:
                    from utils.patterns import parse_date
                    parsed = parse_date(value)
                    if parsed:
                        col_dates[i].append(parsed)

                # Texte
                elif cell_type in (CellType.TEXT_SHORT, CellType.TEXT_LONG):
                    col_lengths[i].append(len(value))

        if progress_callback:
            progress_callback(total_rows, detection.total_lines, f"Statistiques: {total_rows} lignes")

    # Construire les stats par colonne
    column_stats = []
    total_fill = 0

    for i in range(n_cols):
        header = detection.header[i].strip() if i < len(detection.header) else f'Col {i}'
        profile = profiles[i] if i < len(profiles) else ColumnProfile(index=i)

        stats = ColumnStats(
            index=i,
            header=header,
            cell_type=profile.dominant_type,
            total_rows=total_rows,
            non_empty=total_rows - col_empty[i] - col_masked[i],
            empty_count=col_empty[i],
            masked_empty_count=col_masked[i],
            unique_count=len(col_counters[i]),
            date_invalid_count=col_date_invalid[i],
        )

        stats.fill_rate = stats.non_empty / total_rows * 100 if total_rows > 0 else 0
        total_fill += stats.fill_rate

        # Stats numériques
        values = col_values[i]
        if values:
            values_sorted = sorted(values)
            stats.numeric_min = values_sorted[0]
            stats.numeric_max = values_sorted[-1]
            stats.numeric_sum = sum(values)
            stats.numeric_mean = stats.numeric_sum / len(values)
            stats.numeric_median = _median(values_sorted)
            stats.numeric_std = _std(values, stats.numeric_mean)

            # Outliers par IQR
            q1 = _percentile(values_sorted, 25)
            q3 = _percentile(values_sorted, 75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            outlier_count = 0
            for v in values:
                if v < lower or v > upper:
                    outlier_count += 1
            stats.outliers_count = outlier_count

        # Stats texte
        lengths = col_lengths[i]
        if lengths:
            stats.text_min_length = min(lengths)
            stats.text_max_length = max(lengths)
            stats.text_avg_length = sum(lengths) / len(lengths)

        # Top valeurs
        stats.top_values = col_counters[i].most_common(10)

        # Stats dates
        dates = col_dates[i]
        if dates:
            dates_sorted = sorted(dates)
            stats.date_min = dates_sorted[0]
            stats.date_max = dates_sorted[-1]

        column_stats.append(stats)

    # Doublons
    duplicate_rows = sum(count - 1 for count in row_hashes.values() if count > 1)

    overall_fill = total_fill / n_cols if n_cols > 0 else 0

    return FileStats(
        total_rows=total_rows,
        total_columns=n_cols,
        column_stats=column_stats,
        duplicate_rows=duplicate_rows,
        empty_rows=empty_rows,
        overall_fill_rate=overall_fill,
    )


def _median(sorted_values: list[float]) -> float:
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return sorted_values[n // 2]
    return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2


def _percentile(sorted_values: list[float], pct: float) -> float:
    n = len(sorted_values)
    if n == 0:
        return 0.0
    k = (n - 1) * pct / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def _std(values: list[float], mean: float) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)
