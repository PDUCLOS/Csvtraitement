"""Tests complets pour les nouveaux modules : recipe, reporter, suggestions."""

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

from core.recipe import (
    Recipe, RecipeStep, StepType,
    save_recipe, load_recipe, export_recipe,
    record_load, record_fix, record_dedup, record_anonymize,
    record_column_rename, record_column_delete, record_regex_replace,
    record_normalize_case, record_strip_accents,
    export_to_power_query_m, export_to_python, export_to_sql, export_to_dax,
    _safe_col_name, _encoding_to_m, EXPORT_LANGUAGES,
)
from core.reporter import (
    generate_html_report, export_html_report,
    _grade_color, _score_bar, _escape,
)
from core.suggestions import (
    Suggestion, SuggestionPriority, SuggestionCategory,
    generate_suggestions, format_suggestions_text,
    _detect_sensitive_patterns, _suggest_by_column_name,
)
from core.detector import DetectionResult, ColumnProfile
from core.fixer import FixResult, CellCorrection
from core.quality import QualityReport, ColumnQuality
from core.statistics import FileStats, ColumnStats
from utils.patterns import CellType


# ═══════════════════════════════════════════════════════════
# HELPERS - Construction de mock objects
# ═══════════════════════════════════════════════════════════

def _make_detection(
    header=None, n_cols=None, total_lines=100, sep=';', encoding='utf-8',
    conformity_ratio_value=1.0, profiles=None, filepath=None,
    column_count_distribution=None,
) -> DetectionResult:
    """Cree un DetectionResult minimal pour les tests."""
    if header is None:
        header = ['ID', 'Nom', 'Montant']
    if n_cols is None:
        n_cols = len(header)
    if filepath is None:
        filepath = Path('/tmp/test_data.csv')
    if profiles is None:
        profiles = [
            ColumnProfile(index=i, dominant_type=CellType.TEXT_SHORT,
                          type_distribution={CellType.TEXT_SHORT: 90, CellType.EMPTY: 10},
                          empty_ratio=0.1, sample_values=[])
            for i in range(len(header))
        ]
    if column_count_distribution is None:
        conform = int(total_lines * conformity_ratio_value)
        non_conform = total_lines - conform
        column_count_distribution = {n_cols: conform}
        if non_conform > 0:
            column_count_distribution[n_cols + 1] = non_conform

    return DetectionResult(
        filepath=filepath,
        encoding=encoding,
        separator=sep,
        separator_confidence=1.0,
        total_lines=total_lines,
        header=header,
        expected_columns=len(header),
        column_profiles=profiles,
        column_count_distribution=column_count_distribution,
        decimal_format='FR',
    )


def _make_fix_result(date_fixes=5, decimal_fixes=3, realignment_fixes=2) -> FixResult:
    """Cree un FixResult minimal."""
    corrections = []
    for i in range(date_fixes):
        corrections.append(CellCorrection(
            line_number=i + 2, column_index=2,
            original_value='00.00.0000', corrected_value='',
            correction_type='date_invalid'))
    return FixResult(
        corrected_lines={},
        corrections=corrections,
        total_corrections=date_fixes + decimal_fixes + realignment_fixes,
        date_fixes=date_fixes,
        decimal_fixes=decimal_fixes,
        realignment_fixes=realignment_fixes,
    )


def _make_quality(overall=75.0, grade='B', columns=None, dup_rate=2.0) -> QualityReport:
    """Cree un QualityReport minimal."""
    if columns is None:
        columns = [
            ColumnQuality(index=0, header='ID', overall_score=95, fill_score=100,
                          type_consistency_score=100, outlier_score=100,
                          format_homogeneity_score=100, uniqueness_score=80),
            ColumnQuality(index=1, header='Nom', overall_score=80, fill_score=90,
                          type_consistency_score=95, outlier_score=100,
                          format_homogeneity_score=100, uniqueness_score=50),
            ColumnQuality(index=2, header='Montant', overall_score=60, fill_score=85,
                          type_consistency_score=70, outlier_score=50,
                          format_homogeneity_score=60, uniqueness_score=70,
                          issues=['Types mixtes', '5 outliers']),
        ]
    return QualityReport(
        overall_score=overall,
        overall_grade=grade,
        column_scores=columns,
        duplicate_rate=dup_rate,
        empty_row_rate=1.5,
        total_issues=2,
        summary=['2% doublons'],
    )


def _make_file_stats(outliers_count=5) -> FileStats:
    """Cree un FileStats minimal."""
    return FileStats(
        total_rows=100,
        total_columns=3,
        column_stats=[
            ColumnStats(index=0, header='ID', cell_type=CellType.INTEGER,
                        total_rows=100, non_empty=100, outliers_count=0),
            ColumnStats(index=1, header='Nom', cell_type=CellType.TEXT_SHORT,
                        total_rows=100, non_empty=95, outliers_count=0),
            ColumnStats(index=2, header='Montant', cell_type=CellType.DECIMAL,
                        total_rows=100, non_empty=85, outliers_count=outliers_count,
                        numeric_min=0.5, numeric_max=99999.0, numeric_mean=1234.56),
        ],
        duplicate_rows=3,
        empty_rows=2,
        overall_fill_rate=93.3,
    )


