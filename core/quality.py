"""Score de qualité des données (Data Quality Index).

Calcule un score 0-100 par colonne et global basé sur :
- Taux de remplissage (vides + masqués)
- Cohérence de type (% de cellules conformes au type dominant)
- Outliers statistiques (IQR)
- Doublons
- Homogénéité des formats (dates, décimales FR/US mélangées)
"""

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from core.detector import DetectionResult, ColumnProfile
from core.statistics import FileStats, ColumnStats
from utils.patterns import (
    CellType, detect_cell_type, detect_decimal_format,
    get_type_category, DATE_DMY, DATE_YMD, DATETIME_DMY, DATETIME_YMD
)
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)


@dataclass
class ColumnQuality:
    """Score de qualité pour une colonne."""
    index: int
    header: str
    overall_score: float = 0.0          # 0-100

    # Composantes du score (chacune 0-100)
    fill_score: float = 0.0             # Taux de remplissage
    type_consistency_score: float = 0.0  # Cohérence de type
    outlier_score: float = 0.0          # Absence d'outliers
    format_homogeneity_score: float = 0.0  # Homogénéité des formats
    uniqueness_score: float = 0.0       # Diversité des valeurs (pas trop de doublons)

    # Détails
    fill_rate: float = 0.0
    type_consistency: float = 0.0       # % cellules conformes au type dominant
    outlier_rate: float = 0.0
    format_mix: dict[str, int] = field(default_factory=dict)  # ex: {'DD.MM.YYYY': 50, 'YYYY-MM-DD': 10}
    issues: list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        """Note lettre (A-F)."""
        if self.overall_score >= 90:
            return 'A'
        elif self.overall_score >= 75:
            return 'B'
        elif self.overall_score >= 60:
            return 'C'
        elif self.overall_score >= 40:
            return 'D'
        else:
            return 'F'


@dataclass
class QualityReport:
    """Rapport de qualité global."""
    overall_score: float = 0.0
    overall_grade: str = 'F'
    column_scores: list[ColumnQuality] = field(default_factory=list)
    duplicate_rate: float = 0.0
    empty_row_rate: float = 0.0
    total_issues: int = 0
    summary: list[str] = field(default_factory=list)


# ── Poids des composantes dans le score final ──
WEIGHTS = {
    'fill': 0.30,
    'type_consistency': 0.25,
    'outlier': 0.15,
    'format_homogeneity': 0.20,
    'uniqueness': 0.10,
}


def _detect_date_format(value: str) -> Optional[str]:
    """Identifie le format de date exact d'une valeur."""
    stripped = value.strip()
    if DATETIME_DMY.match(stripped):
        sep = '.' if '.' in stripped[:6] else ('/' if '/' in stripped[:6] else '-')
        return f'DD{sep}MM{sep}YYYY HH:MM'
    if DATETIME_YMD.match(stripped):
        return 'YYYY-MM-DD HH:MM'
    if DATE_DMY.match(stripped):
        sep = '.' if '.' in stripped else ('/' if '/' in stripped else '-')
        return f'DD{sep}MM{sep}YYYY'
    if DATE_YMD.match(stripped):
        return 'YYYY-MM-DD'
    return None


