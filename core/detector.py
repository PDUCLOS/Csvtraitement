"""Détection automatique du séparateur CSV et profiling des colonnes.

Analyse un échantillon du fichier pour déterminer :
- Le séparateur utilisé (;  ,  \\t  |)
- Le nombre de colonnes attendu
- Le profil type de chaque colonne (date, nombre, texte, etc.)
- Le format décimal dominant (FR ou US)
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import stdev, mean
from typing import Optional

from utils.patterns import CellType, detect_cell_type, detect_decimal_format, get_type_category
from utils.chunked_reader import detect_encoding, read_lines_raw, sample_lines, count_lines

logger = logging.getLogger(__name__)

CANDIDATE_SEPARATORS = [';', ',', '\t', '|']
SEPARATOR_SAMPLE_SIZE = 200  # Lignes pour détecter le séparateur


@dataclass
class ColumnProfile:
    """Profil d'une colonne basé sur l'analyse statistique."""
    index: int
    dominant_type: CellType = CellType.EMPTY
    type_distribution: dict[CellType, int] = field(default_factory=dict)
    dominant_category: str = 'empty'
    empty_ratio: float = 0.0
    decimal_format: Optional[str] = None  # 'FR' ou 'US'
    sample_values: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Confiance dans le type dominant (0-1)."""
        total = sum(self.type_distribution.values())
        if total == 0:
            return 0.0
        dominant_count = self.type_distribution.get(self.dominant_type, 0)
        return dominant_count / total


@dataclass
class DetectionResult:
    """Résultat complet de la détection."""
    filepath: Path
    encoding: str
    separator: str
    separator_confidence: float
    total_lines: int
    header: list[str]
    expected_columns: int
    column_profiles: list[ColumnProfile]
    column_count_distribution: dict[int, int] = field(default_factory=dict)
    decimal_format: Optional[str] = None  # Format dominant global

    @property
    def regular_column_count(self) -> bool:
        """True si le nombre de colonnes est constant."""
        return len(self.column_count_distribution) == 1

    @property
    def conformity_ratio(self) -> float:
        """Ratio de lignes ayant le bon nombre de colonnes."""
        total = sum(self.column_count_distribution.values())
        if total == 0:
            return 0.0
        expected_count = self.column_count_distribution.get(self.expected_columns, 0)
        return expected_count / total


def detect_separator(filepath: Path, encoding: str = 'utf-8') -> tuple[str, float]:
    """Détecte le séparateur CSV par analyse de régularité.

    Pour chaque candidat, on compte les occurrences par ligne et on mesure
    l'écart-type. Le séparateur avec le meilleur score (count élevé, écart-type faible) gagne.

    Args:
        filepath: Chemin vers le fichier CSV.
        encoding: Encodage du fichier.

    Returns:
        Tuple (séparateur, score de confiance 0-1).
    """
    lines = list(read_lines_raw(filepath, encoding, max_lines=SEPARATOR_SAMPLE_SIZE))

    if not lines:
        logger.warning("Fichier vide, séparateur par défaut: ;")
        return ';', 0.0

    best_sep = ';'
    best_score = -1.0

    for sep in CANDIDATE_SEPARATORS:
        counts = [line.count(sep) for line in lines]

        # Ignorer si jamais présent
        if max(counts) == 0:
            continue

        avg_count = mean(counts)
        std_count = stdev(counts) if len(counts) > 1 else 0.0

        # Score = moyenne des occurrences / (1 + écart-type)
        # Favorise les séparateurs fréquents ET réguliers
        if avg_count > 0:
            score = avg_count / (1.0 + std_count)
        else:
            score = 0.0

        logger.debug(
            f"Séparateur '{repr(sep)}': avg={avg_count:.1f}, "
            f"std={std_count:.2f}, score={score:.2f}"
        )

        if score > best_score:
            best_score = score
            best_sep = sep

    # Normaliser la confiance
    confidence = min(best_score / 10.0, 1.0)  # Score de 10+ = confiance max
    logger.info(f"Séparateur détecté: '{repr(best_sep)}' (confiance: {confidence:.0%})")
    return best_sep, confidence


def detect_column_count(
    filepath: Path,
    separator: str,
    encoding: str = 'utf-8',
    sample_size: int = 5000
) -> tuple[int, dict[int, int]]:
    """Détecte le nombre de colonnes attendu et la distribution.

    Args:
        filepath: Chemin vers le fichier.
        separator: Séparateur détecté.
        encoding: Encodage du fichier.
        sample_size: Nombre de lignes à analyser.

    Returns:
        Tuple (nombre attendu, distribution {nb_colonnes: count}).
    """
    distribution: Counter[int] = Counter()

    for i, line in enumerate(read_lines_raw(filepath, encoding)):
        if i >= sample_size:
            break
        n_cols = len(line.split(separator))
        distribution[n_cols] += 1

    if not distribution:
        return 0, {}

    # Le nombre de colonnes le plus fréquent est l'attendu
    expected = distribution.most_common(1)[0][0]
    logger.info(
        f"Colonnes attendues: {expected} "
        f"(distribution: {dict(distribution.most_common(5))})"
    )
    return expected, dict(distribution)


