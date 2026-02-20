"""Tests pour l'analyse et les nouveaux modules."""

import pytest
from pathlib import Path
from core.detector import run_detection
from core.analyzer import analyze_file

FIXTURES = Path(__file__).parent / 'fixtures'


class TestAnalyzeFile:
    def test_clean_no_issues(self):
        detection = run_detection(FIXTURES / 'clean.csv')
        analysis = analyze_file(detection)
        assert analysis.non_conforming_lines == 0

    def test_shifted_detects_issues(self):
        detection = run_detection(FIXTURES / 'shifted.csv')
        analysis = analyze_file(detection)
        assert analysis.non_conforming_lines > 0
        assert len(analysis.shifts) > 0

    def test_shift_locations(self):
        detection = run_detection(FIXTURES / 'shifted.csv')
        analysis = analyze_file(detection)
        for shift in analysis.shifts:
            assert shift.shift_start_column >= 0
            assert shift.line_number > 0


class TestValidators:
    def test_required_rule(self):
        from utils.validators import ValidationRule, validate_cell
        from utils.patterns import CellType
        rule = ValidationRule(column_index=0, column_name='Test', required=True)
        error = validate_cell('', rule, 1)
        assert error is not None
        assert error.rule_violated == 'required'

    def test_numeric_range(self):
        from utils.validators import ValidationRule, validate_cell
        rule = ValidationRule(column_index=0, column_name='Prix', min_value=0, max_value=100)
        assert validate_cell('50', rule, 1) is None
        error = validate_cell('150', rule, 1)
        assert error is not None
        assert error.rule_violated == 'max_value'

    def test_regex_pattern(self):
        from utils.validators import ValidationRule, validate_cell
        rule = ValidationRule(column_index=0, column_name='Code', regex_pattern=r'^[A-Z]{3}\d{4}$')
        assert validate_cell('ABC1234', rule, 1) is None
        error = validate_cell('invalid', rule, 1)
        assert error is not None


class TestHistory:
    def test_record_and_undo(self):
        from utils.history import CorrectionHistory
        history = CorrectionHistory()
        history.record('test', 'Test op', {1: (['a'], ['b'])})
        assert history.can_undo
        entry = history.undo()
        assert entry is not None
        assert not history.can_undo

    def test_redo(self):
        from utils.history import CorrectionHistory
        history = CorrectionHistory()
        history.record('test', 'Test', {1: (['a'], ['b'])})
        history.undo()
        assert history.can_redo
        entry = history.redo()
        assert entry is not None

    def test_clear(self):
        from utils.history import CorrectionHistory
        history = CorrectionHistory()
        history.record('test', 'Test', {1: (['a'], ['b'])})
        history.clear()
        assert not history.can_undo


class TestChunkedReader:
    def test_parse_csv_line_quoted(self):
        from utils.chunked_reader import parse_csv_line
        line = '"hello;world";test;123'
        result = parse_csv_line(line, ';')
        assert result == ['hello;world', 'test', '123']

    def test_parse_csv_line_simple(self):
        from utils.chunked_reader import parse_csv_line
        line = 'a;b;c'
        result = parse_csv_line(line, ';')
        assert result == ['a', 'b', 'c']

    def test_detect_encoding(self):
        from utils.chunked_reader import detect_encoding
        enc = detect_encoding(FIXTURES / 'clean.csv')
        assert enc is not None


class TestStatistics:
    def test_compute_stats(self):
        from core.statistics import compute_statistics
        detection = run_detection(FIXTURES / 'clean.csv')
        stats = compute_statistics(detection)
        assert stats.total_rows > 0
        assert stats.total_columns == 5
        assert len(stats.column_stats) == 5

    def test_stats_numeric_columns(self):
        from core.statistics import compute_statistics
        detection = run_detection(FIXTURES / 'clean.csv')
        stats = compute_statistics(detection)
        # Column 3 (Montant) should have numeric stats
        montant_stats = stats.column_stats[3]
        assert montant_stats.numeric_min is not None or montant_stats.unique_count > 0
