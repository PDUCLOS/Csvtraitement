"""Détection avancée de doublons avec fuzzy matching.

Fonctionnalités :
- Doublons exacts sur colonnes-clés
- Fuzzy matching (distance de similarité via difflib)
- Normalisation préalable (accents, casse, espaces)
- Doublons partiels (mêmes clés, valeurs différentes)
- Groupement des doublons avec stratégie de sélection
"""

import logging
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from core.detector import DetectionResult
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    """Groupe de lignes dupliquées."""
    group_id: int
    key: str                              # Clé commune du groupe
    line_numbers: list[int] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    match_type: str = 'exact'             # 'exact' ou 'fuzzy'
    similarity: float = 1.0              # Score de similarité (0-1)
    differences: list[tuple[int, str, str]] = field(default_factory=list)  # (col, val1, val2)

    @property
    def count(self) -> int:
        return len(self.line_numbers)

    @property
    def is_exact(self) -> bool:
        return self.match_type == 'exact'


@dataclass
class DedupResult:
    """Résultat de la déduplication."""
    total_rows: int = 0
    unique_rows: int = 0
    exact_duplicate_groups: int = 0
    exact_duplicate_rows: int = 0
    fuzzy_duplicate_groups: int = 0
    fuzzy_duplicate_rows: int = 0
    fuzzy_comparisons: int = 0
    groups: list[DuplicateGroup] = field(default_factory=list)
    lines_to_keep: set[int] = field(default_factory=set)
    lines_to_remove: set[int] = field(default_factory=set)

    @property
    def total_duplicate_rows(self) -> int:
        return self.exact_duplicate_rows + self.fuzzy_duplicate_rows

    @property
    def duplicate_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return self.total_duplicate_rows / self.total_rows * 100


class SelectionStrategy:
    """Stratégie de sélection de la ligne à garder."""
    FIRST = 'first'         # Garder la première occurrence
    LAST = 'last'           # Garder la dernière
    MOST_COMPLETE = 'most_complete'  # Garder la plus complète


def _normalize_for_comparison(value: str, strip_accents: bool = True, lower: bool = True) -> str:
    """Normalise une valeur pour la comparaison fuzzy."""
    result = value.strip()
    if lower:
        result = result.lower()
    if strip_accents:
        nfkd = unicodedata.normalize('NFKD', result)
        result = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    # Normaliser les espaces
    result = ' '.join(result.split())
    return result


def _row_completeness(cells: list[str]) -> int:
    """Score de complétude : nombre de cellules non vides."""
    return sum(1 for c in cells if c.strip())


def _compute_similarity(s1: str, s2: str, threshold: float = 0.0) -> float:
    """Calcule la similarité entre deux strings (0-1).

    Si ``threshold`` est fourni, les bornes supérieures bon marché de difflib
    (``real_quick_ratio`` puis ``quick_ratio``) écartent d'abord les paires qui ne
    peuvent pas l'atteindre : la valeur renvoyée est alors une borne < threshold,
    et le résultat (paire retenue ou non) est identique au calcul complet.
    """
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    sm = SequenceMatcher(None, s1, s2)
    if threshold > 0.0:
        bound = sm.real_quick_ratio()
        if bound < threshold:
            return bound
        bound = sm.quick_ratio()
        if bound < threshold:
            return bound
    return sm.ratio()


def _build_key(cells: list[str], key_columns: list[int], normalize: bool = True) -> str:
    """Construit une clé à partir des colonnes spécifiées."""
    parts = []
    for col in key_columns:
        val = cells[col].strip() if col < len(cells) else ''
        if normalize:
            val = _normalize_for_comparison(val)
        parts.append(val)
    return '|'.join(parts)


def _build_blocks(
    entries: list[tuple[str, tuple[int, list[str]]]],
    key_columns: list[int],
    max_block_size: int = 200,
) -> list[list[int]]:
    """Blocage multi-clés : candidats = lignes partageant une valeur exacte sur une colonne discriminante.

    Deux quasi-doublons (une faute de frappe dans une cellule) partagent forcément les
    autres cellules à l'identique. On indexe donc chaque ligne par la valeur normalisée
    de chaque colonne à forte cardinalité (identifiant, e-mail, téléphone...) et l'on ne
    compare que les lignes d'un même bloc. Repli : préfixe de 3 caractères de la clé
    (comportement historique) si aucune colonne n'est discriminante.

    Returns:
        Liste de blocs (indices dans ``entries``) de taille 2..max_block_size.
    """
    n = len(entries)
    if n < 2:
        return []
    parts = [key.split('|') for key, _ in entries]
    n_parts = len(parts[0])
    discriminant = []
    for c in range(n_parts):
        values = [p[c] for p in parts if c < len(p) and p[c]]
        if values and len(set(values)) / n >= 0.3:
            discriminant.append(c)

    index: dict[tuple, list[int]] = defaultdict(list)
    if discriminant:
        for idx, p in enumerate(parts):
            for c in discriminant:
                if c < len(p) and p[c]:
                    index[(c, p[c])].append(idx)
    else:
        for idx, (key, _) in enumerate(entries):
            index[('prefix', key[:3])].append(idx)

    blocks = [ids for ids in index.values() if 2 <= len(ids) <= max_block_size]
    skipped = sum(1 for ids in index.values() if len(ids) > max_block_size)
    if skipped:
        logger.info(f"Fuzzy: {skipped} blocs trop larges ignorés (> {max_block_size} lignes)")
    return blocks