def _make_recipe(with_steps=True) -> Recipe:
    """Cree une recette de test."""
    recipe = Recipe(name='test_recipe', created_at='2025-01-15T10:30:00')
    recipe.header = ['ID', 'Nom', 'Email', 'Montant']
    recipe.separator = ';'
    recipe.encoding = 'utf-8'
    recipe.source_file = 'data.csv'
    recipe.total_rows_input = 1000

    if with_steps:
        record_load(recipe, 'data.csv', 'utf-8', ';', ['ID', 'Nom', 'Email', 'Montant'], 1000)
        recipe.add_step(RecipeStep(
            step_type=StepType.FIX_DATES,
            description="Corriger les dates",
            rows_affected=50,
        ))
        recipe.add_step(RecipeStep(
            step_type=StepType.FIX_DECIMALS,
            description="Normaliser les decimales",
            rows_affected=30,
        ))
        recipe.add_step(RecipeStep(
            step_type=StepType.DEDUP_EXACT,
            description="Supprimer les doublons exacts",
            rows_affected=10,
        ))

    return recipe


# ═══════════════════════════════════════════════════════════
# TESTS : core/recipe.py
# ═══════════════════════════════════════════════════════════

class TestRecipeStep:
    """Tests pour RecipeStep."""

    def test_create_step(self):
        step = RecipeStep(
            step_type=StepType.FIX_DATES,
            description="Fix dates",
            rows_affected=42,
        )
        assert step.step_type == StepType.FIX_DATES
        assert step.description == "Fix dates"
        assert step.rows_affected == 42
        assert step.params == {}
        assert step.columns == []
        assert step.column_indices == []

    def test_to_dict(self):
        step = RecipeStep(
            step_type=StepType.REGEX_REPLACE,
            description="Replace X",
            params={'pattern': r'\d+', 'replacement': '***'},
            columns=['Email'],
            column_indices=[2],
            rows_affected=100,
        )
        d = step.to_dict()
        assert d['step_type'] == 'REGEX_REPLACE'
        assert d['params']['pattern'] == r'\d+'
        assert d['columns'] == ['Email']
        assert d['rows_affected'] == 100

    def test_from_dict(self):
        data = {
            'step_type': 'FIX_DECIMALS',
            'description': 'Normaliser',
            'params': {'format_input': 'FR'},
            'columns': [],
            'column_indices': [],
            'rows_affected': 25,
        }
        step = RecipeStep.from_dict(data)
        assert step.step_type == StepType.FIX_DECIMALS
        assert step.params['format_input'] == 'FR'

    def test_roundtrip_to_from_dict(self):
        original = RecipeStep(
            step_type=StepType.ANONYMIZE,
            description="Anonymiser emails",
            params={'rules': [{'column_index': 2, 'type': 'EMAIL', 'mask_mode': 'PARTIAL'}]},
            columns=['Email'],
            column_indices=[2],
            rows_affected=500,
        )
        restored = RecipeStep.from_dict(original.to_dict())
        assert restored.step_type == original.step_type
        assert restored.description == original.description
        assert restored.params == original.params
        assert restored.columns == original.columns
        assert restored.rows_affected == original.rows_affected

    def test_from_dict_missing_fields(self):
        data = {'step_type': 'LOAD_FILE', 'description': 'Load'}
        step = RecipeStep.from_dict(data)
        assert step.params == {}
        assert step.columns == []
        assert step.rows_affected == 0


