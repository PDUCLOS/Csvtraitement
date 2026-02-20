"""Tests pour core/recipe.py — systeme de recettes et export multi-langage."""

import json
import pytest
from pathlib import Path

from core.recipe import (
    Recipe, RecipeStep, StepType,
    save_recipe, load_recipe,
    record_load, record_fix, record_dedup, record_anonymize,
    record_column_rename, record_column_delete, record_regex_replace,
    record_normalize_case, record_strip_accents,
    export_to_power_query_m, export_to_python, export_to_sql, export_to_dax,
    export_recipe, EXPORT_LANGUAGES, _safe_col_name, _encoding_to_m,
)


# ── Fixtures ──

def _make_recipe() -> Recipe:
    """Cree une recette de test avec plusieurs etapes."""
    recipe = Recipe(name='test_data', source_file='test.csv')
    recipe.encoding = 'utf-8'
    recipe.separator = ';'
    recipe.header = ['ID', 'Nom', 'Email', 'Montant', 'Date']
    recipe.total_rows_input = 1000
    recipe.total_rows_output = 995

    record_load(recipe, 'test.csv', 'utf-8', ';',
                ['ID', 'Nom', 'Email', 'Montant', 'Date'], 1000)
    return recipe


class _MockFixResult:
    total_corrections = 42
    date_fixes = 10
    decimal_fixes = 25
    realignment_fixes = 7


class _MockDedupResult:
    total_duplicate_rows = 15


class _MockAnonymizeRule:
    column_index = 2
    column_name = 'Email'
    class sensitive_type:
        name = 'EMAIL'
    class mask_mode:
        name = 'PARTIAL'


# ── Tests RecipeStep ──

class TestRecipeStep:
    def test_to_dict(self):
        step = RecipeStep(
            step_type=StepType.FIX_DATES,
            description="Fix dates",
            params={'format': 'YYYY-MM-DD'},
            columns=['Date'],
            column_indices=[4],
            rows_affected=10,
        )
        d = step.to_dict()
        assert d['step_type'] == 'FIX_DATES'
        assert d['description'] == 'Fix dates'
        assert d['params'] == {'format': 'YYYY-MM-DD'}

    def test_from_dict(self):
        d = {
            'step_type': 'FIX_DECIMALS',
            'description': 'Normalize decimals',
            'params': {},
            'columns': [],
            'column_indices': [],
            'rows_affected': 5,
        }
        step = RecipeStep.from_dict(d)
        assert step.step_type == StepType.FIX_DECIMALS
        assert step.rows_affected == 5

    def test_roundtrip(self):
        step = RecipeStep(
            step_type=StepType.ANONYMIZE,
            description="Mask emails",
            params={'mode': 'partial'},
        )
        d = step.to_dict()
        step2 = RecipeStep.from_dict(d)
        assert step2.step_type == step.step_type
        assert step2.description == step.description


# ── Tests Recipe ──

class TestRecipe:
    def test_add_step(self):
        recipe = Recipe(name='test')
        recipe.add_step(RecipeStep(step_type=StepType.LOAD_FILE, description="Load"))
        assert len(recipe.steps) == 1

    def test_to_dict(self):
        recipe = _make_recipe()
        d = recipe.to_dict()
        assert d['name'] == 'test_data'
        assert d['encoding'] == 'utf-8'
        assert d['separator'] == ';'
        assert len(d['header']) == 5
        assert len(d['steps']) >= 1

    def test_from_dict(self):
        recipe = _make_recipe()
        d = recipe.to_dict()
        recipe2 = Recipe.from_dict(d)
        assert recipe2.name == recipe.name
        assert recipe2.header == recipe.header
        assert len(recipe2.steps) == len(recipe.steps)


# ── Tests Recording ──