def find_duplicates(
    detection: DetectionResult,
    key_columns: Optional[list[int]] = None,
    fuzzy_threshold: float = 0.85,
    enable_fuzzy: bool = True,
    normalize: bool = True,
    strategy: str = SelectionStrategy.FIRST,
    max_groups: int = 10_000,
    fuzzy_max_rows: int = 1_000_000,
    max_block_size: int = 200,
    progress_callback=None
) -> DedupResult:
    """Détecte les doublons exacts et fuzzy dans un fichier CSV.

    Args:
        detection: Résultat de détection.
        key_columns: Colonnes-clés pour la comparaison (None = toutes).
        fuzzy_threshold: Seuil de similarité pour le fuzzy matching (0-1).
        enable_fuzzy: Activer le fuzzy matching.
        normalize: Normaliser avant comparaison (accents, casse).
        strategy: Stratégie de sélection ('first', 'last', 'most_complete').
        max_groups: Nombre max de groupes à retourner.
        fuzzy_max_rows: Nombre max de lignes uniques soumises au fuzzy matching.
        max_block_size: Taille max d'un bloc de candidats (au-delà, le bloc est ignoré
            car la valeur partagée n'est pas discriminante).
        progress_callback: Fonction de suivi.

    Returns:
        DedupResult avec tous les groupes de doublons.
    """
    n_cols = detection.expected_columns
    if key_columns is None:
        key_columns = list(range(n_cols))

    result = DedupResult()
    group_id = 0

    # Phase 1 : Doublons exacts (hash-based)
    exact_groups: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)

    total_rows = 0
    for chunk in read_chunks(
        detection.filepath, detection.separator,
        detection.encoding, chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            total_rows += 1
            line_num = total_rows + 1  # +1 pour le header

            key = _build_key(cells, key_columns, normalize=normalize)
            exact_groups[key].append((line_num, list(cells[:n_cols])))

        if progress_callback:
            progress_callback(total_rows, detection.total_lines,
                              f"Doublons: indexation {total_rows} lignes")

    result.total_rows = total_rows

    # Construire les groupes exacts
    exact_keys_with_dups = []
    for key, entries in exact_groups.items():
        if len(entries) > 1:
            if len(result.groups) >= max_groups:
                break

            group = DuplicateGroup(
                group_id=group_id,
                key=key,
                line_numbers=[e[0] for e in entries],
                rows=[e[1] for e in entries],
                match_type='exact',
                similarity=1.0,
            )
            result.groups.append(group)
            result.exact_duplicate_groups += 1
            result.exact_duplicate_rows += len(entries) - 1
            group_id += 1
            exact_keys_with_dups.append(key)

    # Phase 2 : Fuzzy matching (un représentant par clé exacte)
    if enable_fuzzy and fuzzy_threshold < 1.0:
        unique_entries = [(key, entries[0]) for key, entries in exact_groups.items()]

        if len(unique_entries) > fuzzy_max_rows:
            unique_entries = unique_entries[:fuzzy_max_rows]
            logger.warning(f"Fuzzy matching limité aux {fuzzy_max_rows:,} premières lignes uniques")

        blocks = _build_blocks(unique_entries, key_columns, max_block_size)

        fuzzy_checked = 0
        seen_pairs: set[tuple[int, int]] = set()
        for block in blocks:
            for a in range(len(block)):
                for b in range(a + 1, len(block)):
                    if len(result.groups) >= max_groups:
                        break
                    i, j = block[a], block[b]
                    if (i, j) in seen_pairs:
                        continue
                    seen_pairs.add((i, j))

                    key_i, (line_i, cells_i) = unique_entries[i]
                    key_j, (line_j, cells_j) = unique_entries[j]

                    sim = _compute_similarity(key_i, key_j, fuzzy_threshold)
                    fuzzy_checked += 1

                    if sim >= fuzzy_threshold:
                        # Trouver les différences
                        diffs = []
                        for col in range(min(len(cells_i), len(cells_j))):
                            vi = cells_i[col].strip()
                            vj = cells_j[col].strip()
                            if vi != vj:
                                diffs.append((col, vi, vj))

                        group = DuplicateGroup(
                            group_id=group_id,
                            key=f"~{key_i[:50]}",
                            line_numbers=[line_i, line_j],
                            rows=[cells_i, cells_j],
                            match_type='fuzzy',
                            similarity=sim,
                            differences=diffs,
                        )
                        result.groups.append(group)
                        result.fuzzy_duplicate_groups += 1
                        result.fuzzy_duplicate_rows += 1
                        group_id += 1

        result.fuzzy_comparisons = fuzzy_checked
        logger.info(f"Fuzzy: {fuzzy_checked:,} comparaisons sur {len(blocks):,} blocs")
        if progress_callback:
            progress_callback(total_rows, total_rows,
                              f"Fuzzy: {fuzzy_checked} comparaisons")

    # Phase 3 : Déterminer quelles lignes garder/supprimer
    result.unique_rows = total_rows - result.total_duplicate_rows

    for group in result.groups:
        if strategy == SelectionStrategy.FIRST:
            keep_idx = 0
        elif strategy == SelectionStrategy.LAST:
            keep_idx = len(group.line_numbers) - 1
        elif strategy == SelectionStrategy.MOST_COMPLETE:
            completeness = [_row_completeness(r) for r in group.rows]
            keep_idx = completeness.index(max(completeness))
        else:
            keep_idx = 0

        for idx, line_num in enumerate(group.line_numbers):
            if idx == keep_idx:
                result.lines_to_keep.add(line_num)
            else:
                result.lines_to_remove.add(line_num)

    logger.info(
        f"Déduplication: {result.exact_duplicate_groups} groupes exacts "
        f"({result.exact_duplicate_rows} lignes), "
        f"{result.fuzzy_duplicate_groups} groupes fuzzy "
        f"({result.fuzzy_duplicate_rows} lignes)"
    )
    return result
