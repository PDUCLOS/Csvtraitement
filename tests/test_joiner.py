"""Tests pour le module core/joiner.py — Jointure intelligente."""

import pytest

from core.joiner import (
    detect_key_candidates,
    smart_join,
    JoinType,
    MatchStrategy,
    _normalize,
    _normalize_numeric,
    _header_similarity,
    _value_overlap,
    _build_key,
)


# ---------------------------------------------------------------------------
# Données de test
# ---------------------------------------------------------------------------

HEADER_L = ['id_client', 'nom', 'ville']
ROWS_L = [
    ['1', 'Dupont', 'Paris'],
    ['2', 'Martin', 'Lyon'],
    ['3', 'Durand', 'Marseille'],
    ['4', 'Bernard', 'Toulouse'],
]

HEADER_R = ['client_id', 'montant', 'date']
ROWS_R = [
    ['1', '100.50', '2024-01-15'],
    ['2', '200.00', '2024-02-20'],
    ['3', '50.25', '2024-03-10'],
    ['5', '300.00', '2024-04-01'],
]


# ---------------------------------------------------------------------------
# Tests _normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_basic(self):
        assert _normalize('  Hello World  ') == 'hello world'

    def test_accents(self):
        assert _normalize('Éléphant') == 'elephant'

    def test_spaces(self):
        assert _normalize('a   b\tc') == 'a b c'

    def test_empty(self):
        assert _normalize('') == ''


# ---------------------------------------------------------------------------
# Tests _normalize_numeric
# ---------------------------------------------------------------------------

class TestNormalizeNumeric:
    def test_integer(self):
        assert _normalize_numeric('42') == 42.0

    def test_us_decimal(self):
        assert _normalize_numeric('1,234.56') == 1234.56

    def test_fr_decimal(self):
        assert _normalize_numeric('1.234,56') == 1234.56

    def test_simple_comma(self):
        assert _normalize_numeric('3,14') == 3.14

    def test_parentheses(self):
        assert _normalize_numeric('(100)') == -100.0

    def test_empty(self):
        assert _normalize_numeric('') is None

    def test_text(self):
        assert _normalize_numeric('abc') is None


# ---------------------------------------------------------------------------
# Tests _header_similarity
# ---------------------------------------------------------------------------

class TestHeaderSimilarity:
    def test_identical(self):
        assert _header_similarity('id_client', 'id_client') == 1.0

    def test_case_insensitive(self):
        assert _header_similarity('ID_CLIENT', 'id_client') == 1.0

    def test_accents(self):
        assert _header_similarity('Numéro', 'numero') == 1.0

    def test_inclusion(self):
        assert _header_similarity('id', 'id_client') == 0.85

    def test_unrelated(self):
        sim = _header_similarity('date', 'montant')
        assert sim < 0.5


# ---------------------------------------------------------------------------
# Tests _value_overlap
# ---------------------------------------------------------------------------

class TestValueOverlap:
    def test_full_overlap(self):
        a = {'1', '2', '3'}
        b = {'1', '2', '3'}
        count, rate_a, rate_b = _value_overlap(a, b)
        assert count == 3
        assert rate_a == 1.0
        assert rate_b == 1.0

    def test_partial_overlap(self):
        a = {'1', '2', '3', '4'}
        b = {'1', '2', '3', '5'}
        count, rate_a, rate_b = _value_overlap(a, b)
        assert count == 3
        assert rate_a == 0.75
        assert rate_b == 0.75

    def test_no_overlap(self):
        a = {'a', 'b'}
        b = {'c', 'd'}
        count, rate_a, rate_b = _value_overlap(a, b)
        assert count == 0

    def test_with_normalize(self):
        a = {'Hello', 'World'}
        b = {'hello', 'WORLD'}
        count, _, _ = _value_overlap(a, b, normalize_fn=_normalize)
        assert count == 2


# ---------------------------------------------------------------------------
# Tests detect_key_candidates
# ---------------------------------------------------------------------------

class TestDetectKeyCandidates:
    def test_finds_id_match(self):
        candidates = detect_key_candidates(HEADER_L, ROWS_L, HEADER_R, ROWS_R)
        assert len(candidates) > 0
        # La meilleure clé devrait être id_client ↔ client_id (overlap + noms similaires)
        best = candidates[0]
        assert best.left_column == 0  # id_client
        assert best.right_column == 0  # client_id
        assert best.confidence > 0.3

    def test_identical_names(self):
        header_r = ['id_client', 'montant', 'date']
        candidates = detect_key_candidates(HEADER_L, ROWS_L, header_r, ROWS_R)
        best = candidates[0]
        assert best.left_name == 'id_client'
        assert best.right_name == 'id_client'
        assert best.confidence > 0.7

    def test_no_match(self):
        header_l = ['aaa', 'bbb']
        rows_l = [['x', 'y'], ['z', 'w']]
        header_r = ['ccc', 'ddd']
        rows_r = [['1', '2'], ['3', '4']]
        candidates = detect_key_candidates(header_l, rows_l, header_r, rows_r,
                                           min_confidence=0.8)
        # Avec un seuil élevé, rien ne devrait matcher
        assert all(c.confidence < 0.8 or c.match_count > 0 for c in candidates)