class TestRecording:
    def test_record_load(self):
        recipe = Recipe(name='t')
        record_load(recipe, 'data.csv', 'utf-8', ';', ['A', 'B'], 100)
        assert len(recipe.steps) == 1
        assert recipe.steps[0].step_type == StepType.LOAD_FILE
        assert recipe.source_file == 'data.csv'
        assert recipe.total_rows_input == 100

    def test_record_fix(self):
        recipe = Recipe(name='t')
        recipe.header = ['A', 'B']
        record_fix(recipe, _MockFixResult())
        # date_fixes > 0, decimal_fixes > 0, realignment > 0 -> 3 steps
        assert len(recipe.steps) == 3
        types = [s.step_type for s in recipe.steps]
        assert StepType.FIX_DATES in types
        assert StepType.FIX_DECIMALS in types
        assert StepType.FIX_SHIFTS in types

    def test_record_fix_dates_only(self):
        class Fix:
            total_corrections = 5
            date_fixes = 5
            decimal_fixes = 0
            realignment_fixes = 0
        recipe = Recipe(name='t')
        record_fix(recipe, Fix())
        assert len(recipe.steps) == 1
        assert recipe.steps[0].step_type == StepType.FIX_DATES

    def test_record_dedup(self):
        recipe = Recipe(name='t')
        record_dedup(recipe, _MockDedupResult(), fuzzy=False)
        assert recipe.steps[0].step_type == StepType.DEDUP_EXACT

    def test_record_dedup_fuzzy(self):
        recipe = Recipe(name='t')
        record_dedup(recipe, _MockDedupResult(), fuzzy=True)
        assert recipe.steps[0].step_type == StepType.DEDUP_FUZZY

    def test_record_anonymize(self):
        recipe = Recipe(name='t')
        record_anonymize(recipe, [_MockAnonymizeRule()], rows_affected=50)
        assert recipe.steps[0].step_type == StepType.ANONYMIZE
        assert recipe.steps[0].rows_affected == 50

    def test_record_column_rename(self):
        recipe = Recipe(name='t')
        record_column_rename(recipe, {0: 'NewA', 2: 'NewC'}, ['A', 'B', 'C'])
        assert recipe.steps[0].step_type == StepType.RENAME_COLUMN
        assert recipe.steps[0].params['mapping'] == {'A': 'NewA', 'C': 'NewC'}

    def test_record_column_delete(self):
        recipe = Recipe(name='t')
        record_column_delete(recipe, [1, 3], ['A', 'B', 'C', 'D'])
        assert recipe.steps[0].step_type == StepType.DELETE_COLUMN
        assert recipe.steps[0].columns == ['B', 'D']

    def test_record_regex_replace(self):
        recipe = Recipe(name='t')
        record_regex_replace(recipe, 'Nom', 0, r'\s+', ' ', rows_affected=10)
        assert recipe.steps[0].step_type == StepType.REGEX_REPLACE
        assert recipe.steps[0].params['pattern'] == r'\s+'

    def test_record_normalize_case(self):
        recipe = Recipe(name='t')
        record_normalize_case(recipe, ['Nom'], [1], mode='lower')
        assert recipe.steps[0].step_type == StepType.NORMALIZE_CASE

    def test_record_strip_accents(self):
        recipe = Recipe(name='t')
        record_strip_accents(recipe, ['Nom'], [1])
        assert recipe.steps[0].step_type == StepType.STRIP_ACCENTS


# ── Tests Save/Load ──

class TestSaveLoad:
    def test_save_and_load(self, tmp_path):
        recipe = _make_recipe()
        record_fix(recipe, _MockFixResult())
        filepath = tmp_path / 'recipe.json'
        save_recipe(recipe, filepath)

        assert filepath.exists()
        loaded = load_recipe(filepath)
        assert loaded.name == recipe.name
        assert len(loaded.steps) == len(recipe.steps)
        assert loaded.header == recipe.header

    def test_json_content(self, tmp_path):
        recipe = _make_recipe()
        filepath = tmp_path / 'recipe.json'
        save_recipe(recipe, filepath)

        with open(filepath) as f:
            data = json.load(f)
        assert 'name' in data
        assert 'steps' in data
        assert data['name'] == 'test_data'


