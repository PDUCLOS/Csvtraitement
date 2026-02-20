"""Tests pour core/reporter.py — generateur de rapport HTML."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from collections import Counter

from core.reporter import (
    generate_html_report, export_html_report,
    _grade_color, _score_bar, _escape,
)
from core.detector import DetectionResult, ColumnProfile
from core.quality import QualityReport, ColumnQuality
from core.fixer import FixResult, CellCorrection
from core.statistics import FileStats, ColumnStats
from utils.patterns import CellType


# ── Fixtures ──

def _make_detection(tmp_path=None) -> DetectionResult:
    """Cree un DetectionResult de test."""
    filepath = tmp_path / 'test.csv' if tmp_path else Path('/tmp/test.csv')
    if tmp_path:
        filepath.write_text('ID;Nom;Age\n1;Alice;30\n2;Bob;25\n')

    p1 = ColumnProfile(index=0)
    p1.dominant_type = CellType.INTEGER
    p1.empty_ratio = 0.01
    p1.type_distribution = {CellType.INTEGER: 99}

    p2 = ColumnProfile(index=1)
    p2.dominant_type = CellType.TEXT_SHORT
    p2.empty_ratio = 0.0
    p2.type_distribution = {CellType.TEXT_SHORT: 100}

    p3 = ColumnProfile(index=2)
    p3.dominant_type = CellType.INTEGER
    p3.empty_ratio = 0.05
    p3.type_distribution = {CellType.INTEGER: 95}

    det = DetectionResult(
        filepath=filepath, encoding='utf-8', separator=';',
        separator_confidence=1.0, total_lines=100,
        header=['ID', 'Nom', 'Age'], expected_columns=3,
        column_profiles=[p1, p2, p3],
        column_count_distribution={3: 95, 2: 5}, decimal_format='FR',
    )
    return det


def _make_quality() -> QualityReport:
    """Cree un QualityReport de test."""
    cq1 = ColumnQuality(index=0, header='ID', overall_score=95)
    cq1.fill_score = 99
    cq1.type_consistency_score = 100
    cq1.outlier_score = 100
    cq1.format_homogeneity_score = 100
    cq1.uniqueness_score = 90

    cq2 = ColumnQuality(index=1, header='Nom', overall_score=85)
    cq2.fill_score = 100
    cq2.type_consistency_score = 100
    cq2.outlier_score = 90
    cq2.format_homogeneity_score = 80
    cq2.uniqueness_score = 70
    cq2.issues = ['Formats mixtes']

    return QualityReport(
        overall_score=90,
        overall_grade='A',
        column_scores=[cq1, cq2],
        duplicate_rate=1.5,
        empty_row_rate=0.5,
        total_issues=1,
        summary=['1 probleme detecte'],
    )


def _make_fix_result() -> FixResult:
    """Cree un FixResult de test."""
    fr = FixResult(corrected_lines={}, corrections=[])
    fr.total_corrections = 15
    fr.date_fixes = 5
    fr.decimal_fixes = 8
    fr.realignment_fixes = 2
    fr.corrections = [
        CellCorrection(line_number=10, column_index=2,
                       original_value='00.00.0000', corrected_value='',
                       correction_type='date_invalid'),
        CellCorrection(line_number=20, column_index=3,
                       original_value='1.234,56', corrected_value='1234.56',
                       correction_type='decimal_normalize'),
    ]
    return fr


# ── Tests helpers ──

class TestHelpers:
    def test_grade_color_a(self):
        assert _grade_color('A') == '#a6e3a1'

    def test_grade_color_f(self):
        assert _grade_color('F') == '#f38ba8'

    def test_grade_color_unknown(self):
        assert _grade_color('X') == '#cdd6f4'

    def test_score_bar(self):
        bar = _score_bar(50)
        assert '\u2588' in bar
        assert '\u2591' in bar
        assert len(bar) == 20

    def test_score_bar_full(self):
        bar = _score_bar(100)
        assert bar == '\u2588' * 20

    def test_score_bar_empty(self):
        bar = _score_bar(0)
        assert bar == '\u2591' * 20

    def test_escape(self):
        assert _escape('<script>') == '&lt;script&gt;'
        assert _escape('Normal') == 'Normal'


# ── Tests generate_html_report ──

class TestGenerateHtmlReport:
    def test_basic_detection_only(self, tmp_path):
        det = _make_detection(tmp_path)
        html = generate_html_report(det)
        assert '<!DOCTYPE html>' in html
        assert 'test.csv' in html
        assert 'utf-8' in html
        assert '100' in html  # total_lines

    def test_with_quality(self, tmp_path):
        det = _make_detection(tmp_path)
        quality = _make_quality()
        html = generate_html_report(det, quality=quality)
        assert 'Score de qualite global' in html
        assert '90' in html
        assert 'A' in html
        assert 'Qualite par colonne' in html

    def test_with_fix_result(self, tmp_path):
        det = _make_detection(tmp_path)
        fix = _make_fix_result()
        html = generate_html_report(det, fix_result=fix)
        assert 'Corrections appliquees' in html
        assert '15' in html  # total_corrections
        assert '00.00.0000' in html
        assert '1234.56' in html

    def test_with_all(self, tmp_path):
        det = _make_detection(tmp_path)
        quality = _make_quality()
        fix = _make_fix_result()
        html = generate_html_report(det, quality=quality, fix_result=fix)
        assert 'Score de qualite global' in html
        assert 'Corrections appliquees' in html
        assert 'Profil des colonnes' in html
        assert 'Distribution' in html

    def test_column_profiles_displayed(self, tmp_path):
        det = _make_detection(tmp_path)
        html = generate_html_report(det)
        assert 'ID' in html
        assert 'Nom' in html
        assert 'Age' in html
        assert 'INTEGER' in html
        assert 'TEXT_SHORT' in html

    def test_distribution_displayed(self, tmp_path):
        det = _make_detection(tmp_path)
        html = generate_html_report(det)
        assert '3 cols' in html
        assert '2 cols' in html

    def test_custom_title(self, tmp_path):
        det = _make_detection(tmp_path)
        html = generate_html_report(det, title="Mon Rapport Custom")
        assert 'Mon Rapport Custom' in html

    def test_has_css(self, tmp_path):
        det = _make_detection(tmp_path)
        html = generate_html_report(det)
        assert '<style>' in html
        assert 'var(--bg)' in html

    def test_print_friendly(self, tmp_path):
        det = _make_detection(tmp_path)
        html = generate_html_report(det)
        assert '@media print' in html


# ── Tests export_html_report ──

class TestExportHtmlReport:
    def test_creates_file(self, tmp_path):
        det = _make_detection(tmp_path)
        out = tmp_path / 'report.html'
        result = export_html_report(det, out)
        assert result == out
        assert out.exists()
        content = out.read_text(encoding='utf-8')
        assert '<!DOCTYPE html>' in content

    def test_creates_dirs(self, tmp_path):
        det = _make_detection(tmp_path)
        out = tmp_path / 'sub' / 'dir' / 'report.html'
        export_html_report(det, out)
        assert out.exists()

    def test_with_quality(self, tmp_path):
        det = _make_detection(tmp_path)
        quality = _make_quality()
        out = tmp_path / 'report.html'
        export_html_report(det, out, quality=quality)
        content = out.read_text(encoding='utf-8')
        assert 'Score de qualite' in content
