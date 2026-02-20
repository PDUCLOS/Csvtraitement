"""Jointure intelligente entre deux fichiers tabulaires.

Fonctionnalités :
- Détection automatique des colonnes communes (nom, contenu)
- Proposition de clé de jointure avec score de confiance
- Stratégies multiples de matching (exact, case-insensitive, fuzzy, normalisé)
- Sélection des colonnes à reprendre dans la sortie
- Types de jointure : INNER, LEFT, RIGHT, FULL (OUTER)
"""

import logging
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from utils.universal_reader import read_any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Taille de l'échantillon pour l'analyse de contenu
SAMPLE_SIZE = 1000


class JoinType:
    """Types de jointure supportés."""
    INNER = 'inner'       # Seulement les lignes matchées
    LEFT = 'left'         # Toutes les lignes du fichier gauche
    RIGHT = 'right'       # Toutes les lignes du fichier droit
    FULL = 'full'         # Toutes les lignes des deux fichiers


class MatchStrategy:
    """Stratégies de matching sur la clé."""
    EXACT = 'exact'                     # Correspondance exacte
    CASE_INSENSITIVE = 'case_insensitive'  # Ignorer la casse
    NORMALIZED = 'normalized'           # Sans accents, casse, espaces normalisés
    FUZZY = 'fuzzy'                     # Similarité > seuil (difflib)
    NUMERIC = 'numeric'                 # Comparaison numérique (ignore formatage)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class KeyCandidate:
    """Proposition de clé de jointure."""
    left_column: int          # Index colonne fichier gauche
    left_name: str            # Nom colonne fichier gauche
    right_column: int         # Index colonne fichier droit
    right_name: str           # Nom colonne fichier droit
    confidence: float         # Score de confiance 0-1
    match_count: int = 0      # Nombre de valeurs communes trouvées
    match_rate_left: float = 0.0   # % de valeurs gauche trouvées à droite
    match_rate_right: float = 0.0  # % de valeurs droite trouvées à gauche
    reason: str = ''          # Explication du score


@dataclass
class JoinColumn:
    """Colonne sélectionnée pour la sortie."""
    source: str               # 'left' ou 'right'
    index: int                # Index dans le fichier source
    name: str                 # Nom de la colonne
    alias: str = ''           # Alias de sortie (si conflit de noms)


@dataclass
class JoinResult:
    """Résultat de la jointure."""
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    matched_count: int = 0
    left_only_count: int = 0
    right_only_count: int = 0
    total_rows: int = 0
    join_type: str = JoinType.INNER
    strategy: str = MatchStrategy.EXACT
    key_left: list[int] = field(default_factory=list)
    key_right: list[int] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return self.matched_count / self.total_rows * 100


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------

def _normalize(value: str) -> str:
    """Normalise une valeur : lowercase, sans accents, espaces condensés."""
    result = value.strip().lower()
    nfkd = unicodedata.normalize('NFKD', result)
    result = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    result = ' '.join(result.split())
    return result


def _normalize_numeric(value: str) -> Optional[float]:
    """Tente de parser une valeur numérique (FR ou US)."""
    v = value.strip()
    if not v:
        return None
    # Parenthèses comptables
    if v.startswith('(') and v.endswith(')'):
        v = '-' + v[1:-1]
    # Supprimer les espaces et nbsp
    v = v.replace('\xa0', '').replace(' ', '')
    # Format FR : 1.234.567,89
    if ',' in v and '.' in v:
        if v.rindex(',') > v.rindex('.'):
            v = v.replace('.', '').replace(',', '.')
        else:
            v = v.replace(',', '')
    elif ',' in v:
        v = v.replace(',', '.')
    try:
        return float(v)
    except ValueError:
        return None


def _header_similarity(name_a: str, name_b: str) -> float:
    """Calcule la similarité entre deux noms de colonnes."""
    na = _normalize(name_a)
    nb = _normalize(name_b)
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    # Vérifier inclusion
    if na in nb or nb in na:
        return 0.85
    return SequenceMatcher(None, na, nb).ratio()


def _value_overlap(values_a: set[str], values_b: set[str],
                   normalize_fn=None) -> tuple[int, float, float]:
    """Calcule le chevauchement entre deux ensembles de valeurs.

    Returns:
        (count, rate_a, rate_b) — nombre commun, % de A dans B, % de B dans A.
    """
    if normalize_fn:
        set_a = {normalize_fn(v) for v in values_a if v.strip()}
        set_b = {normalize_fn(v) for v in values_b if v.strip()}
    else:
        set_a = {v.strip() for v in values_a if v.strip()}
        set_b = {v.strip() for v in values_b if v.strip()}

    if not set_a or not set_b:
        return 0, 0.0, 0.0

    common = set_a & set_b
    count = len(common)
    rate_a = count / len(set_a) if set_a else 0.0
    rate_b = count / len(set_b) if set_b else 0.0
    return count, rate_a, rate_b