class TestRecipe:
    """Tests pour Recipe."""

    def test_create_recipe(self):
        recipe = Recipe(name='test')
        assert recipe.name == 'test'
        assert recipe.steps == []
        assert recipe.header == []
        assert recipe.total_rows_input == 0

    def test_add_step(self):
        recipe = Recipe(name='test')
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description="Load"))
        recipe.add_step(RecipeStep(step_type=StepType.FIX_DATES, description="Fix"))
        assert len(recipe.steps) == 2

    def test_to_dict_and_from_dict(self):
        original = _make_recipe(with_steps=True)
        d = original.to_dict()
        restored = Recipe.from_dict(d)
        assert restored.name == original.name
        assert restored.separator == original.separator
        assert restored.encoding == original.encoding
        assert restored.header == original.header
        assert len(restored.steps) == len(original.steps)
        for o, r in zip(original.steps, restored.steps):
            assert o.step_type == r.step_type
            assert o.description == r.description

    def test_save_and_load(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'test_recipe.json'
        save_recipe(recipe, filepath)
        assert filepath.exists()

        loaded = load_recipe(filepath)
        assert loaded.name == recipe.name
        assert len(loaded.steps) == len(recipe.steps)
        assert loaded.header == recipe.header

    def test_save_creates_parent_dirs(self, tmp_path):
        recipe = Recipe(name='deep')
        filepath = tmp_path / 'sub' / 'dir' / 'recipe.json'
        save_recipe(recipe, filepath)
        assert filepath.exists()

    def test_save_json_valid(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'valid.json'
        save_recipe(recipe, filepath)
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data['name'] == 'test_recipe'
        assert isinstance(data['steps'], list)


class TestRecordFunctions:
    """Tests pour les fonctions record_*."""

    def test_record_load(self):
        recipe = Recipe(name='test')
        record_load(recipe, '/tmp/data.csv', 'utf-8', ';', ['A', 'B', 'C'], 500)
        assert recipe.source_file == '/tmp/data.csv'
        assert recipe.encoding == 'utf-8'
        assert recipe.header == ['A', 'B', 'C']
        assert recipe.total_rows_input == 500
        assert len(recipe.steps) == 1
        assert recipe.steps[0].step_type == StepType.LOAD_FILE

    def test_record_fix_all_types(self):
        recipe = Recipe(name='test')
        recipe.header = ['A', 'B']
        fix = _make_fix_result(date_fixes=5, decimal_fixes=3, realignment_fixes=2)
        record_fix(recipe, fix)
        step_types = [s.step_type for s in recipe.steps]
        assert StepType.FIX_DATES in step_types
        assert StepType.FIX_DECIMALS in step_types
        assert StepType.FIX_SHIFTS in step_types

    def test_record_fix_dates_only(self):
        recipe = Recipe(name='test')
        fix = _make_fix_result(date_fixes=10, decimal_fixes=0, realignment_fixes=0)
        record_fix(recipe, fix)
        assert len(recipe.steps) == 1
        assert recipe.steps[0].step_type == StepType.FIX_DATES
        assert recipe.steps[0].rows_affected == 10

    def test_record_fix_no_fixes(self):
        recipe = Recipe(name='test')
        fix = _make_fix_result(date_fixes=0, decimal_fixes=0, realignment_fixes=0)
        record_fix(recipe, fix)
        assert len(recipe.steps) == 0

    def test_record_dedup_exact(self):
        recipe = Recipe(name='test')

        class FakeDedup:
            total_duplicate_rows = 15
        record_dedup(recipe, FakeDedup(), fuzzy=False)
        assert recipe.steps[0].step_type == StepType.DEDUP_EXACT
        assert recipe.steps[0].rows_affected == 15

    def test_record_dedup_fuzzy(self):
        recipe = Recipe(name='test')

        class FakeDedup:
            total_duplicate_rows = 8
        record_dedup(recipe, FakeDedup(), fuzzy=True)
        assert recipe.steps[0].step_type == StepType.DEDUP_FUZZY
        assert recipe.steps[0].params['threshold'] == 0.85

    def test_record_anonymize(self):
        recipe = Recipe(name='test')
        recipe.header = ['ID', 'Email', 'Tel']

        class FakeRule:
            column_index = 1
            column_name = 'Email'

            class sensitive_type:
                name = 'EMAIL'

            class mask_mode:
                name = 'PARTIAL'
        record_anonymize(recipe, [FakeRule()], rows_affected=100)
        assert recipe.steps[0].step_type == StepType.ANONYMIZE
        assert recipe.steps[0].columns == ['Email']
        assert recipe.steps[0].rows_affected == 100

    def test_record_column_rename(self):
        recipe = Recipe(name='test')
        record_column_rename(recipe, {0: 'Identifiant', 2: 'Prix'}, ['ID', 'Nom', 'Montant'])
        assert recipe.steps[0].step_type == StepType.RENAME_COLUMN
        assert recipe.steps[0].params['mapping'] == {'ID': 'Identifiant', 'Montant': 'Prix'}

    def test_record_column_delete(self):
        recipe = Recipe(name='test')
        record_column_delete(recipe, [1, 3], ['ID', 'Tmp', 'Nom', 'Unused'])
        assert recipe.steps[0].columns == ['Tmp', 'Unused']
        assert recipe.steps[0].column_indices == [1, 3]

    def test_record_regex_replace(self):
        recipe = Recipe(name='test')
        record_regex_replace(recipe, 'Email', 2, r'@.*', '@masked.com', 50)
        assert recipe.steps[0].step_type == StepType.REGEX_REPLACE
        assert recipe.steps[0].params['pattern'] == r'@.*'

    def test_record_normalize_case(self):
        recipe = Recipe(name='test')
        record_normalize_case(recipe, ['Nom', 'Ville'], [1, 3], mode='upper', rows_affected=200)
        assert recipe.steps[0].step_type == StepType.NORMALIZE_CASE
        assert recipe.steps[0].params['mode'] == 'upper'

    def test_record_strip_accents(self):
        recipe = Recipe(name='test')
        record_strip_accents(recipe, ['Nom'], [1], rows_affected=150)
        assert recipe.steps[0].step_type == StepType.STRIP_ACCENTS
        assert recipe.steps[0].columns == ['Nom']


class TestSafeColName:
    """Tests pour _safe_col_name."""

    def test_simple_name(self):
        assert _safe_col_name('Name') == 'Name'

    def test_with_spaces_and_special(self):
        assert _safe_col_name('Montant HT (EUR)') == 'Montant_HT__EUR'

    def test_starts_with_digit(self):
        assert _safe_col_name('1er_trimestre') == '_1er_trimestre'

    def test_empty_string(self):
        assert _safe_col_name('') == '_col'

    def test_all_special_chars(self):
        assert _safe_col_name('!!!') == '_col'


class TestEncodingToM:
    """Tests pour _encoding_to_m."""

    def test_utf8(self):
        assert _encoding_to_m('utf-8') == '65001'

    def test_latin1(self):
        assert _encoding_to_m('latin-1') == '28591'

    def test_cp1252(self):
        assert _encoding_to_m('cp1252') == '1252'

    def test_unknown_defaults_utf8(self):
        assert _encoding_to_m('iso-8859-42') == '65001'

    def test_case_insensitive(self):
        assert _encoding_to_m('UTF-8') == '65001'


class TestExportPowerQueryM:
    """Tests pour export_to_power_query_m."""

    def test_basic_export(self):
        recipe = _make_recipe()
        m_code = export_to_power_query_m(recipe)
        assert 'let' in m_code
        assert 'in' in m_code
        assert 'Result' in m_code
        assert 'Csv.Document' in m_code
        assert 'PromotedHeaders' in m_code

    def test_contains_step_comments(self):
        recipe = _make_recipe()
        m_code = export_to_power_query_m(recipe)
        assert 'Corriger les dates' in m_code
        assert 'Normaliser les decimales' in m_code

    def test_dedup_step(self):
        recipe = _make_recipe()
        m_code = export_to_power_query_m(recipe)
        assert 'Table.Distinct' in m_code

    def test_encoding_in_output(self):
        recipe = _make_recipe()
        recipe.encoding = 'cp1252'
        m_code = export_to_power_query_m(recipe)
        assert '1252' in m_code

    def test_rename_column_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A', 'B']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.RENAME_COLUMN,
            description='Renommer',
            params={'mapping': {'A': 'Alpha', 'B': 'Beta'}},
        ))
        m_code = export_to_power_query_m(recipe)
        assert 'Table.RenameColumns' in m_code
        assert '"Alpha"' in m_code

    def test_delete_column_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A', 'B', 'C']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.DELETE_COLUMN,
            description='Supprimer B',
            columns=['B'],
        ))
        m_code = export_to_power_query_m(recipe)
        assert 'Table.RemoveColumns' in m_code
        assert '"B"' in m_code

    def test_anonymize_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['ID', 'Email']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.ANONYMIZE,
            description='Anonymiser',
            params={'rules': [{'column_index': 1, 'mask_mode': 'PARTIAL'}]},
        ))
        m_code = export_to_power_query_m(recipe)
        assert 'Text.Start' in m_code
        assert 'Text.Repeat("*"' in m_code

    def test_normalize_case_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['Nom']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.NORMALIZE_CASE,
            description='Minuscule',
            params={'mode': 'lower'},
            columns=['Nom'],
        ))
        m_code = export_to_power_query_m(recipe)
        assert 'Text.Lower' in m_code

    def test_empty_recipe(self):
        recipe = Recipe(name='empty', created_at='2025-01-01T00:00:00')
        recipe.header = []
        m_code = export_to_power_query_m(recipe)
        assert 'let' in m_code
        assert 'in' in m_code

    def test_fix_shifts_comment(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(step_type=StepType.FIX_SHIFTS, description='Realigner'))
        m_code = export_to_power_query_m(recipe)
        assert 'Realignement' in m_code
        assert "pas d'equivalent direct" in m_code