def compute_quality(
    detection: DetectionResult,
    file_stats: Optional[FileStats] = None,
    progress_callback=None
) -> QualityReport:
    """Calcule le score de qualité des données.

    Args:
        detection: Résultat de détection.
        file_stats: Statistiques pré-calculées (optionnel, recalculé si absent).
        progress_callback: Fonction de suivi.

    Returns:
        QualityReport avec scores par colonne et global.
    """
    n_cols = detection.expected_columns
    profiles = detection.column_profiles

    # Collecteurs par colonne
    col_type_counts: list[Counter] = [Counter() for _ in range(n_cols)]
    col_date_formats: list[Counter] = [Counter() for _ in range(n_cols)]
    col_decimal_formats: list[Counter] = [Counter() for _ in range(n_cols)]
    col_values: list[list[float]] = [[] for _ in range(n_cols)]
    col_empty: list[int] = [0] * n_cols
    col_total: list[int] = [0] * n_cols
    col_value_counts: list[Counter] = [Counter() for _ in range(n_cols)]

    total_rows = 0
    empty_rows = 0
    row_hashes: Counter = Counter()

    for chunk in read_chunks(
        detection.filepath, detection.separator,
        detection.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            total_rows += 1
            row_key = tuple(c.strip() for c in cells[:n_cols])
            row_hashes[row_key] += 1

            if all(not c.strip() for c in cells[:n_cols]):
                empty_rows += 1

            for i in range(min(len(cells), n_cols)):
                col_total[i] += 1
                value = cells[i].strip() if i < len(cells) else ''
                cell_type = detect_cell_type(value)

                # Type distribution
                col_type_counts[i][cell_type] += 1

                if cell_type in (CellType.EMPTY, CellType.MASKED_EMPTY):
                    col_empty[i] += 1
                    continue

                # Format de date
                if cell_type == CellType.DATE:
                    fmt = _detect_date_format(value)
                    if fmt:
                        col_date_formats[i][fmt] += 1

                # Format décimal
                if cell_type in (CellType.DECIMAL,):
                    dec_fmt = detect_decimal_format(value)
                    if dec_fmt:
                        col_decimal_formats[i][dec_fmt] += 1

                # Valeurs numériques pour outliers
                if cell_type in (CellType.INTEGER, CellType.DECIMAL):
                    from utils.patterns import normalize_decimal, normalize_integer
                    num = normalize_decimal(value)
                    if num is None:
                        num_int = normalize_integer(value)
                        if num_int is not None:
                            num = float(num_int)
                    if num is not None:
                        col_values[i].append(num)

                # Compteur de valeurs pour diversité
                if len(col_value_counts[i]) < 10_000:
                    col_value_counts[i][value] += 1

        if progress_callback:
            progress_callback(total_rows, detection.total_lines, f"Qualité: {total_rows} lignes")

    # ── Calculer les scores par colonne ──
    column_scores = []

    for i in range(n_cols):
        header = detection.header[i].strip() if i < len(detection.header) else f'Col {i}'
        profile = profiles[i] if i < len(profiles) else ColumnProfile(index=i)
        cq = ColumnQuality(index=i, header=header)

        total = col_total[i]
        if total == 0:
            column_scores.append(cq)
            continue

        # 1. Fill score
        non_empty = total - col_empty[i]
        cq.fill_rate = non_empty / total * 100
        cq.fill_score = min(cq.fill_rate, 100.0)

        # 2. Type consistency
        type_dist = col_type_counts[i]
        non_empty_types = {k: v for k, v in type_dist.items()
                          if k not in (CellType.EMPTY, CellType.MASKED_EMPTY)}
        if non_empty_types:
            dominant_count = max(non_empty_types.values())
            total_non_empty = sum(non_empty_types.values())
            cq.type_consistency = dominant_count / total_non_empty * 100
            cq.type_consistency_score = cq.type_consistency
            if cq.type_consistency < 80:
                cq.issues.append(f"Types mixtes : {dict(non_empty_types)}")
        else:
            cq.type_consistency_score = 100.0

        # 3. Outlier score
        values = col_values[i]
        if len(values) >= 10:
            sorted_vals = sorted(values)
            q1 = _percentile(sorted_vals, 25)
            q3 = _percentile(sorted_vals, 75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            outliers = sum(1 for v in values if v < lower or v > upper)
            cq.outlier_rate = outliers / len(values) * 100
            # Score inversé : 0% outliers = 100, 10%+ outliers = 0
            cq.outlier_score = max(0, 100 - cq.outlier_rate * 10)
            if outliers > 0:
                cq.issues.append(f"{outliers} outliers ({cq.outlier_rate:.1f}%)")
        else:
            cq.outlier_score = 100.0

        # 4. Format homogeneity
        date_fmts = col_date_formats[i]
        dec_fmts = col_decimal_formats[i]

        if date_fmts:
            total_dated = sum(date_fmts.values())
            dominant_fmt_count = date_fmts.most_common(1)[0][1]
            cq.format_homogeneity_score = dominant_fmt_count / total_dated * 100
            cq.format_mix = dict(date_fmts.most_common(5))
            if len(date_fmts) > 1:
                cq.issues.append(f"Formats de date mixtes : {dict(date_fmts.most_common(3))}")
        elif dec_fmts:
            total_dec = sum(dec_fmts.values())
            dominant_fmt_count = dec_fmts.most_common(1)[0][1]
            cq.format_homogeneity_score = dominant_fmt_count / total_dec * 100
            cq.format_mix = dict(dec_fmts.most_common(5))
            if len(dec_fmts) > 1:
                cq.issues.append(f"Formats décimaux mixtes : FR={dec_fmts.get('FR',0)}, US={dec_fmts.get('US',0)}")
        else:
            cq.format_homogeneity_score = 100.0

        # 5. Uniqueness score (pénalise si trop de valeurs identiques, sauf pour booléens)
        if profile.dominant_type != CellType.BOOLEAN and col_value_counts[i]:
            unique = len(col_value_counts[i])
            total_vals = sum(col_value_counts[i].values())
            if total_vals > 0:
                ratio = unique / total_vals
                # Ajuster : <1% unique = mauvais, >50% = bon
                cq.uniqueness_score = min(ratio * 200, 100.0)
            else:
                cq.uniqueness_score = 100.0
        else:
            cq.uniqueness_score = 100.0

        # Score global pondéré
        cq.overall_score = (
            cq.fill_score * WEIGHTS['fill']
            + cq.type_consistency_score * WEIGHTS['type_consistency']
            + cq.outlier_score * WEIGHTS['outlier']
            + cq.format_homogeneity_score * WEIGHTS['format_homogeneity']
            + cq.uniqueness_score * WEIGHTS['uniqueness']
        )

        column_scores.append(cq)

    # ── Score global ──
    duplicate_rows = sum(c - 1 for c in row_hashes.values() if c > 1)
    duplicate_rate = duplicate_rows / total_rows * 100 if total_rows > 0 else 0
    empty_row_rate = empty_rows / total_rows * 100 if total_rows > 0 else 0

    if column_scores:
        avg_score = sum(cq.overall_score for cq in column_scores) / len(column_scores)
    else:
        avg_score = 0.0

    # Pénalités globales
    dup_penalty = min(duplicate_rate * 2, 15)  # Max -15 pour doublons
    empty_penalty = min(empty_row_rate, 10)     # Max -10 pour lignes vides
    overall = max(0, avg_score - dup_penalty - empty_penalty)

    total_issues = sum(len(cq.issues) for cq in column_scores)
    summary = []
    if duplicate_rate > 1:
        summary.append(f"{duplicate_rows:,} lignes dupliquées ({duplicate_rate:.1f}%)")
    if empty_row_rate > 1:
        summary.append(f"{empty_rows:,} lignes vides ({empty_row_rate:.1f}%)")

    low_quality_cols = [cq for cq in column_scores if cq.overall_score < 60]
    if low_quality_cols:
        names = ', '.join(cq.header for cq in low_quality_cols[:5])
        summary.append(f"{len(low_quality_cols)} colonne(s) sous 60% : {names}")

    report = QualityReport(
        overall_score=overall,
        overall_grade=_score_to_grade(overall),
        column_scores=column_scores,
        duplicate_rate=duplicate_rate,
        empty_row_rate=empty_row_rate,
        total_issues=total_issues,
        summary=summary,
    )

    logger.info(f"Qualité globale : {overall:.1f}/100 ({report.overall_grade})")
    return report


def _score_to_grade(score: float) -> str:
    if score >= 90:
        return 'A'
    elif score >= 75:
        return 'B'
    elif score >= 60:
        return 'C'
    elif score >= 40:
        return 'D'
    return 'F'


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