# ── Tests Export Power Query M ──

class TestExportPowerQueryM:
    def test_basic_output(self):
        recipe = _make_recipe()
        m_code = export_to_power_query_m(recipe)
        assert 'let' in m_code
        assert 'in' in m_code
        assert 'Csv.Document' in m_code
        assert 'Result' in m_code

    def test_with_fix_dates(self):
        recipe = _make_recipe()
        record_fix(recipe, _MockFixResult())
        m_code = export_to_power_query_m(recipe)
        assert '00.00.0000' in m_code
        assert 'Table.TransformColumns' in m_code

    def test_with_dedup(self):
        recipe = _make_recipe()
        record_dedup(recipe, _MockDedupResult())
        m_code = export_to_power_query_m(recipe)
        assert 'Table.Distinct' in m_code

    def test_with_rename(self):
        recipe = _make_recipe()
        record_column_rename(recipe, {0: 'Identifiant'}, recipe.header)
        m_code = export_to_power_query_m(recipe)
        assert 'Table.RenameColumns' in m_code

    def test_with_delete(self):
        recipe = _make_recipe()
        record_column_delete(recipe, [2], recipe.header)
        m_code = export_to_power_query_m(recipe)
        assert 'Table.RemoveColumns' in m_code

    def test_with_anonymize(self):
        recipe = _make_recipe()
        record_anonymize(recipe, [_MockAnonymizeRule()])
        m_code = export_to_power_query_m(recipe)
        assert 'Table.TransformColumns' in m_code
        assert 'Text.Start' in m_code

    def test_with_normalize_case(self):
        recipe = _make_recipe()
        record_normalize_case(recipe, ['Nom'], [1], mode='upper')
        m_code = export_to_power_query_m(recipe)
        assert 'Text.Upper' in m_code


# ── Tests Export Python ──

class TestExportPython:
    def test_basic_output(self):
        recipe = _make_recipe()
        py_code = export_to_python(recipe)
        assert 'import pandas as pd' in py_code
        assert 'def apply_recipe' in py_code
        assert "pd.read_csv" in py_code

    def test_with_fix_dates(self):
        recipe = _make_recipe()
        record_fix(recipe, _MockFixResult())
        py_code = export_to_python(recipe)
        assert 'invalid_dates' in py_code
        assert 'normalize_decimal_fr' in py_code

    def test_with_dedup(self):
        recipe = _make_recipe()
        record_dedup(recipe, _MockDedupResult())
        py_code = export_to_python(recipe)
        assert 'drop_duplicates' in py_code

    def test_with_rename(self):
        recipe = _make_recipe()
        record_column_rename(recipe, {0: 'NewID'}, recipe.header)
        py_code = export_to_python(recipe)
        assert 'rename' in py_code

    def test_with_anonymize(self):
        recipe = _make_recipe()
        record_anonymize(recipe, [_MockAnonymizeRule()])
        py_code = export_to_python(recipe)
        assert 'Masquage partiel' in py_code
        assert '****' in py_code


# ── Tests Export SQL ──

class TestExportSQL:
    def test_basic_output(self):
        recipe = _make_recipe()
        sql_code = export_to_sql(recipe)
        assert 'source_data' in sql_code
        assert 'Recette generee' in sql_code

    def test_with_fix_dates(self):
        recipe = _make_recipe()
        record_fix(recipe, _MockFixResult())
        sql_code = export_to_sql(recipe)
        assert 'UPDATE source_data' in sql_code
        assert '00.00.0000' in sql_code

    def test_with_dedup(self):
        recipe = _make_recipe()
        record_dedup(recipe, _MockDedupResult())
        sql_code = export_to_sql(recipe)
        assert 'DELETE FROM' in sql_code
        assert 'GROUP BY' in sql_code

    def test_with_rename(self):
        recipe = _make_recipe()
        record_column_rename(recipe, {0: 'NewID'}, recipe.header)
        sql_code = export_to_sql(recipe)
        assert 'ALTER TABLE' in sql_code
        assert 'RENAME COLUMN' in sql_code

    def test_with_regex(self):
        recipe = _make_recipe()
        record_regex_replace(recipe, 'Nom', 1, r'\s+', ' ')
        sql_code = export_to_sql(recipe)
        assert 'REGEXP_REPLACE' in sql_code