class TestExportPython:
    """Tests pour export_to_python."""

    def test_basic_export(self):
        recipe = _make_recipe()
        py_code = export_to_python(recipe)
        assert 'import pandas as pd' in py_code
        assert 'def apply_recipe' in py_code
        assert 'pd.read_csv' in py_code
        assert 'return df' in py_code

    def test_fix_dates_step(self):
        recipe = _make_recipe()
        py_code = export_to_python(recipe)
        assert '00.00.0000' in py_code
        assert 'invalid_dates' in py_code

    def test_fix_decimals_step(self):
        recipe = _make_recipe()
        py_code = export_to_python(recipe)
        assert 'normalize_decimal_fr' in py_code

    def test_dedup_step(self):
        recipe = _make_recipe()
        py_code = export_to_python(recipe)
        assert 'drop_duplicates' in py_code

    def test_main_block(self):
        recipe = _make_recipe()
        py_code = export_to_python(recipe)
        assert 'if __name__' in py_code
        assert 'to_csv' in py_code

    def test_rename_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.RENAME_COLUMN,
            description='Rename',
            params={'mapping': {'A': 'Alpha'}},
        ))
        py_code = export_to_python(recipe)
        assert 'df.rename' in py_code

    def test_delete_column_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A', 'B']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.DELETE_COLUMN,
            description='Delete B',
            columns=['B'],
        ))
        py_code = export_to_python(recipe)
        assert 'df.drop' in py_code

    def test_strip_accents_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['Nom']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.STRIP_ACCENTS,
            description='Strip accents',
            columns=['Nom'],
        ))
        py_code = export_to_python(recipe)
        assert 'unicodedata' in py_code
        assert 'strip_accents' in py_code

    def test_clean_masked_step(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(step_type=StepType.CLEAN_MASKED, description='Clean'))
        py_code = export_to_python(recipe)
        assert 'masked_values' in py_code
        assert 'N/A' in py_code


class TestExportSQL:
    """Tests pour export_to_sql."""

    def test_basic_export(self):
        recipe = _make_recipe()
        sql = export_to_sql(recipe)
        assert 'UPDATE source_data' in sql
        assert 'SELECT * FROM source_data' in sql

    def test_fix_dates_sql(self):
        recipe = _make_recipe()
        sql = export_to_sql(recipe)
        assert '00.00.0000' in sql
        assert 'CASE WHEN' in sql
        assert 'NULL' in sql

    def test_dedup_sql(self):
        recipe = _make_recipe()
        sql = export_to_sql(recipe)
        assert 'DELETE FROM' in sql
        assert 'GROUP BY' in sql

    def test_rename_column_sql(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.RENAME_COLUMN,
            description='Rename',
            params={'mapping': {'A': 'Alpha'}},
        ))
        sql = export_to_sql(recipe)
        assert 'ALTER TABLE' in sql
        assert 'RENAME COLUMN' in sql

    def test_delete_column_sql(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A', 'B']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.DELETE_COLUMN,
            description='Drop B',
            columns=['B'],
        ))
        sql = export_to_sql(recipe)
        assert 'DROP COLUMN' in sql

    def test_regex_replace_sql(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.REGEX_REPLACE,
            description='Regex',
            params={'pattern': r'\d+', 'replacement': 'X'},
            columns=['A'],
        ))
        sql = export_to_sql(recipe)
        assert 'REGEXP_REPLACE' in sql

    def test_normalize_case_sql(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['A']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.NORMALIZE_CASE,
            description='Lower',
            params={'mode': 'lower'},
            columns=['A'],
        ))
        sql = export_to_sql(recipe)
        assert 'LOWER' in sql


