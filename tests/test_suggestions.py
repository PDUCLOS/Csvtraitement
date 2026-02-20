"""Tests pour core/suggestions.py — suggestions automatiques de validation."""

import pytest
from collections import Counter
from pathlib import Path

from core.suggestions import (
    generate_suggestions, format_suggestions_text,
    Suggestion, SuggestionPriority, SuggestionCategory,
    _detect_sensitive_patterns,
)
from core.detector import DetectionResult, ColumnProfile
from core.quality import QualityReport, ColumnQuality
from core.statistics import FileStats, ColumnStats
from utils.patterns import CellType


# ── Fixtures ──

def _make_detection() -> DetectionResult:
    p1 = ColumnProfile(index=0)
    p1.dominant_type = CellType.INTEGER
    p1.empty_ratio = 0.0
    p1.type_distribution = {CellType.INTEGER: 1000}
    p1.sample_values = ['1', '2', '3', '4', '5']

    p2 = ColumnProfile(index=1)
    p2.dominant_type = CellType.TEXT_SHORT
    p2.empty_ratio = 0.0
    p2.type_distribution = {CellType.TEXT_SHORT: 1000}
    p2.sample_values = ['alice@example.com', 'bob@test.fr', 'charlie@mail.org']

    p3 = ColumnProfile(index=2)
    p3.dominant_type = CellType.DECIMAL
    p3.empty_ratio = 0.1
    p3.decimal_format = 'FR'
    p3.type_distribution = {CellType.DECIMAL: 700, CellType.INTEGER: 200, CellType.EMPTY: 100}
    p3.sample_values = ['1.234,56', '5.678,90', '100']

    p4 = ColumnProfile(index=3)
    p4.dominant_type = CellType.DATE
    p4.empty_ratio = 0.85
    p4.type_distribution = {CellType.DATE: 100, CellType.EMPTY: 850, CellType.TEXT_SHORT: 50}
    p4.sample_values = ['01.01.2024', '', '', '']

    det = DetectionResult(
        filepath=Path('/tmp/test.csv'), encoding='utf-8', separator=';',
        separator_confidence=1.0, total_lines=1000,
        header=['ID', 'Email', 'Montant', 'Date'], expected_columns=4,
        column_profiles=[p1, p2, p3, p4],
        column_count_distribution={4: 980, 3: 20},
    )
    return det


# ── Tests suggestions generation ──

class TestGenerateSuggestions:
    def test_returns_list(self):
        det = _make_detection()
        suggestions = generate_suggestions(det)
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0

    def test_detects_empty_column(self):
        det = _make_detection()
        suggestions = generate_suggestions(det)
        empty_sugs = [s for s in suggestions if s.category == SuggestionCategory.MISSING]
        assert len(empty_sugs) > 0
        assert any('Date' in s.column_name for s in empty_sugs)

    def test_detects_email(self):
        det = _make_detection()
        suggestions = generate_suggestions(det)
        security_sugs = [s for s in suggestions if s.category == SuggestionCategory.SECURITY]
        assert len(security_sugs) > 0
        assert any('Email' in s.column_name for s in security_sugs)

    def test_detects_mixed_types(self):
        det = _make_detection()
        suggestions = generate_suggestions(det)
        type_sugs = [s for s in suggestions if s.category == SuggestionCategory.TYPE]
        # Montant has DECIMAL + INTEGER mixed
        assert len(type_sugs) > 0

    def test_sorted_by_priority(self):
        det = _make_detection()
        suggestions = generate_suggestions(det)
        priorities = [s.priority for s in suggestions]
        # HIGH should come before MEDIUM which comes before LOW
        high_indices = [i for i, p in enumerate(priorities) if p == SuggestionPriority.HIGH]
        medium_indices = [i for i, p in enumerate(priorities) if p == SuggestionPriority.MEDIUM]
        low_indices = [i for i, p in enumerate(priorities) if p == SuggestionPriority.LOW]
        if high_indices and medium_indices:
            assert max(high_indices) < min(medium_indices) or not medium_indices
        if medium_indices and low_indices:
            assert max(medium_indices) < min(low_indices)

    def test_with_quality_duplicates(self):
        det = _make_detection()
        quality = QualityReport(
            overall_score=55,
            overall_grade='D',
            column_scores=[],
            duplicate_rate=8.0,
            empty_row_rate=2.0,
            total_issues=5,
        )
        suggestions = generate_suggestions(det, quality=quality)
        dup_sugs = [s for s in suggestions if s.category == SuggestionCategory.DUPLICATE]
        assert len(dup_sugs) > 0

    def test_with_quality_low_score(self):
        det = _make_detection()
        quality = QualityReport(
            overall_score=40,
            overall_grade='D',
            column_scores=[],
            duplicate_rate=0,
            empty_row_rate=0,
            total_issues=10,
        )
        suggestions = generate_suggestions(det, quality=quality)
        validate_sugs = [s for s in suggestions if s.action == 'full_cleanup']
        assert len(validate_sugs) > 0

    def test_low_conformity(self):
        det = _make_detection()
        # Override distribution to get low conformity (70%)
        det.column_count_distribution = {4: 700, 3: 200, 5: 100}
        suggestions = generate_suggestions(det)
        format_sugs = [s for s in suggestions if s.action == 'fix_shifts']
        assert len(format_sugs) > 0

    def test_no_suggestions_for_clean_file(self):
        p1 = ColumnProfile(index=0)
        p1.dominant_type = CellType.INTEGER
        p1.empty_ratio = 0.0
        p1.type_distribution = {CellType.INTEGER: 100}
        p1.sample_values = ['1', '2', '3']

        p2 = ColumnProfile(index=1)
        p2.dominant_type = CellType.TEXT_SHORT
        p2.empty_ratio = 0.0
        p2.type_distribution = {CellType.TEXT_SHORT: 100}
        p2.sample_values = ['hello', 'world']

        det = DetectionResult(
            filepath=Path('/tmp/clean.csv'), encoding='utf-8', separator=';',
            separator_confidence=1.0, total_lines=100,
            header=['A', 'B'], expected_columns=2,
            column_profiles=[p1, p2],
            column_count_distribution={2: 100},
        )
        suggestions = generate_suggestions(det)
        high = [s for s in suggestions if s.priority == SuggestionPriority.HIGH]
        assert len(high) == 0


