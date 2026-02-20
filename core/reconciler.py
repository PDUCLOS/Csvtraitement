"""Rapprochement / réconciliation entre deux fichiers CSV.

Cas d'usage : factures vs paiements, commandes vs livraisons.

Fonctionnalités :
- Jointure sur une ou plusieurs colonnes-clés
- Comparaison de colonnes de montant avec tolérance
- Statut par ligne : Matché, Non trouvé (gauche/droite), Écart
- Export du rapprochement avec résumé
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from core.detector import DetectionResult
from utils.patterns import normalize_decimal, normalize_integer
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)


class MatchStatus:
    """Statut de rapprochement d'une ligne."""
    MATCHED = 'matched'                   # Trouvé + montants OK
    MISMATCH = 'mismatch'                 # Trouvé + écart sur montants
    LEFT_ONLY = 'left_only'               # Présent uniquement dans fichier gauche
    RIGHT_ONLY = 'right_only'             # Présent uniquement dans fichier droit


@dataclass
class ReconciliationMatch:
    """Un rapprochement entre deux lignes."""
    status: str
    key: str
    line_left: Optional[int] = None
    line_right: Optional[int] = None
    cells_left: list[str] = field(default_factory=list)
    cells_right: list[str] = field(default_factory=list)
    amount_left: Optional[float] = None
    amount_right: Optional[float] = None
    difference: Optional[float] = None
    compare_details: list[tuple[str, str, str]] = field(default_factory=list)  # (col_name, val_left, val_right)


@dataclass
class ReconciliationResult:
    """Résultat du rapprochement."""
    file_left: str
    file_right: str
    total_left: int = 0
    total_right: int = 0
    matched: int = 0
    mismatched: int = 0
    left_only: int = 0
    right_only: int = 0
    total_difference: float = 0.0         # Somme des écarts
    matches: list[ReconciliationMatch] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        total = max(self.total_left, self.total_right)
        if total == 0:
            return 100.0
        return (self.matched + self.mismatched) / total * 100

    @property
    def perfect_match_rate(self) -> float:
        total = max(self.total_left, self.total_right)
        if total == 0:
            return 100.0
        return self.matched / total * 100

    @property
    def summary(self) -> dict:
        return {
            'matched': self.matched,
            'mismatched': self.mismatched,
            'left_only': self.left_only,
            'right_only': self.right_only,
            'total_difference': self.total_difference,
            'match_rate': self.match_rate,
        }


def _parse_amount(value: str) -> Optional[float]:
    """Parse une valeur monétaire."""
    stripped = value.strip()
    if not stripped:
        return None
    num = normalize_decimal(stripped)
    if num is None:
        num_int = normalize_integer(stripped)
        if num_int is not None:
            return float(num_int)
    return num


def _build_key(cells: list[str], key_columns: list[int]) -> str:
    """Construit une clé de jointure."""
    parts = []
    for col in key_columns:
        val = cells[col].strip() if col < len(cells) else ''
        parts.append(val)
    return '|'.join(parts)