class TestExportDAX:
    """Tests pour export_to_dax."""

    def test_basic_export(self):
        recipe = _make_recipe()
        dax = export_to_dax(recipe)
        assert 'DAX' in dax
        assert 'CSV Traitement' in dax

    def test_fix_dates_dax(self):
        recipe = _make_recipe()
        dax = export_to_dax(recipe)
        assert 'BLANK()' in dax
        assert '00.00.0000' in dax

    def test_fix_decimals_dax(self):
        recipe = _make_recipe()
        dax = export_to_dax(recipe)
        assert 'SUBSTITUTE' in dax
        assert 'VALUE' in dax

    def test_normalize_case_dax(self):
        recipe = Recipe(name='test', created_at='2025-01-01T00:00:00')
        recipe.header = ['Nom']
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description='Load'))
        recipe.add_step(RecipeStep(
            step_type=StepType.NORMALIZE_CASE,
            description='Upper',
            params={'mode': 'upper'},
            columns=['Nom'],
        ))
        dax = export_to_dax(recipe)
        assert 'UPPER' in dax


class TestExportRecipe:
    """Tests pour export_recipe (dispatch)."""

    def test_export_json(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'recipe.json'
        result = export_recipe(recipe, 'json', filepath)
        assert result.exists()
        data = json.loads(result.read_text(encoding='utf-8'))
        assert data['name'] == 'test_recipe'

    def test_export_power_query_m(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'recipe.pq'
        result = export_recipe(recipe, 'power_query_m', filepath)
        assert result.exists()
        content = result.read_text(encoding='utf-8')
        assert 'let' in content

    def test_export_python(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'recipe.py'
        result = export_recipe(recipe, 'python', filepath)
        assert result.exists()
        content = result.read_text(encoding='utf-8')
        assert 'import pandas' in content

    def test_export_sql(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'recipe.sql'
        result = export_recipe(recipe, 'sql', filepath)
        assert result.exists()

    def test_export_dax(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'recipe.dax'
        result = export_recipe(recipe, 'dax', filepath)
        assert result.exists()

    def test_export_unknown_language(self, tmp_path):
        recipe = _make_recipe()
        with pytest.raises(ValueError, match="Langage inconnu"):
            export_recipe(recipe, 'cobol', tmp_path / 'recipe.cob')

    def test_all_languages_listed(self):
        assert 'power_query_m' in EXPORT_LANGUAGES
        assert 'python' in EXPORT_LANGUAGES
        assert 'sql' in EXPORT_LANGUAGES
        assert 'dax' in EXPORT_LANGUAGES
        assert 'json' in EXPORT_LANGUAGES


# ═══════════════════════════════════════════════════════════
# TESTS : core/reporter.py
# ═══════════════════════════════════════════════════════════

class TestReporterHelpers:
    """Tests pour les helpers du reporter."""

    def test_grade_color_a(self):
        assert _grade_color('A') == '#a6e3a1'

    def test_grade_color_f(self):
        assert _grade_color('F') == '#f38ba8'

    def test_grade_color_unknown(self):
        assert _grade_color('X') == '#cdd6f4'

    def test_score_bar_full(self):
        bar = _score_bar(100)
        assert '\u2588' * 20 == bar

    def test_score_bar_empty(self):
        bar = _score_bar(0)
        assert '\u2591' * 20 == bar

    def test_score_bar_half(self):
        bar = _score_bar(50)
        assert len(bar) == 20
        assert bar.count('\u2588') == 10
        assert bar.count('\u2591') == 10

    def test_escape_html_entities(self):
        assert _escape('<script>alert("xss")</script>') == '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'

    def test_escape_none(self):
        assert _escape(None) == 'None'


class TestGenerateHtmlReport:
    """Tests pour generate_html_report."""

    def test_basic_report_without_quality(self):
        detection = _make_detection()
        html_content = generate_html_report(detection)
        assert '<!DOCTYPE html>' in html_content
        assert 'test_data.csv' in html_content
        assert 'utf-8' in html_content
        assert '100' in html_content  # total lines

    def test_report_with_quality(self):
        detection = _make_detection()
        quality = _make_quality(overall=85.0, grade='B')
        html_content = generate_html_report(detection, quality=quality)
        assert 'score-card' in html_content
        assert '85' in html_content
        assert 'B' in html_content
        assert '#94e2d5' in html_content  # grade B color

    def test_report_with_fix_result(self):
        detection = _make_detection()
        fix = _make_fix_result()
        html_content = generate_html_report(detection, fix_result=fix)
        assert 'Corrections appliquees' in html_content
        assert '00.00.0000' in html_content
        assert 'date_invalid' in html_content

    def test_report_with_stats(self):
        detection = _make_detection()
        stats = _make_file_stats()
        html_content = generate_html_report(detection, file_stats=stats)
        assert 'Statistiques' in html_content
        assert '93.3%' in html_content  # fill rate

    def test_report_with_everything(self):
        detection = _make_detection()
        quality = _make_quality()
        fix = _make_fix_result()
        stats = _make_file_stats()
        html_content = generate_html_report(detection, quality, fix, stats)
        # Check all sections present
        assert 'score-card' in html_content
        assert 'Corrections appliquees' in html_content
        assert 'Statistiques' in html_content
        assert 'Qualite par colonne' in html_content
        assert 'Profil des colonnes' in html_content
        assert 'Distribution du nombre de colonnes' in html_content
        assert 'footer' in html_content

    def test_html_escaping_in_report(self):
        detection = _make_detection(header=['<script>evil</script>', 'B', 'C'])
        html_content = generate_html_report(detection)
        assert '<script>' not in html_content
        assert '&lt;script&gt;' in html_content

    def test_custom_title(self):
        detection = _make_detection()
        html_content = generate_html_report(detection, title="Mon rapport special")
        assert 'Mon rapport special' in html_content

    def test_column_quality_table(self):
        detection = _make_detection()
        quality = _make_quality()
        html_content = generate_html_report(detection, quality=quality)
        assert 'Montant' in html_content
        assert 'Types mixtes' in html_content  # issue
        assert 'mini-bar' in html_content

    def test_distribution_section(self):
        detection = _make_detection(conformity_ratio_value=0.9)
        html_content = generate_html_report(detection)
        assert 'Distribution du nombre de colonnes' in html_content
        assert '(attendu)' in html_content


class TestExportHtmlReport:
    """Tests pour export_html_report."""

    def test_export_creates_file(self, tmp_path):
        detection = _make_detection()
        output = tmp_path / 'report.html'
        result = export_html_report(detection, output)
        assert result.exists()
        content = result.read_text(encoding='utf-8')
        assert '<!DOCTYPE html>' in content

    def test_export_with_quality(self, tmp_path):
        detection = _make_detection()
        quality = _make_quality()
        output = tmp_path / 'report.html'
        export_html_report(detection, output, quality=quality)
        content = output.read_text(encoding='utf-8')
        assert 'score-card' in content

    def test_export_creates_dirs(self, tmp_path):
        detection = _make_detection()
        output = tmp_path / 'sub' / 'dir' / 'report.html'
        export_html_report(detection, output)
        assert output.exists()

    def test_report_valid_html_structure(self, tmp_path):
        detection = _make_detection()
        quality = _make_quality()
        fix = _make_fix_result()
        output = tmp_path / 'report.html'
        export_html_report(detection, output, quality=quality, fix_result=fix)
        content = output.read_text(encoding='utf-8')
        # Check basic HTML structure
        assert content.count('<html') == 1
        assert content.count('</html>') == 1
        assert content.count('<head>') == 1
        assert content.count('</head>') == 1
        assert content.count('<body>') == 1
        assert content.count('</body>') == 1


# ═══════════════════════════════════════════════════════════
# TESTS : core/suggestions.py
# ═══════════════════════════════════════════════════════════

class TestSuggestion:
    """Tests pour la dataclass Suggestion."""

    def test_priority_label(self):
        s = Suggestion(
            category=SuggestionCategory.FORMAT,
            priority=SuggestionPriority.HIGH,
            column_index=0, column_name='A',
            message='test', detail='detail', action='fix',
        )
        assert s.priority_label == 'Haute'

    def test_priority_label_medium(self):
        s = Suggestion(
            category=SuggestionCategory.FORMAT,
            priority=SuggestionPriority.MEDIUM,
            column_index=0, column_name='A',
            message='test', detail='detail', action='fix',
        )
        assert s.priority_label == 'Moyenne'

    def test_priority_label_low(self):
        s = Suggestion(
            category=SuggestionCategory.FORMAT,
            priority=SuggestionPriority.LOW,
            column_index=0, column_name='A',
            message='test', detail='detail', action='fix',
        )
        assert s.priority_label == 'Basse'


class TestDetectSensitivePatterns:
    """Tests pour _detect_sensitive_patterns."""

    def test_detect_emails(self):
        samples = ['alice@example.com', 'bob@test.fr', 'carol@company.org']
        result = _detect_sensitive_patterns(samples, 'contact')
        assert result == 'email'

    def test_detect_emails_by_column_name(self):
        result = _detect_sensitive_patterns([], 'email_client')
        assert result == 'email'

    def test_detect_phone_by_column_name(self):
        result = _detect_sensitive_patterns([], 'telephone')
        assert result == 'telephone'

    def test_detect_iban_by_column_name(self):
        result = _detect_sensitive_patterns([], 'iban')
        assert result == 'IBAN'

    def test_detect_ssn_by_column_name(self):
        result = _detect_sensitive_patterns([], 'n_secu')
        assert result == 'n_secu'

    def test_no_detection_on_normal_data(self):
        samples = ['Paris', 'Lyon', 'Marseille', 'Bordeaux']
        result = _detect_sensitive_patterns(samples, 'ville')
        assert result is None

    def test_empty_values_ignored(self):
        samples = ['', '', '', 'alice@test.com', 'bob@test.com']
        result = _detect_sensitive_patterns(samples, 'data')
        assert result == 'email'

    def test_detect_phones_by_value(self):
        samples = ['+33 6 12 34 56 78', '06.12.34.56.78', '01-23-45-67-89']
        result = _detect_sensitive_patterns(samples, 'contact')
        assert result == 'telephone'


class TestSuggestByColumnName:
    """Tests pour _suggest_by_column_name."""

    def test_code_postal(self):
        profile = ColumnProfile(index=0, dominant_type=CellType.INTEGER)
        results = _suggest_by_column_name(0, 'Code_Postal', 'code_postal', profile)
        assert len(results) >= 1
        assert any('code postal' in s.message for s in results)

    def test_zip_code(self):
        profile = ColumnProfile(index=0, dominant_type=CellType.TEXT_SHORT)
        results = _suggest_by_column_name(0, 'zip', 'zip', profile)
        assert any('code postal' in s.message for s in results)

    def test_pays_column(self):
        profile = ColumnProfile(index=0, dominant_type=CellType.TEXT_SHORT)
        results = _suggest_by_column_name(0, 'pays', 'pays', profile)
        assert any('pays' in s.message for s in results)

    def test_montant_column(self):
        profile = ColumnProfile(index=0, dominant_type=CellType.DECIMAL)
        results = _suggest_by_column_name(0, 'Montant_HT', 'montant_ht', profile)
        assert any('montant' in s.message for s in results)

    def test_id_column(self):
        profile = ColumnProfile(index=0, dominant_type=CellType.INTEGER)
        results = _suggest_by_column_name(0, 'ID', 'id', profile)
        assert any('identifiant' in s.message for s in results)

    def test_unrecognized_column(self):
        profile = ColumnProfile(index=0, dominant_type=CellType.TEXT_SHORT)
        results = _suggest_by_column_name(0, 'XYZ_Data', 'xyz_data', profile)
        assert len(results) == 0


class TestGenerateSuggestions:
    """Tests pour generate_suggestions."""

    def test_empty_column_suggestion(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.TEXT_SHORT,
                          type_distribution={CellType.TEXT_SHORT: 10, CellType.EMPTY: 90},
                          empty_ratio=0.9, sample_values=[]),
        ]
        detection = _make_detection(header=['MaybeEmpty'], profiles=profiles)
        results = generate_suggestions(detection)
        assert any(s.category == SuggestionCategory.MISSING for s in results)
        assert any('90%' in s.message for s in results)

    def test_mixed_types_suggestion(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.INTEGER,
                          type_distribution={CellType.INTEGER: 50, CellType.TEXT_SHORT: 50},
                          empty_ratio=0.0, sample_values=[]),
        ]
        detection = _make_detection(header=['Mixed'], profiles=profiles)
        results = generate_suggestions(detection)
        assert any(s.category == SuggestionCategory.TYPE for s in results)

    def test_date_invalid_suggestion(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.DATE_INVALID,
                          type_distribution={CellType.DATE_INVALID: 100},
                          empty_ratio=0.0, sample_values=[]),
        ]
        detection = _make_detection(header=['BadDate'], profiles=profiles)
        results = generate_suggestions(detection)
        assert any(s.action == 'fix_dates' for s in results)

    def test_low_conformity_suggestion(self):
        detection = _make_detection(conformity_ratio_value=0.7)
        results = generate_suggestions(detection)
        assert any(s.action == 'fix_shifts' for s in results)

    def test_low_quality_suggestion(self):
        detection = _make_detection()
        quality = _make_quality(overall=40.0, grade='D')
        results = generate_suggestions(detection, quality=quality)
        assert any(s.action == 'full_cleanup' for s in results)

    def test_duplicate_suggestion(self):
        detection = _make_detection()
        quality = _make_quality(dup_rate=8.0)
        results = generate_suggestions(detection, quality=quality)
        assert any(s.action == 'dedup' for s in results)

    def test_outlier_suggestion(self):
        detection = _make_detection()
        stats = _make_file_stats(outliers_count=15)
        results = generate_suggestions(detection, file_stats=stats)
        assert any(s.action == 'review_outliers' for s in results)

    def test_sensitive_data_detection(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.TEXT_SHORT,
                          type_distribution={CellType.TEXT_SHORT: 100},
                          empty_ratio=0.0,
                          sample_values=['alice@example.com', 'bob@test.fr', 'carol@x.com']),
        ]
        detection = _make_detection(header=['Email'], profiles=profiles)
        results = generate_suggestions(detection)
        assert any(s.category == SuggestionCategory.SECURITY for s in results)

    def test_no_suggestions_on_clean_data(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.TEXT_SHORT,
                          type_distribution={CellType.TEXT_SHORT: 100},
                          empty_ratio=0.05, sample_values=['Paris', 'Lyon']),
        ]
        detection = _make_detection(
            header=['Ville'], profiles=profiles, conformity_ratio_value=1.0)
        results = generate_suggestions(detection)
        # Should have no high-priority suggestions
        high = [s for s in results if s.priority == SuggestionPriority.HIGH]
        assert len(high) == 0

    def test_suggestions_sorted_by_priority(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.DATE_INVALID,
                          type_distribution={CellType.DATE_INVALID: 100},
                          empty_ratio=0.9,
                          sample_values=['alice@test.com'] * 5),
        ]
        detection = _make_detection(
            header=['Data'], profiles=profiles, conformity_ratio_value=0.5)
        results = generate_suggestions(detection)
        # All HIGH suggestions should come before MEDIUM/LOW
        priorities = [s.priority for s in results]
        high_indices = [i for i, p in enumerate(priorities) if p == SuggestionPriority.HIGH]
        low_indices = [i for i, p in enumerate(priorities) if p == SuggestionPriority.LOW]
        if high_indices and low_indices:
            assert max(high_indices) < min(low_indices)

    def test_column_name_based_suggestions(self):
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.INTEGER,
                          type_distribution={CellType.INTEGER: 100},
                          empty_ratio=0.0, sample_values=[]),
            ColumnProfile(index=1, dominant_type=CellType.TEXT_SHORT,
                          type_distribution={CellType.TEXT_SHORT: 100},
                          empty_ratio=0.0, sample_values=[]),
        ]
        detection = _make_detection(header=['Code_Postal', 'pays'], profiles=profiles)
        results = generate_suggestions(detection)
        actions = [s.action for s in results]
        assert 'validate_regex' in actions  # postal code
        assert 'normalize_case' in actions  # country