# ---------------------------------------------------------------------------
# Détection de clé
# ---------------------------------------------------------------------------

def detect_key_candidates(
    header_left: list[str],
    rows_left: list[list[str]],
    header_right: list[str],
    rows_right: list[list[str]],
    sample_size: int = SAMPLE_SIZE,
    min_confidence: float = 0.1,
) -> list[KeyCandidate]:
    """Détecte les colonnes candidates pour la jointure.

    Analyse en 3 phases :
    1. Similarité des noms de colonnes
    2. Chevauchement des valeurs (échantillon)
    3. Score combiné pondéré

    Args:
        header_left: En-têtes du fichier gauche.
        rows_left: Lignes du fichier gauche.
        header_right: En-têtes du fichier droit.
        rows_right: Lignes du fichier droit.
        sample_size: Taille de l'échantillon pour l'analyse.
        min_confidence: Seuil minimum de confiance pour proposer.

    Returns:
        Liste de KeyCandidate triée par confiance décroissante.
    """
    candidates = []

    # Échantillonner les données
    sample_l = rows_left[:sample_size]
    sample_r = rows_right[:sample_size]

    for i, name_l in enumerate(header_left):
        # Collecter les valeurs uniques de la colonne gauche
        vals_l = set()
        for row in sample_l:
            if i < len(row) and row[i].strip():
                vals_l.add(row[i].strip())

        if not vals_l:
            continue

        for j, name_r in enumerate(header_right):
            # Collecter les valeurs uniques de la colonne droite
            vals_r = set()
            for row in sample_r:
                if j < len(row) and row[j].strip():
                    vals_r.add(row[j].strip())

            if not vals_r:
                continue

            # Phase 1 : Similarité des noms
            name_sim = _header_similarity(name_l, name_r)

            # Phase 2 : Chevauchement des valeurs (exact)
            overlap_count, rate_l, rate_r = _value_overlap(vals_l, vals_r)

            # Phase 2b : Chevauchement normalisé (si exact faible)
            if rate_l < 0.3 and rate_r < 0.3:
                overlap_count_n, rate_l_n, rate_r_n = _value_overlap(
                    vals_l, vals_r, normalize_fn=_normalize
                )
                if rate_l_n > rate_l:
                    overlap_count, rate_l, rate_r = overlap_count_n, rate_l_n, rate_r_n

            # Phase 3 : Score combiné
            # Pondération : nom 30%, chevauchement 70%
            overlap_score = (rate_l + rate_r) / 2
            confidence = name_sim * 0.3 + overlap_score * 0.7

            # Bonus si nom identique et overlap > 0
            if name_sim >= 0.95 and overlap_count > 0:
                confidence = max(confidence, 0.8)

            # Bonus si overlap très élevé même sans match de nom
            if overlap_score > 0.5 and overlap_count > 10:
                confidence = max(confidence, 0.6)

            if confidence >= min_confidence:
                # Construire l'explication
                reasons = []
                if name_sim >= 0.95:
                    reasons.append(f"noms identiques ({name_l!r})")
                elif name_sim > 0.6:
                    reasons.append(f"noms similaires ({name_sim:.0%})")

                if overlap_score > 0.5:
                    reasons.append(f"fort chevauchement ({overlap_count} valeurs communes)")
                elif overlap_count > 0:
                    reasons.append(f"{overlap_count} valeurs communes")

                candidate = KeyCandidate(
                    left_column=i,
                    left_name=name_l,
                    right_column=j,
                    right_name=name_r,
                    confidence=round(confidence, 3),
                    match_count=overlap_count,
                    match_rate_left=round(rate_l, 3),
                    match_rate_right=round(rate_r, 3),
                    reason=' + '.join(reasons) if reasons else 'faible correspondance',
                )
                candidates.append(candidate)

    # Trier par confiance décroissante
    candidates.sort(key=lambda c: c.confidence, reverse=True)

    logger.info(f"Détection de clé : {len(candidates)} candidats trouvés")
    for c in candidates[:5]:
        logger.info(
            f"  [{c.confidence:.0%}] {c.left_name!r} (L:{c.left_column}) "
            f"↔ {c.right_name!r} (R:{c.right_column}) — {c.reason}"
        )

    return candidates


# ---------------------------------------------------------------------------
# Construction de la clé
# ---------------------------------------------------------------------------