# ── Tests Export DAX ──

class TestExportDAX:
    def test_basic_output(self):
        recipe = _make_recipe()
        dax_code = export_to_dax(recipe)
        assert 'Recette DAX' in dax_code

    def test_with_fix_dates(self):
        recipe = _make_recipe()
        record_fix(recipe, _MockFixResult())
        dax_code = export_to_dax(recipe)
        assert 'BLANK()' in dax_code
        assert '_Clean' in dax_code

    def test_with_anonymize(self):
        recipe = _make_recipe()
        record_anonymize(recipe, [_MockAnonymizeRule()])
        dax_code = export_to_dax(recipe)
        assert 'REPT("*"' in dax_code
        assert '_Masked' in dax_code


# ── Tests Helpers ──

class TestHelpers:
    def test_safe_col_name(self):
        assert _safe_col_name('Nom') == 'Nom'
        assert _safe_col_name('Nom Prenom') == 'Nom_Prenom'
        assert _safe_col_name('123col') == '_123col'
        assert _safe_col_name('') == '_col'
        assert _safe_col_name('a@b#c') == 'a_b_c'

    def test_encoding_to_m(self):
        assert _encoding_to_m('utf-8') == '65001'
        assert _encoding_to_m('cp1252') == '1252'
        assert _encoding_to_m('latin-1') == '28591'
        assert _encoding_to_m('ascii') == '20127'
        assert _encoding_to_m('unknown') == '65001'


# ── Tests export_recipe dispatch ──

class TestExportRecipeDispatch:
    def test_export_json(self, tmp_path):
        recipe = _make_recipe()
        out = tmp_path / 'recipe.json'
        export_recipe(recipe, 'json', out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data['name'] == 'test_data'

    def test_export_power_query(self, tmp_path):
        recipe = _make_recipe()
        out = tmp_path / 'recipe.pq'
        export_recipe(recipe, 'power_query_m', out)
        assert out.exists()
        content = out.read_text()
        assert 'let' in content

    def test_export_python(self, tmp_path):
        recipe = _make_recipe()
        out = tmp_path / 'recipe.py'
        export_recipe(recipe, 'python', out)
        assert out.exists()
        content = out.read_text()
        assert 'pandas' in content

    def test_export_sql(self, tmp_path):
        recipe = _make_recipe()
        out = tmp_path / 'recipe.sql'
        export_recipe(recipe, 'sql', out)
        assert out.exists()

    def test_export_dax(self, tmp_path):
        recipe = _make_recipe()
        out = tmp_path / 'recipe.dax'
        export_recipe(recipe, 'dax', out)
        assert out.exists()

    def test_invalid_language(self, tmp_path):
        recipe = _make_recipe()
        with pytest.raises(ValueError, match="Langage inconnu"):
            export_recipe(recipe, 'fortran', tmp_path / 'x')


# ── Tests EXPORT_LANGUAGES registry ──

class TestExportLanguages:
    def test_all_languages_registered(self):
        assert 'power_query_m' in EXPORT_LANGUAGES
        assert 'python' in EXPORT_LANGUAGES
        assert 'sql' in EXPORT_LANGUAGES
        assert 'dax' in EXPORT_LANGUAGES
        assert 'json' in EXPORT_LANGUAGES

    def test_each_has_name_and_ext(self):
        for key, (name, ext, fn) in EXPORT_LANGUAGES.items():
            assert name, f"Missing name for {key}"
            assert ext.startswith('.'), f"Bad extension for {key}: {ext}"