class TestFormatSuggestionsText:
    """Tests pour format_suggestions_text."""

    def test_no_suggestions(self):
        result = format_suggestions_text([])
        assert result == "Aucune suggestion."

    def test_with_suggestions(self):
        suggestions = [
            Suggestion(
                category=SuggestionCategory.FORMAT,
                priority=SuggestionPriority.HIGH,
                column_index=0, column_name='Date',
                message='Dates invalides', detail='Corriger les dates',
                action='fix_dates', icon='\U0001f4c5',
            ),
            Suggestion(
                category=SuggestionCategory.MISSING,
                priority=SuggestionPriority.MEDIUM,
                column_index=1, column_name='Note',
                message='Colonne vide', detail='80% vides',
                action='check_required', icon='\u26a0',
            ),
        ]
        result = format_suggestions_text(suggestions)
        assert '2 suggestion(s)' in result
        assert 'HAUTE PRIORITE' in result
        assert 'PRIORITE MOYENNE' in result
        assert 'Dates invalides' in result
        assert 'Colonne vide' in result


# ═══════════════════════════════════════════════════════════
# TESTS D'INTEGRATION
# ═══════════════════════════════════════════════════════════

class TestIntegrationRecipeReporter:
    """Tests d'integration entre modules."""

    def test_full_pipeline_recipe_and_report(self, tmp_path):
        """Simule un pipeline complet : detection -> fix -> recipe -> export multi-langage."""
        # 1. Creer la recette
        recipe = Recipe(name='integration_test')
        record_load(recipe, 'data.csv', 'utf-8', ';', ['ID', 'Date', 'Montant', 'Email'], 500)

        fix = _make_fix_result(date_fixes=20, decimal_fixes=15, realignment_fixes=0)
        record_fix(recipe, fix)
        record_regex_replace(recipe, 'Email', 3, r'@.*', '@masked.com', 500)
        record_normalize_case(recipe, ['Email'], [3], mode='lower', rows_affected=500)

        # 2. Exporter dans tous les formats
        for lang in ['json', 'power_query_m', 'python', 'sql', 'dax']:
            ext = EXPORT_LANGUAGES[lang][1] if lang != 'json' else '.json'
            filepath = tmp_path / f'recipe{ext}'
            export_recipe(recipe, lang, filepath)
            assert filepath.exists()
            content = filepath.read_text(encoding='utf-8')
            assert len(content) > 50

    def test_full_pipeline_report(self, tmp_path):
        """Genere un rapport HTML complet avec toutes les sections."""
        detection = _make_detection(
            header=['ID', 'Nom', 'Date', 'Montant', 'Email'],
            profiles=[
                ColumnProfile(index=i, dominant_type=CellType.TEXT_SHORT,
                              type_distribution={CellType.TEXT_SHORT: 95, CellType.EMPTY: 5},
                              empty_ratio=0.05, sample_values=[])
                for i in range(5)
            ],
        )
        quality = _make_quality()
        fix = _make_fix_result()
        stats = _make_file_stats()

        output = tmp_path / 'full_report.html'
        export_html_report(detection, output, quality, fix, stats)
        content = output.read_text(encoding='utf-8')

        # Verify all sections
        assert '<!DOCTYPE html>' in content
        assert 'score-card' in content
        assert 'Corrections appliquees' in content
        assert 'Statistiques' in content
        assert 'Qualite par colonne' in content
        assert 'footer' in content
        assert len(content) > 2000

    def test_suggestions_feed_recipe(self):
        """Les suggestions pourraient alimenter une recette."""
        profiles = [
            ColumnProfile(index=0, dominant_type=CellType.DATE_INVALID,
                          type_distribution={CellType.DATE_INVALID: 100},
                          empty_ratio=0.0, sample_values=[]),
            ColumnProfile(index=1, dominant_type=CellType.TEXT_SHORT,
                          type_distribution={CellType.TEXT_SHORT: 100},
                          empty_ratio=0.0,
                          sample_values=['alice@test.com', 'bob@test.com', 'carol@x.com']),
        ]
        detection = _make_detection(header=['Date', 'Email'], profiles=profiles)
        suggestions = generate_suggestions(detection)

        # Verifier qu'on a des suggestions utiles
        actions = [s.action for s in suggestions]
        assert 'fix_dates' in actions
        assert 'anonymize' in actions

    def test_recipe_roundtrip_with_all_step_types(self, tmp_path):
        """Teste la serialisation/deserialisation de tous les types d'etapes."""
        recipe = Recipe(name='all_types', created_at='2025-01-01T00:00:00')
        recipe.header = ['A', 'B', 'C']

        step_types_to_test = [
            StepType.LOAD_FILE, StepType.FIX_DATES, StepType.FIX_DECIMALS,
            StepType.FIX_SHIFTS, StepType.DEDUP_EXACT, StepType.DEDUP_FUZZY,
            StepType.ANONYMIZE, StepType.RENAME_COLUMN, StepType.DELETE_COLUMN,
            StepType.REGEX_REPLACE, StepType.NORMALIZE_CASE, StepType.STRIP_ACCENTS,
            StepType.NORMALIZE_WHITESPACE, StepType.CLEAN_MASKED,
            StepType.FILTER_ROWS, StepType.VALUE_MAP, StepType.SPLIT_COLUMN,
            StepType.MERGE_COLUMNS, StepType.CUSTOM_FORMULA,
        ]

        for st in step_types_to_test:
            recipe.add_step(RecipeStep(step_type=st, description=f"Step {st.name}"))

        filepath = tmp_path / 'all_steps.json'
        save_recipe(recipe, filepath)
        loaded = load_recipe(filepath)

        assert len(loaded.steps) == len(step_types_to_test)
        for original, restored in zip(step_types_to_test, [s.step_type for s in loaded.steps]):
            assert original == restored