def profile_columns(
    lines: list[str],
    separator: str,
    expected_columns: int,
    max_samples_per_col: int = 10
) -> list[ColumnProfile]:
    """Construit le profil type de chaque colonne.

    Analyse uniquement les lignes conformes (bon nombre de colonnes)
    pour établir le profil de référence.

    Args:
        lines: Lignes échantillonnées.
        separator: Séparateur CSV.
        expected_columns: Nombre de colonnes attendu.
        max_samples_per_col: Nombre d'exemples à conserver par colonne.

    Returns:
        Liste de ColumnProfile, un par colonne.
    """
    profiles = [ColumnProfile(index=i) for i in range(expected_columns)]
    decimal_formats: Counter[str] = Counter()

    for line in lines:
        cells = line.split(separator)
        if len(cells) != expected_columns:
            continue  # Ignorer lignes non conformes pour le profiling

        for i, cell in enumerate(cells):
            cell_type = detect_cell_type(cell)
            profiles[i].type_distribution[cell_type] = (
                profiles[i].type_distribution.get(cell_type, 0) + 1
            )

            # Collecter les exemples
            if len(profiles[i].sample_values) < max_samples_per_col and cell.strip():
                profiles[i].sample_values.append(cell.strip())

            # Détecter format décimal
            dec_fmt = detect_decimal_format(cell)
            if dec_fmt:
                decimal_formats[dec_fmt] += 1

    # Déterminer le type dominant de chaque colonne
    for profile in profiles:
        if profile.type_distribution:
            # Exclure EMPTY pour trouver le type dominant
            non_empty = {
                k: v for k, v in profile.type_distribution.items()
                if k != CellType.EMPTY
            }
            if non_empty:
                profile.dominant_type = max(non_empty, key=non_empty.get)
            else:
                profile.dominant_type = CellType.EMPTY

            profile.dominant_category = get_type_category(profile.dominant_type)

            # Ratio de vides
            total = sum(profile.type_distribution.values())
            empty_count = profile.type_distribution.get(CellType.EMPTY, 0)
            profile.empty_ratio = empty_count / total if total > 0 else 0.0

    # Format décimal global
    global_decimal = decimal_formats.most_common(1)[0][0] if decimal_formats else None

    # Affecter le format décimal aux colonnes numériques
    for profile in profiles:
        if profile.dominant_type == CellType.DECIMAL:
            # Analyser les échantillons de cette colonne spécifiquement
            col_formats = Counter()
            for val in profile.sample_values:
                fmt = detect_decimal_format(val)
                if fmt:
                    col_formats[fmt] += 1
            if col_formats:
                profile.decimal_format = col_formats.most_common(1)[0][0]
            else:
                profile.decimal_format = global_decimal

    logger.info(
        f"Profils: {[f'col{p.index}={p.dominant_type.name}' for p in profiles]}"
    )
    return profiles


def run_detection(filepath: Path, progress_callback=None) -> DetectionResult:
    """Exécute la détection complète sur un fichier.

    Args:
        filepath: Chemin vers le fichier CSV.
        progress_callback: Fonction(step, total, message) pour le suivi.

    Returns:
        DetectionResult avec tous les résultats.
    """
    fp = Path(filepath)

    def progress(step: int, total: int, msg: str):
        logger.info(f"[{step}/{total}] {msg}")
        if progress_callback:
            progress_callback(step, total, msg)

    # Étape 1 : Encodage
    progress(1, 6, "Détection de l'encodage...")
    encoding = detect_encoding(fp)

    # Étape 2 : Comptage lignes
    progress(2, 6, "Comptage des lignes...")
    total_lines = count_lines(fp, encoding)

    # Étape 3 : Séparateur
    progress(3, 6, "Détection du séparateur...")
    separator, sep_confidence = detect_separator(fp, encoding)

    # Étape 4 : Nombre de colonnes
    progress(4, 6, "Analyse du nombre de colonnes...")
    expected_cols, col_distribution = detect_column_count(fp, separator, encoding)

    # Étape 5 : Header
    progress(5, 6, "Lecture de l'en-tête...")
    header_line = next(read_lines_raw(fp, encoding, max_lines=1), '')
    header = header_line.split(separator)

    # Étape 6 : Profiling colonnes
    progress(6, 6, "Profiling des colonnes...")
    sampled = sample_lines(fp, encoding, n_head=1000, n_random=1000, total_lines=total_lines)
    # Exclure le header de l'échantillon
    if sampled and sampled[0] == header_line:
        sampled = sampled[1:]

    col_profiles = profile_columns(sampled, separator, expected_cols)

    # Format décimal global
    decimal_counts: Counter[str] = Counter()
    for p in col_profiles:
        if p.decimal_format:
            decimal_counts[p.decimal_format] += 1
    global_decimal = decimal_counts.most_common(1)[0][0] if decimal_counts else None

    return DetectionResult(
        filepath=fp,
        encoding=encoding,
        separator=separator,
        separator_confidence=sep_confidence,
        total_lines=total_lines,
        header=header,
        expected_columns=expected_cols,
        column_profiles=col_profiles,
        column_count_distribution=col_distribution,
        decimal_format=global_decimal,
    )