def _build_key(cells: list[str], key_columns: list[int],
               strategy: str = MatchStrategy.EXACT) -> str:
    """Construit la clé de jointure selon la stratégie."""
    parts = []
    for col in key_columns:
        val = cells[col].strip() if col < len(cells) else ''
        if strategy == MatchStrategy.CASE_INSENSITIVE:
            val = val.lower()
        elif strategy == MatchStrategy.NORMALIZED:
            val = _normalize(val)
        elif strategy == MatchStrategy.NUMERIC:
            num = _normalize_numeric(val)
            val = str(num) if num is not None else val.strip()
        parts.append(val)
    return '|'.join(parts)


# ---------------------------------------------------------------------------
# Jointure
# ---------------------------------------------------------------------------

def smart_join(
    header_left: list[str],
    rows_left: list[list[str]],
    header_right: list[str],
    rows_right: list[list[str]],
    key_left: list[int],
    key_right: list[int],
    join_type: str = JoinType.INNER,
    strategy: str = MatchStrategy.EXACT,
    fuzzy_threshold: float = 0.85,
    columns_left: Optional[list[int]] = None,
    columns_right: Optional[list[int]] = None,
    suffixes: tuple[str, str] = ('_gauche', '_droite'),
    progress_callback=None,
) -> JoinResult:
    """Effectue une jointure intelligente entre deux jeux de données.

    Args:
        header_left: En-têtes du fichier gauche.
        rows_left: Lignes du fichier gauche.
        header_right: En-têtes du fichier droit.
        rows_right: Lignes du fichier droit.
        key_left: Indices des colonnes-clés dans le fichier gauche.
        key_right: Indices des colonnes-clés dans le fichier droit.
        join_type: Type de jointure (inner, left, right, full).
        strategy: Stratégie de matching.
        fuzzy_threshold: Seuil pour le fuzzy matching.
        columns_left: Colonnes à reprendre du fichier gauche (None = toutes).
        columns_right: Colonnes à reprendre du fichier droit (None = toutes sauf clé).
        suffixes: Suffixes pour désambiguïser les noms de colonnes.
        progress_callback: Callback de progression.

    Returns:
        JoinResult avec les données fusionnées.
    """
    # Colonnes sélectionnées
    if columns_left is None:
        columns_left = list(range(len(header_left)))
    if columns_right is None:
        # Par défaut : toutes sauf les colonnes-clés (déjà présentes via gauche)
        columns_right = [c for c in range(len(header_right)) if c not in key_right]

    # Construire le header de sortie
    out_header = []
    used_names = set()

    for col in columns_left:
        name = header_left[col] if col < len(header_left) else f'col_{col}'
        if name in used_names:
            name = name + suffixes[0]
        used_names.add(name)
        out_header.append(name)

    for col in columns_right:
        name = header_right[col] if col < len(header_right) else f'col_{col}'
        if name in used_names:
            name = name + suffixes[1]
        used_names.add(name)
        out_header.append(name)

    n_left_cols = len(columns_left)
    n_right_cols = len(columns_right)
    empty_right = [''] * n_right_cols
    empty_left = [''] * n_left_cols

    # Indexer le fichier droit
    index_right: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
    for idx, row in enumerate(rows_right):
        key = _build_key(row, key_right, strategy)
        index_right[key].append((idx, row))
        if progress_callback and idx % 10_000 == 0:
            progress_callback(idx, len(rows_right) + len(rows_left),
                              f"Indexation droite : {idx} lignes")

    # Pour le fuzzy matching, construire la liste des clés droites
    right_keys_list = None
    if strategy == MatchStrategy.FUZZY:
        right_keys_list = list(index_right.keys())

    # Parcourir le fichier gauche
    result = JoinResult(
        header=out_header,
        join_type=join_type,
        strategy=strategy,
        key_left=key_left,
        key_right=key_right,
    )

    matched_right_indices: set[int] = set()

    for left_idx, row_left in enumerate(rows_left):
        key = _build_key(row_left, key_left, strategy)

        # Chercher les correspondances
        matches = index_right.get(key, [])

        # Fuzzy fallback
        if not matches and strategy == MatchStrategy.FUZZY and right_keys_list:
            best_sim = 0.0
            best_key = None
            for rk in right_keys_list:
                sim = SequenceMatcher(None, key, rk).ratio()
                if sim > best_sim:
                    best_sim = sim
                    best_key = rk
            if best_sim >= fuzzy_threshold and best_key is not None:
                matches = index_right[best_key]

        if matches:
            # Jointure : une ligne pour chaque match
            for right_idx, row_right in matches:
                matched_right_indices.add(right_idx)
                out_row = _extract_cols(row_left, columns_left) + \
                          _extract_cols(row_right, columns_right)
                result.rows.append(out_row)
                result.matched_count += 1
        elif join_type in (JoinType.LEFT, JoinType.FULL):
            out_row = _extract_cols(row_left, columns_left) + empty_right
            result.rows.append(out_row)
            result.left_only_count += 1

        if progress_callback and left_idx % 10_000 == 0:
            progress_callback(
                len(rows_right) + left_idx,
                len(rows_right) + len(rows_left),
                f"Jointure : {left_idx} lignes traitées"
            )

    # Lignes du fichier droit non matchées (RIGHT / FULL)
    if join_type in (JoinType.RIGHT, JoinType.FULL):
        for right_idx, row_right in enumerate(rows_right):
            if right_idx not in matched_right_indices:
                out_row = empty_left + _extract_cols(row_right, columns_right)
                result.rows.append(out_row)
                result.right_only_count += 1

    result.total_rows = len(result.rows)

    logger.info(
        f"Jointure {join_type}: {result.matched_count} matchés, "
        f"{result.left_only_count} gauche seul, "
        f"{result.right_only_count} droite seul, "
        f"{result.total_rows} lignes totales"
    )

    return result