def reconcile(
    detection_left: DetectionResult,
    detection_right: DetectionResult,
    join_columns: list[int],
    compare_columns: Optional[list[tuple[int, int]]] = None,
    amount_columns: Optional[tuple[int, int]] = None,
    tolerance: float = 0.01,
    max_matches: int = 50_000,
    progress_callback=None
) -> ReconciliationResult:
    """Rapproche deux fichiers CSV.

    Args:
        detection_left: Détection du fichier gauche.
        detection_right: Détection du fichier droit.
        join_columns: Indices des colonnes de jointure (dans les deux fichiers).
        compare_columns: Paires (col_left, col_right) à comparer. None = auto.
        amount_columns: Tuple (col_left, col_right) pour les montants à rapprocher.
        tolerance: Tolérance absolue sur les montants.
        max_matches: Nombre max de résultats.
        progress_callback: Fonction de suivi.

    Returns:
        ReconciliationResult.
    """
    result = ReconciliationResult(
        file_left=str(detection_left.filepath.name),
        file_right=str(detection_right.filepath.name),
    )

    # Indexer le fichier droit
    index_right: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
    rows_right = 0

    for chunk in read_chunks(
        detection_right.filepath, detection_right.separator,
        detection_right.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            rows_right += 1
            line_num = rows_right + 1
            key = _build_key(cells, join_columns)
            index_right[key].append((line_num, list(cells)))

        if progress_callback:
            total_est = detection_left.total_lines + detection_right.total_lines
            progress_callback(rows_right, total_est,
                              f"Rapprochement: indexation droite ({rows_right})")

    result.total_right = rows_right
    matched_right_keys: set[str] = set()

    # Parcourir le fichier gauche et rapprocher
    header_left = [h.strip() for h in detection_left.header]
    header_right = [h.strip() for h in detection_right.header]

    rows_left = 0
    for chunk in read_chunks(
        detection_left.filepath, detection_left.separator,
        detection_left.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            rows_left += 1
            line_num_left = rows_left + 1
            key = _build_key(cells, join_columns)
            cells_left = list(cells)

            if key in index_right:
                entries = index_right[key]
                line_num_right, cells_right = entries[0]
                matched_right_keys.add(key)

                # Comparer les montants
                amt_left = None
                amt_right = None
                difference = None

                if amount_columns:
                    amt_left = _parse_amount(
                        cells_left[amount_columns[0]] if amount_columns[0] < len(cells_left) else ''
                    )
                    amt_right = _parse_amount(
                        cells_right[amount_columns[1]] if amount_columns[1] < len(cells_right) else ''
                    )

                    if amt_left is not None and amt_right is not None:
                        difference = amt_left - amt_right

                # Comparer les colonnes supplémentaires
                compare_details = []
                if compare_columns:
                    for col_l, col_r in compare_columns:
                        val_l = cells_left[col_l].strip() if col_l < len(cells_left) else ''
                        val_r = cells_right[col_r].strip() if col_r < len(cells_right) else ''
                        if val_l != val_r:
                            name_l = header_left[col_l] if col_l < len(header_left) else f'Col {col_l}'
                            compare_details.append((name_l, val_l, val_r))

                # Déterminer le statut
                if difference is not None and abs(difference) > tolerance:
                    status = MatchStatus.MISMATCH
                    result.mismatched += 1
                    result.total_difference += abs(difference)
                elif compare_details:
                    status = MatchStatus.MISMATCH
                    result.mismatched += 1
                else:
                    status = MatchStatus.MATCHED
                    result.matched += 1

                if len(result.matches) < max_matches:
                    result.matches.append(ReconciliationMatch(
                        status=status,
                        key=key,
                        line_left=line_num_left,
                        line_right=line_num_right,
                        cells_left=cells_left,
                        cells_right=cells_right,
                        amount_left=amt_left,
                        amount_right=amt_right,
                        difference=difference,
                        compare_details=compare_details,
                    ))
            else:
                result.left_only += 1
                if len(result.matches) < max_matches:
                    result.matches.append(ReconciliationMatch(
                        status=MatchStatus.LEFT_ONLY,
                        key=key,
                        line_left=line_num_left,
                        cells_left=cells_left,
                    ))

        if progress_callback:
            total_est = detection_left.total_lines + detection_right.total_lines
            progress_callback(
                detection_right.total_lines + rows_left, total_est,
                f"Rapprochement: {rows_left} lignes traitées"
            )

    result.total_left = rows_left

    # Lignes du fichier droit non rapprochées
    for key, entries in index_right.items():
        if key not in matched_right_keys:
            for line_num_right, cells_right in entries:
                result.right_only += 1
                if len(result.matches) < max_matches:
                    result.matches.append(ReconciliationMatch(
                        status=MatchStatus.RIGHT_ONLY,
                        key=key,
                        line_right=line_num_right,
                        cells_right=cells_right,
                    ))

    logger.info(
        f"Rapprochement: {result.matched} OK, {result.mismatched} écarts, "
        f"{result.left_only} gauche seul, {result.right_only} droite seul, "
        f"écart total: {result.total_difference:.2f}"
    )
    return result