# ---------------------------------------------------------------------------
# Tests _build_key
# ---------------------------------------------------------------------------

class TestBuildKey:
    def test_exact(self):
        assert _build_key(['a', 'b', 'c'], [0, 2], MatchStrategy.EXACT) == 'a|c'

    def test_case_insensitive(self):
        assert _build_key(['ABC', 'def'], [0], MatchStrategy.CASE_INSENSITIVE) == 'abc'

    def test_normalized(self):
        assert _build_key(['Éléphant'], [0], MatchStrategy.NORMALIZED) == 'elephant'

    def test_numeric(self):
        key = _build_key(['1.234,56'], [0], MatchStrategy.NUMERIC)
        assert key == '1234.56'

    def test_out_of_range(self):
        assert _build_key(['a'], [0, 5], MatchStrategy.EXACT) == 'a|'


# ---------------------------------------------------------------------------
# Tests smart_join
# ---------------------------------------------------------------------------

class TestSmartJoin:
    def test_inner_join(self):
        result = smart_join(
            HEADER_L, ROWS_L, HEADER_R, ROWS_R,
            key_left=[0], key_right=[0],
            join_type=JoinType.INNER,
        )
        # IDs communs : 1, 2, 3
        assert result.matched_count == 3
        assert result.left_only_count == 0
        assert result.right_only_count == 0
        assert result.total_rows == 3
        # Header: id_client, nom, ville, montant, date
        assert len(result.header) == 5

    def test_left_join(self):
        result = smart_join(
            HEADER_L, ROWS_L, HEADER_R, ROWS_R,
            key_left=[0], key_right=[0],
            join_type=JoinType.LEFT,
        )
        assert result.matched_count == 3
        assert result.left_only_count == 1  # ID 4 sans match
        assert result.total_rows == 4

    def test_right_join(self):
        result = smart_join(
            HEADER_L, ROWS_L, HEADER_R, ROWS_R,
            key_left=[0], key_right=[0],
            join_type=JoinType.RIGHT,
        )
        assert result.matched_count == 3
        assert result.right_only_count == 1  # ID 5 sans match
        assert result.total_rows == 4

    def test_full_join(self):
        result = smart_join(
            HEADER_L, ROWS_L, HEADER_R, ROWS_R,
            key_left=[0], key_right=[0],
            join_type=JoinType.FULL,
        )
        assert result.matched_count == 3
        assert result.left_only_count == 1
        assert result.right_only_count == 1
        assert result.total_rows == 5

    def test_case_insensitive_strategy(self):
        rows_l = [['ABC', 'val1']]
        rows_r = [['abc', 'val2']]
        result = smart_join(
            ['key', 'data'], rows_l,
            ['key', 'data'], rows_r,
            key_left=[0], key_right=[0],
            strategy=MatchStrategy.CASE_INSENSITIVE,
        )
        assert result.matched_count == 1

    def test_normalized_strategy(self):
        rows_l = [['Éléphant', 'val1']]
        rows_r = [['elephant', 'val2']]
        result = smart_join(
            ['key', 'data'], rows_l,
            ['key', 'data'], rows_r,
            key_left=[0], key_right=[0],
            strategy=MatchStrategy.NORMALIZED,
        )
        assert result.matched_count == 1

    def test_numeric_strategy(self):
        rows_l = [['1.234,56', 'val1']]
        rows_r = [['1234.56', 'val2']]
        result = smart_join(
            ['montant', 'data'], rows_l,
            ['montant', 'data'], rows_r,
            key_left=[0], key_right=[0],
            strategy=MatchStrategy.NUMERIC,
        )
        assert result.matched_count == 1

    def test_column_selection(self):
        result = smart_join(
            HEADER_L, ROWS_L, HEADER_R, ROWS_R,
            key_left=[0], key_right=[0],
            columns_left=[0, 1],  # id_client, nom
            columns_right=[1],     # montant (pas date)
        )
        assert len(result.header) == 3  # id_client, nom, montant

    def test_suffix_disambiguation(self):
        header_l = ['id', 'name']
        header_r = ['id', 'name']
        rows_l = [['1', 'Alice']]
        rows_r = [['1', 'Bob']]
        result = smart_join(
            header_l, rows_l, header_r, rows_r,
            key_left=[0], key_right=[0],
            columns_left=[0, 1],
            columns_right=[1],  # name from right (will be suffixed)
        )
        assert 'name' in result.header
        assert 'name_droite' in result.header

    def test_empty_inputs(self):
        result = smart_join(
            ['a'], [], ['b'], [],
            key_left=[0], key_right=[0],
        )
        assert result.total_rows == 0

    def test_match_rate(self):
        result = smart_join(
            HEADER_L, ROWS_L, HEADER_R, ROWS_R,
            key_left=[0], key_right=[0],
            join_type=JoinType.INNER,
        )
        assert result.match_rate == 100.0  # All output rows are matched