def _extract_cols(row: list[str], columns: list[int]) -> list[str]:
    """Extrait les colonnes sélectionnées d'une ligne."""
    return [row[c] if c < len(row) else '' for c in columns]


# ---------------------------------------------------------------------------
# Fonction haut niveau (lecture fichiers + jointure)
# ---------------------------------------------------------------------------

def join_files(
    filepath_left,
    filepath_right,
    key_left: Optional[list[int]] = None,
    key_right: Optional[list[int]] = None,
    join_type: str = JoinType.INNER,
    strategy: str = MatchStrategy.EXACT,
    fuzzy_threshold: float = 0.85,
    columns_left: Optional[list[int]] = None,
    columns_right: Optional[list[int]] = None,
    auto_detect_key: bool = True,
    sheet_left: Optional[str] = None,
    sheet_right: Optional[str] = None,
    progress_callback=None,
) -> tuple[JoinResult, list[KeyCandidate]]:
    """Jointure intelligente entre deux fichiers (tout format supporté).

    Si key_left/key_right ne sont pas fournis et auto_detect_key=True,
    détecte automatiquement la meilleure clé de jointure.

    Args:
        filepath_left: Chemin du fichier gauche.
        filepath_right: Chemin du fichier droit.
        key_left: Colonnes-clés gauche (None = auto-détection).
        key_right: Colonnes-clés droite (None = auto-détection).
        join_type: Type de jointure.
        strategy: Stratégie de matching.
        fuzzy_threshold: Seuil fuzzy.
        columns_left: Colonnes à reprendre (None = toutes).
        columns_right: Colonnes à reprendre (None = toutes sauf clé).
        auto_detect_key: Détecter automatiquement la clé.
        sheet_left: Feuille pour le fichier gauche (Excel/ODS).
        sheet_right: Feuille pour le fichier droit (Excel/ODS).
        progress_callback: Callback de progression.

    Returns:
        Tuple (JoinResult, list[KeyCandidate]).
    """
    from pathlib import Path

    # Lire les fichiers
    if progress_callback:
        progress_callback(0, 100, "Lecture du fichier gauche...")
    header_l, rows_l, _ = read_any(Path(filepath_left), sheet_name=sheet_left)

    if progress_callback:
        progress_callback(20, 100, "Lecture du fichier droit...")
    header_r, rows_r, _ = read_any(Path(filepath_right), sheet_name=sheet_right)

    # Détecter les clés
    if progress_callback:
        progress_callback(40, 100, "Analyse des colonnes...")
    candidates = detect_key_candidates(header_l, rows_l, header_r, rows_r)

    # Si clés non fournies, utiliser la meilleure proposition
    if key_left is None or key_right is None:
        if auto_detect_key and candidates:
            best = candidates[0]
            key_left = [best.left_column]
            key_right = [best.right_column]
            logger.info(
                f"Clé auto-détectée : {best.left_name!r} ↔ {best.right_name!r} "
                f"(confiance: {best.confidence:.0%})"
            )
        else:
            # Fallback : première colonne
            key_left = [0]
            key_right = [0]
            logger.warning("Aucune clé détectée, utilisation de la colonne 0")

    # Jointure
    if progress_callback:
        progress_callback(50, 100, "Jointure en cours...")

    def _inner_progress(current, total, msg):
        if progress_callback:
            pct = 50 + int(current / max(total, 1) * 50)
            progress_callback(pct, 100, msg)

    join_result = smart_join(
        header_l, rows_l,
        header_r, rows_r,
        key_left=key_left,
        key_right=key_right,
        join_type=join_type,
        strategy=strategy,
        fuzzy_threshold=fuzzy_threshold,
        columns_left=columns_left,
        columns_right=columns_right,
        progress_callback=_inner_progress,
    )

    if progress_callback:
        progress_callback(100, 100, "Jointure terminée")

    return join_result, candidates