# ── Tests _detect_sensitive_patterns ──

class TestDetectSensitivePatterns:
    def test_detects_emails(self):
        result = _detect_sensitive_patterns(
            ['alice@example.com', 'bob@test.fr', 'charlie@mail.org'], 'contact')
        assert result == 'email'

    def test_detects_emails_by_name(self):
        result = _detect_sensitive_patterns(['some_value'], 'email_client')
        assert result == 'email'

    def test_detects_phones(self):
        result = _detect_sensitive_patterns(
            ['+33 6 12 34 56 78', '06 12 34 56 78', '01.23.45.67.89'], 'contact')
        assert result == 'telephone'

    def test_detects_phones_by_name(self):
        result = _detect_sensitive_patterns(['abc'], 'telephone')
        assert result == 'telephone'

    def test_detects_iban_by_name(self):
        result = _detect_sensitive_patterns(['val'], 'iban_code')
        assert result == 'IBAN'

    def test_no_sensitive(self):
        result = _detect_sensitive_patterns(['hello', 'world', '123'], 'data')
        assert result is None


# ── Tests column name suggestions ──

class TestColumnNameSuggestions:
    def test_postal_code(self):
        p = ColumnProfile(index=0)
        p.dominant_type = CellType.INTEGER
        p.empty_ratio = 0.0
        p.type_distribution = {CellType.INTEGER: 10}
        p.sample_values = ['75001', '69003']

        det = DetectionResult(
            filepath=Path('/tmp/t.csv'), encoding='utf-8', separator=';',
            separator_confidence=1.0, total_lines=10,
            header=['Code_Postal'], expected_columns=1,
            column_profiles=[p],
            column_count_distribution={1: 10},
        )
        suggestions = generate_suggestions(det)
        postal = [s for s in suggestions if s.action == 'validate_regex']
        assert len(postal) > 0

    def test_id_column(self):
        p = ColumnProfile(index=0)
        p.dominant_type = CellType.INTEGER
        p.empty_ratio = 0.0
        p.type_distribution = {CellType.INTEGER: 10}
        p.sample_values = ['1', '2']

        det = DetectionResult(
            filepath=Path('/tmp/t.csv'), encoding='utf-8', separator=';',
            separator_confidence=1.0, total_lines=10,
            header=['ID_Client'], expected_columns=1,
            column_profiles=[p],
            column_count_distribution={1: 10},
        )
        suggestions = generate_suggestions(det)
        id_sugs = [s for s in suggestions if s.action == 'check_unique']
        assert len(id_sugs) > 0


# ── Tests format_suggestions_text ──

class TestFormatSuggestionsText:
    def test_empty_list(self):
        result = format_suggestions_text([])
        assert 'Aucune suggestion' in result

    def test_with_suggestions(self):
        suggestions = [
            Suggestion(
                category=SuggestionCategory.SECURITY,
                priority=SuggestionPriority.HIGH,
                column_index=1,
                column_name='Email',
                message='Donnees sensibles detectees',
                detail='Anonymiser les emails',
                action='anonymize',
                icon='\U0001f512',
            ),
            Suggestion(
                category=SuggestionCategory.MISSING,
                priority=SuggestionPriority.MEDIUM,
                column_index=3,
                column_name='Date',
                message='85% de vides',
                detail='Verifier la colonne',
                action='check_required',
                icon='\u26a0',
            ),
        ]
        result = format_suggestions_text(suggestions)
        assert '2 suggestion(s)' in result
        assert 'HAUTE PRIORITE' in result
        assert 'sensibles' in result

    def test_priority_label(self):
        s = Suggestion(
            category=SuggestionCategory.FORMAT,
            priority=SuggestionPriority.LOW,
            column_index=0, column_name='A',
            message='msg', detail='det', action='act',
        )
        assert s.priority_label == 'Basse'
