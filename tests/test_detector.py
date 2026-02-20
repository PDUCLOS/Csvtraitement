"""Tests pour le module de détection."""

import pytest
from pathlib import Path
from core.detector import detect_separator, detect_column_count, profile_columns, run_detection
from utils.patterns import CellType

FIXTURES = Path(__file__).parent / 'fixtures'


class TestDetectSeparator:
    def test_semicolon(self):
        sep, _ = detect_separator(FIXTURES / 'clean.csv', 'utf-8')
        assert sep == ';'

    def test_shifted(self):
        sep, _ = detect_separator(FIXTURES / 'shifted.csv', 'utf-8')
        assert sep == ';'

    def test_mixed(self):
        sep, _ = detect_separator(FIXTURES / 'mixed_formats.csv', 'utf-8')
        assert sep == ';'


class TestDetectColumnCount:
    def test_clean(self):
        expected, dist = detect_column_count(FIXTURES / 'clean.csv', ';', 'utf-8')
        assert expected == 5

    def test_shifted(self):
        expected, dist = detect_column_count(FIXTURES / 'shifted.csv', ';', 'utf-8')
        assert expected == 5
        assert 6 in dist


class TestProfileColumns:
    def test_clean_profiles(self):
        lines = [
            "Dupont;Jean;15.03.1985;1.234,56;Lyon",
            "Martin;Marie;22.11.1990;987,50;Paris",
            "Bernard;Pierre;01.06.1978;45.678,90;Marseille",
        ]
        profiles = profile_columns(lines, ';', 5)
        assert len(profiles) == 5
        assert profiles[0].dominant_type == CellType.TEXT_SHORT
        assert profiles[2].dominant_type == CellType.DATE
        assert profiles[3].dominant_type == CellType.DECIMAL

    def test_decimal_format(self):
        lines = [
            "1;test;01.01.2020;1.234,56;Lyon",
            "2;test;02.02.2020;987,50;Paris",
        ]
        profiles = profile_columns(lines, ';', 5)
        assert profiles[3].decimal_format == 'FR'


class TestRunDetection:
    def test_clean(self):
        result = run_detection(FIXTURES / 'clean.csv')
        assert result.separator == ';'
        assert result.expected_columns == 5

    def test_shifted(self):
        result = run_detection(FIXTURES / 'shifted.csv')
        assert result.expected_columns == 5
        assert not result.regular_column_count
