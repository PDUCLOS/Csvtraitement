"""Systeme de recettes : enregistrement des transformations et export multi-langage.

Enregistre toutes les operations appliquees sur un fichier et permet de les
exporter en :
- JSON (pour rejeu dans CSV Traitement)
- Power Query M (pour Power BI / Excel)
- Python / pandas
- SQL (transformations post-import)
- DAX (colonnes calculees Power BI)
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StepType(Enum):
    """Types d'etapes de transformation."""
    LOAD_FILE = auto()
    FIX_DATES = auto()
    FIX_DECIMALS = auto()
    FIX_SHIFTS = auto()
    NORMALIZE_WHITESPACE = auto()
    CLEAN_MASKED = auto()
    DEDUP_EXACT = auto()
    DEDUP_FUZZY = auto()
    ANONYMIZE = auto()
    RENAME_COLUMN = auto()
    DELETE_COLUMN = auto()
    REORDER_COLUMNS = auto()
    FILTER_ROWS = auto()
    REGEX_REPLACE = auto()
    VALUE_MAP = auto()
    SPLIT_COLUMN = auto()
    MERGE_COLUMNS = auto()
    NORMALIZE_CASE = auto()
    STRIP_ACCENTS = auto()
    CUSTOM_FORMULA = auto()


@dataclass
class RecipeStep:
    """Une etape de transformation dans la recette."""
    step_type: StepType
    description: str
    params: dict = field(default_factory=dict)
    # Colonnes concernees (noms et/ou indices)
    columns: list[str] = field(default_factory=list)
    column_indices: list[int] = field(default_factory=list)
    # Resultats de cette etape
    rows_affected: int = 0

    def to_dict(self) -> dict:
        return {
            'step_type': self.step_type.name,
            'description': self.description,
            'params': self.params,
            'columns': self.columns,
            'column_indices': self.column_indices,
            'rows_affected': self.rows_affected,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RecipeStep':
        return cls(
            step_type=StepType[data['step_type']],
            description=data['description'],
            params=data.get('params', {}),
            columns=data.get('columns', []),
            column_indices=data.get('column_indices', []),
            rows_affected=data.get('rows_affected', 0),
        )


@dataclass
class Recipe:
    """Recette complete : serie d'etapes de transformation."""
    name: str
    source_file: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    separator: str = ";"
    encoding: str = "utf-8"
    header: list[str] = field(default_factory=list)
    steps: list[RecipeStep] = field(default_factory=list)
    total_rows_input: int = 0
    total_rows_output: int = 0

    def add_step(self, step: RecipeStep):
        self.steps.append(step)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'source_file': self.source_file,
            'created_at': self.created_at,
            'separator': self.separator,
            'encoding': self.encoding,
            'header': self.header,
            'steps': [s.to_dict() for s in self.steps],
            'total_rows_input': self.total_rows_input,
            'total_rows_output': self.total_rows_output,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Recipe':
        recipe = cls(
            name=data['name'],
            source_file=data.get('source_file', ''),
            created_at=data.get('created_at', ''),
            separator=data.get('separator', ';'),
            encoding=data.get('encoding', 'utf-8'),
            header=data.get('header', []),
            total_rows_input=data.get('total_rows_input', 0),
            total_rows_output=data.get('total_rows_output', 0),
        )
        for s in data.get('steps', []):
            recipe.steps.append(RecipeStep.from_dict(s))
        return recipe


def save_recipe(recipe: Recipe, filepath: Path) -> Path:
    """Sauvegarde la recette en JSON."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(recipe.to_dict(), f, indent=2, ensure_ascii=False)
    logger.info(f"Recette sauvegardee : {filepath}")
    return filepath


def load_recipe(filepath: Path) -> Recipe:
    """Charge une recette depuis un JSON."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return Recipe.from_dict(data)


# ── Enregistrement automatique des etapes ──

def record_load(recipe: Recipe, filepath: str, encoding: str, separator: str,
                header: list[str], total_lines: int):
    """Enregistre l'etape de chargement."""
    recipe.source_file = filepath
    recipe.encoding = encoding
    recipe.separator = separator
    recipe.header = [h.strip() for h in header]
    recipe.total_rows_input = total_lines
    recipe.add_step(RecipeStep(
        step_type=StepType.LOAD_FILE,
        description=f"Charger {Path(filepath).name}",
        params={'encoding': encoding, 'separator': repr(separator)},
        rows_affected=total_lines,
    ))


def record_fix(recipe: Recipe, fix_result):
    """Enregistre les corrections appliquees."""
    if fix_result.date_fixes > 0:
        recipe.add_step(RecipeStep(
            step_type=StepType.FIX_DATES,
            description="Corriger les dates invalides (00.00.0000 -> vide, normaliser en YYYY-MM-DD)",
            params={'format_output': 'YYYY-MM-DD'},
            rows_affected=fix_result.date_fixes,
        ))
    if fix_result.decimal_fixes > 0:
        recipe.add_step(RecipeStep(
            step_type=StepType.FIX_DECIMALS,
            description="Normaliser les decimales (FR 1.234,56 -> 1234.56)",
            params={'format_input': 'FR', 'format_output': 'standard'},
            rows_affected=fix_result.decimal_fixes,
        ))
    if fix_result.realignment_fixes > 0:
        recipe.add_step(RecipeStep(
            step_type=StepType.FIX_SHIFTS,
            description="Realigner les colonnes decalees",
            rows_affected=fix_result.realignment_fixes,
        ))


def record_dedup(recipe: Recipe, dedup_result, fuzzy: bool = False):
    """Enregistre la deduplication."""
    step_type = StepType.DEDUP_FUZZY if fuzzy else StepType.DEDUP_EXACT
    recipe.add_step(RecipeStep(
        step_type=step_type,
        description=f"Supprimer les doublons ({'fuzzy' if fuzzy else 'exacts'})",
        params={
            'mode': 'fuzzy' if fuzzy else 'exact',
            'threshold': 0.85 if fuzzy else 1.0,
        },
        rows_affected=dedup_result.total_duplicate_rows,
    ))


def record_anonymize(recipe: Recipe, rules: list, rows_affected: int = 0):
    """Enregistre l'anonymisation."""
    cols = []
    col_indices = []
    params_rules = []
    for r in rules:
        cols.append(r.column_name if hasattr(r, 'column_name') else f"col_{r.column_index}")
        col_indices.append(r.column_index)
        params_rules.append({
            'column_index': r.column_index,
            'type': r.sensitive_type.name,
            'mask_mode': r.mask_mode.name,
        })
    recipe.add_step(RecipeStep(
        step_type=StepType.ANONYMIZE,
        description=f"Anonymiser {len(rules)} colonne(s) sensible(s)",
        params={'rules': params_rules},
        columns=cols,
        column_indices=col_indices,
        rows_affected=rows_affected,
    ))


def record_column_rename(recipe: Recipe, mapping: dict[int, str], header: list[str]):
    """Enregistre un renommage de colonnes."""
    old_new = {}
    for idx, new_name in mapping.items():
        old_name = header[idx].strip() if idx < len(header) else f"col_{idx}"
        old_new[old_name] = new_name
    recipe.add_step(RecipeStep(
        step_type=StepType.RENAME_COLUMN,
        description=f"Renommer {len(mapping)} colonne(s)",
        params={'mapping': old_new},
    ))


def record_column_delete(recipe: Recipe, indices: list[int], header: list[str]):
    """Enregistre une suppression de colonnes."""
    cols = [header[i].strip() if i < len(header) else f"col_{i}" for i in indices]
    recipe.add_step(RecipeStep(
        step_type=StepType.DELETE_COLUMN,
        description=f"Supprimer {len(indices)} colonne(s) : {', '.join(cols)}",
        columns=cols,
        column_indices=indices,
    ))


def record_regex_replace(recipe: Recipe, column: str, column_index: int,
                         pattern: str, replacement: str, rows_affected: int = 0):
    """Enregistre un remplacement regex."""
    recipe.add_step(RecipeStep(
        step_type=StepType.REGEX_REPLACE,
        description=f"Regex sur [{column}] : s/{pattern}/{replacement}/",
        params={'pattern': pattern, 'replacement': replacement},
        columns=[column],
        column_indices=[column_index],
        rows_affected=rows_affected,
    ))


def record_normalize_case(recipe: Recipe, columns: list[str], column_indices: list[int],
                          mode: str = 'lower', rows_affected: int = 0):
    """Enregistre une normalisation de casse."""
    recipe.add_step(RecipeStep(
        step_type=StepType.NORMALIZE_CASE,
        description=f"Normaliser casse ({mode}) sur {len(columns)} colonne(s)",
        params={'mode': mode},
        columns=columns,
        column_indices=column_indices,
        rows_affected=rows_affected,
    ))


def record_strip_accents(recipe: Recipe, columns: list[str], column_indices: list[int],
                         rows_affected: int = 0):
    """Enregistre un retrait d'accents."""
    recipe.add_step(RecipeStep(
        step_type=StepType.STRIP_ACCENTS,
        description=f"Retirer les accents sur {len(columns)} colonne(s)",
        columns=columns,
        column_indices=column_indices,
        rows_affected=rows_affected,
    ))


# ═══════════════════════════════════════════════════════════
# EXPORT MULTI-LANGAGE
# ═══════════════════════════════════════════════════════════

def _safe_col_name(name: str) -> str:
    """Nettoie un nom de colonne pour utilisation dans du code."""
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', name.strip())
    # Supprimer les underscores en trop aux extremites
    safe = safe.strip('_')
    if safe and safe[0].isdigit():
        safe = '_' + safe
    return safe or '_col'


def export_to_power_query_m(recipe: Recipe) -> str:
    """Exporte la recette en langage Power Query M (pour Power BI / Excel).

    Genere un script M complet et executable qui reproduit les transformations.
    """
    lines = [
        '// ══════════════════════════════════════════════════════',
        f'// Recette generee par CSV Traitement — {recipe.created_at[:10]}',
        f'// Source : {recipe.source_file}',
        f'// Etapes : {len(recipe.steps)}',
        '// ══════════════════════════════════════════════════════',
        '',
        'let',
    ]

    step_num = 0
    prev_step = 'Source'

    # Etape 0 : chargement
    sep_m = recipe.separator.replace('"', '""')
    lines.append(f'    // Etape 1 : Charger le fichier')
    lines.append(f'    Source = Csv.Document(')
    lines.append(f'        File.Contents("CHEMIN_VERS_FICHIER"),')
    lines.append(f'        [Delimiter="{sep_m}", Encoding={_encoding_to_m(recipe.encoding)}, '
                 f'QuoteStyle=QuoteStyle.Csv]')
    lines.append(f'    ),')
    lines.append(f'    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),')
    prev_step = 'PromotedHeaders'
    step_num = 1

    for step in recipe.steps:
        if step.step_type == StepType.LOAD_FILE:
            continue  # deja traite

        step_num += 1
        step_name = f'Step{step_num}'
        lines.append(f'')
        lines.append(f'    // Etape {step_num} : {step.description}')

        if step.step_type == StepType.FIX_DATES:
            # Transformer les colonnes dates en type date
            lines.append(f'    // Corriger les dates invalides et normaliser')
            transforms = []
            for col in recipe.header:
                transforms.append(f'{{"{col}", type text}}')
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            # On applique un remplacement des dates invalides
            lines.append(f'        // Remplacer les dates invalides (00.00.0000, etc.) par null')
            lines.append(f'        // puis convertir en date ISO')
            col_transforms = []
            for col in recipe.header:
                col_transforms.append(
                    f'        {{"{col}", each if Text.Contains(_, "00.00.0000") or '
                    f'Text.Contains(_, "00/00/0000") then null else _, type nullable text}}'
                )
            lines.append(',\n'.join(col_transforms))
            lines.append(f'    }}),')

        elif step.step_type == StepType.FIX_DECIMALS:
            lines.append(f'    // Normaliser les decimales FR (1.234,56 -> 1234.56)')
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            col_transforms = []
            for col in recipe.header:
                col_transforms.append(
                    f'        {{"{col}", each try Number.From('
                    f'Text.Replace(Text.Replace(_, ".", ""), ",", ".")) '
                    f'otherwise _, type nullable text}}'
                )
            lines.append(',\n'.join(col_transforms))
            lines.append(f'    }}),')

        elif step.step_type == StepType.DEDUP_EXACT:
            lines.append(f'    {step_name} = Table.Distinct({prev_step}),')

        elif step.step_type == StepType.DEDUP_FUZZY:
            lines.append(f'    // Deduplication fuzzy (a adapter manuellement)')
            lines.append(f'    // Power Query n\'a pas de fuzzy matching natif.')
            lines.append(f'    // Utilisez Table.FuzzyNestedJoin ou un clustering externe.')
            lines.append(f'    {step_name} = Table.Distinct({prev_step}),')

        elif step.step_type == StepType.ANONYMIZE:
            rules = step.params.get('rules', [])
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            col_transforms = []
            for rule in rules:
                col_idx = rule.get('column_index', 0)
                col_name = recipe.header[col_idx] if col_idx < len(recipe.header) else f'Col{col_idx}'
                mask_mode = rule.get('mask_mode', 'PARTIAL')
                if mask_mode == 'PARTIAL':
                    col_transforms.append(
                        f'        {{"{col_name}", each if _ <> null and Text.Length(_) > 4 '
                        f'then Text.Start(_, 2) & Text.Repeat("*", Text.Length(_) - 4) '
                        f'& Text.End(_, 2) else "****", type text}}'
                    )
                else:
                    col_transforms.append(
                        f'        {{"{col_name}", each "****", type text}}'
                    )
            lines.append(',\n'.join(col_transforms))
            lines.append(f'    }}),')

        elif step.step_type == StepType.RENAME_COLUMN:
            mapping = step.params.get('mapping', {})
            renames = ', '.join(f'{{"{old}", "{new}"}}' for old, new in mapping.items())
            lines.append(f'    {step_name} = Table.RenameColumns({prev_step}, {{{renames}}}),')

        elif step.step_type == StepType.DELETE_COLUMN:
            cols = ', '.join(f'"{c}"' for c in step.columns)
            lines.append(f'    {step_name} = Table.RemoveColumns({prev_step}, {{{cols}}}),')

        elif step.step_type == StepType.REGEX_REPLACE:
            pattern = step.params.get('pattern', '')
            replacement = step.params.get('replacement', '')
            col = step.columns[0] if step.columns else 'Col'
            lines.append(f'    // Regex : s/{pattern}/{replacement}/')
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            lines.append(f'        {{"{col}", each Text.Replace(_, "{pattern}", "{replacement}"), type text}}')
            lines.append(f'    }}),')

        elif step.step_type == StepType.NORMALIZE_CASE:
            mode = step.params.get('mode', 'lower')
            func = 'Text.Lower' if mode == 'lower' else ('Text.Upper' if mode == 'upper' else 'Text.Proper')
            col_transforms = []
            for col in step.columns:
                col_transforms.append(f'        {{"{col}", each {func}(_), type text}}')
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            lines.append(',\n'.join(col_transforms))
            lines.append(f'    }}),')

        elif step.step_type == StepType.STRIP_ACCENTS:
            lines.append(f'    // Retirer les accents (necessite une fonction helper M)')
            lines.append(f'    fnRemoveAccents = (txt as nullable text) as nullable text =>')
            lines.append(f'        if txt = null then null')
            lines.append(f'        else Text.Combine(List.Transform(Text.ToList(txt), each')
            lines.append(f'            if _ = "e" or _ = "e" then "e"')
            lines.append(f'            else if _ = "a" or _ = "a" then "a"')
            lines.append(f'            else _)),')
            col_transforms = []
            for col in step.columns:
                col_transforms.append(f'        {{"{col}", each fnRemoveAccents(_), type text}}')
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            lines.append(',\n'.join(col_transforms))
            lines.append(f'    }}),')

        elif step.step_type == StepType.NORMALIZE_WHITESPACE:
            lines.append(f'    {step_name} = Table.TransformColumns({prev_step}, {{')
            col_transforms = []
            for col in recipe.header:
                col_transforms.append(
                    f'        {{"{col}", each Text.Trim(Text.Clean(_)), type text}}'
                )
            lines.append(',\n'.join(col_transforms))
            lines.append(f'    }}),')

        elif step.step_type == StepType.CLEAN_MASKED:
            lines.append(f'    // Nettoyer les valeurs masquees (N/A, NULL, #REF!, etc.)')
            lines.append(f'    {step_name} = Table.ReplaceValue({prev_step},')
            lines.append(f'        each [Column1],  // Appliquer sur toutes les colonnes')
            lines.append(f'        each if List.Contains({{"N/A", "NULL", "#REF!", "-", "??", "n/a", "null"}}, _)')
            lines.append(f'             then null else _,')
            lines.append(f'        Replacer.ReplaceValue,')
            lines.append(f'        Table.ColumnNames({prev_step})')
            lines.append(f'    ),')

        elif step.step_type == StepType.FILTER_ROWS:
            condition = step.params.get('condition', 'true')
            lines.append(f'    // Filtrer les lignes : {condition}')
            lines.append(f'    {step_name} = Table.SelectRows({prev_step}, each true), // A adapter')

        elif step.step_type == StepType.FIX_SHIFTS:
            lines.append(f'    // Realignement de colonnes (specifique a CSV Traitement)')
            lines.append(f'    // Cette operation n\'a pas d\'equivalent direct en Power Query.')
            lines.append(f'    // Le fichier corrige par CSV Traitement est deja realigne.')
            lines.append(f'    {step_name} = {prev_step},')

        else:
            lines.append(f'    // {step.step_type.name} : {step.description}')
            lines.append(f'    // (pas de traduction automatique — a adapter manuellement)')
            lines.append(f'    {step_name} = {prev_step},')

        prev_step = step_name

    lines.append(f'')
    lines.append(f'    Result = {prev_step}')
    lines.append(f'in')
    lines.append(f'    Result')

    return '\n'.join(lines)


def _encoding_to_m(encoding: str) -> str:
    """Convertit un nom d'encodage Python en code Power Query M."""
    enc = encoding.lower().replace('-', '')
    mapping = {
        'utf8': '65001',
        'utf16': '1200',
        'utf16le': '1200',
        'utf16be': '1201',
        'latin1': '28591',
        'iso88591': '28591',
        'iso885915': '28605',
        'cp1252': '1252',
        'windows1252': '1252',
        'ascii': '20127',
    }
    return mapping.get(enc, '65001')


def export_to_python(recipe: Recipe) -> str:
    """Exporte la recette en code Python / pandas."""
    lines = [
        '"""',
        f'Recette generee par CSV Traitement - {recipe.created_at[:10]}',
        f'Source : {recipe.source_file}',
        f'Etapes : {len(recipe.steps)}',
        '"""',
        '',
        'import pandas as pd',
        'import re',
        '',
        '',
        'def apply_recipe(filepath: str) -> pd.DataFrame:',
        f'    """Applique la recette de nettoyage sur un fichier."""',
    ]

    for step in recipe.steps:
        lines.append(f'')
        lines.append(f'    # {step.description}')

        if step.step_type == StepType.LOAD_FILE:
            sep = recipe.separator.replace("'", "\\'")
            lines.append(f"    df = pd.read_csv(filepath, sep='{sep}', encoding='{recipe.encoding}')")

        elif step.step_type == StepType.FIX_DATES:
            lines.append(f'    # Remplacer les dates invalides par NaT')
            lines.append(f'    invalid_dates = ["00.00.0000", "00/00/0000", "00-00-0000"]')
            lines.append(f'    for col in df.columns:')
            lines.append(f'        mask = df[col].astype(str).isin(invalid_dates)')
            lines.append(f'        df.loc[mask, col] = None')

        elif step.step_type == StepType.FIX_DECIMALS:
            lines.append(f'    # Normaliser les decimales FR (1.234,56 -> 1234.56)')
            lines.append(f'    def normalize_decimal_fr(val):')
            lines.append(f'        if not isinstance(val, str):')
            lines.append(f'            return val')
            lines.append(f'        # Format FR : points = milliers, virgule = decimale')
            lines.append(f'        if "," in val and "." in val:')
            lines.append(f'            val = val.replace(".", "").replace(",", ".")')
            lines.append(f'        elif "," in val:')
            lines.append(f'            val = val.replace(",", ".")')
            lines.append(f'        try:')
            lines.append(f'            return float(val)')
            lines.append(f'        except (ValueError, TypeError):')
            lines.append(f'            return val')
            lines.append(f'    for col in df.columns:')
            lines.append(f'        df[col] = df[col].apply(normalize_decimal_fr)')

        elif step.step_type == StepType.DEDUP_EXACT:
            lines.append(f'    df = df.drop_duplicates()')

        elif step.step_type == StepType.DEDUP_FUZZY:
            lines.append(f'    # Deduplication fuzzy (necessite fuzzywuzzy ou rapidfuzz)')
            lines.append(f'    df = df.drop_duplicates()  # Fallback exact')

        elif step.step_type == StepType.ANONYMIZE:
            rules = step.params.get('rules', [])
            for rule in rules:
                col_idx = rule.get('column_index', 0)
                col_name = recipe.header[col_idx] if col_idx < len(recipe.header) else f'col_{col_idx}'
                safe = _safe_col_name(col_name)
                mask_mode = rule.get('mask_mode', 'PARTIAL')
                if mask_mode == 'PARTIAL':
                    lines.append(f'    # Masquage partiel de la colonne "{col_name}"')
                    lines.append(f'    df["{col_name}"] = df["{col_name}"].astype(str).apply(')
                    lines.append(f'        lambda x: x[:2] + "*" * max(0, len(x)-4) + x[-2:] '
                                 f'if len(x) > 4 else "****")')
                else:
                    lines.append(f'    df["{col_name}"] = "****"')

        elif step.step_type == StepType.RENAME_COLUMN:
            mapping = step.params.get('mapping', {})
            lines.append(f'    df = df.rename(columns={repr(mapping)})')

        elif step.step_type == StepType.DELETE_COLUMN:
            cols = step.columns
            lines.append(f'    df = df.drop(columns={repr(cols)}, errors="ignore")')

        elif step.step_type == StepType.REGEX_REPLACE:
            pattern = step.params.get('pattern', '')
            replacement = step.params.get('replacement', '')
            col = step.columns[0] if step.columns else 'Col'
            lines.append(f'    df["{col}"] = df["{col}"].astype(str).str.replace(')
            lines.append(f'        r"{pattern}", "{replacement}", regex=True)')

        elif step.step_type == StepType.NORMALIZE_CASE:
            mode = step.params.get('mode', 'lower')
            func = 'str.lower()' if mode == 'lower' else ('str.upper()' if mode == 'upper' else 'str.title()')
            for col in step.columns:
                lines.append(f'    df["{col}"] = df["{col}"].astype(str).{func}')

        elif step.step_type == StepType.STRIP_ACCENTS:
            lines.append(f'    import unicodedata')
            lines.append(f'    def strip_accents(s):')
            lines.append(f'        return "".join(c for c in unicodedata.normalize("NFD", str(s))')
            lines.append(f'                       if unicodedata.category(c) != "Mn")')
            for col in step.columns:
                lines.append(f'    df["{col}"] = df["{col}"].apply(strip_accents)')

        elif step.step_type == StepType.NORMALIZE_WHITESPACE:
            lines.append(f'    for col in df.columns:')
            lines.append(f'        df[col] = df[col].astype(str).str.strip().str.replace(r"\\s+", " ", regex=True)')

        elif step.step_type == StepType.CLEAN_MASKED:
            lines.append(f'    masked_values = ["N/A", "NULL", "#REF!", "-", "??", "n/a", "null", ""]')
            lines.append(f'    df = df.replace(masked_values, None)')

        elif step.step_type == StepType.FILTER_ROWS:
            condition = step.params.get('condition', '')
            lines.append(f'    # Filtrer : {condition}')
            lines.append(f'    df = df.dropna(how="all")  # A adapter')

        else:
            lines.append(f'    # {step.step_type.name} : a adapter manuellement')
            lines.append(f'    pass')

    lines.append(f'')
    lines.append(f'    return df')
    lines.append(f'')
    lines.append(f'')
    lines.append(f'if __name__ == "__main__":')
    lines.append(f'    df = apply_recipe("CHEMIN_VERS_FICHIER")')
    lines.append(f'    print(f"Resultat : {{len(df)}} lignes, {{len(df.columns)}} colonnes")')
    lines.append(f'    df.to_csv("output_clean.csv", index=False, sep=";")')

    return '\n'.join(lines)


def export_to_sql(recipe: Recipe) -> str:
    """Exporte la recette en instructions SQL (post-import)."""
    lines = [
        '-- ══════════════════════════════════════════════════════',
        f'-- Recette generee par CSV Traitement - {recipe.created_at[:10]}',
        f'-- Source : {recipe.source_file}',
        f'-- Etapes : {len(recipe.steps)}',
        '-- ══════════════════════════════════════════════════════',
        '',
        '-- Note : ces instructions SQL s\'appliquent APRES import des donnees',
        '-- dans une table temporaire. Adaptez le nom de table.',
        '',
        '-- Table source (a adapter)',
        f'-- CREATE TABLE source_data AS SELECT * FROM imported_csv;',
        '',
    ]

    table = 'source_data'
    step_num = 0

    for step in recipe.steps:
        if step.step_type == StepType.LOAD_FILE:
            continue

        step_num += 1
        lines.append(f'-- Etape {step_num} : {step.description}')

        if step.step_type == StepType.FIX_DATES:
            lines.append(f"UPDATE {table} SET")
            updates = []
            for col in recipe.header:
                safe = _safe_col_name(col)
                updates.append(
                    f'    "{col}" = CASE WHEN "{col}" IN (\'00.00.0000\', \'00/00/0000\') '
                    f'THEN NULL ELSE "{col}" END'
                )
            lines.append(',\n'.join(updates) + ';')

        elif step.step_type == StepType.FIX_DECIMALS:
            lines.append(f'-- Normaliser les decimales FR -> standard')
            for col in recipe.header:
                lines.append(
                    f'UPDATE {table} SET "{col}" = '
                    f'REPLACE(REPLACE("{col}", \'.\', \'\'), \',\', \'.\') '
                    f'WHERE "{col}" LIKE \'%.%,%\';'
                )

        elif step.step_type == StepType.DEDUP_EXACT:
            lines.append(f'DELETE FROM {table} WHERE rowid NOT IN (')
            lines.append(f'    SELECT MIN(rowid) FROM {table} GROUP BY')
            cols_list = ', '.join(f'"{c}"' for c in recipe.header)
            lines.append(f'    {cols_list}')
            lines.append(f');')

        elif step.step_type == StepType.ANONYMIZE:
            rules = step.params.get('rules', [])
            for rule in rules:
                col_idx = rule.get('column_index', 0)
                col_name = recipe.header[col_idx] if col_idx < len(recipe.header) else f'col_{col_idx}'
                mask_mode = rule.get('mask_mode', 'PARTIAL')
                if mask_mode == 'PARTIAL':
                    lines.append(
                        f'UPDATE {table} SET "{col_name}" = '
                        f'SUBSTR("{col_name}", 1, 2) || '
                        f'REPLACE(SUBSTR("{col_name}", 3, LENGTH("{col_name}") - 4), '
                        f'SUBSTR("{col_name}", 3, 1), \'*\') || '
                        f'SUBSTR("{col_name}", -2);'
                    )
                else:
                    lines.append(f'UPDATE {table} SET "{col_name}" = \'****\';')

        elif step.step_type == StepType.RENAME_COLUMN:
            mapping = step.params.get('mapping', {})
            for old, new in mapping.items():
                lines.append(f'ALTER TABLE {table} RENAME COLUMN "{old}" TO "{new}";')

        elif step.step_type == StepType.DELETE_COLUMN:
            for col in step.columns:
                lines.append(f'ALTER TABLE {table} DROP COLUMN "{col}";')

        elif step.step_type == StepType.REGEX_REPLACE:
            pattern = step.params.get('pattern', '')
            replacement = step.params.get('replacement', '')
            col = step.columns[0] if step.columns else 'Col'
            lines.append(f'-- Regex replace (syntaxe PostgreSQL)')
            lines.append(
                f'UPDATE {table} SET "{col}" = REGEXP_REPLACE("{col}", \'{pattern}\', \'{replacement}\', \'g\');'
            )

        elif step.step_type == StepType.NORMALIZE_CASE:
            mode = step.params.get('mode', 'lower')
            func = 'LOWER' if mode == 'lower' else ('UPPER' if mode == 'upper' else 'INITCAP')
            for col in step.columns:
                lines.append(f'UPDATE {table} SET "{col}" = {func}("{col}");')

        elif step.step_type == StepType.NORMALIZE_WHITESPACE:
            lines.append(f'-- Normaliser les espaces')
            for col in recipe.header:
                lines.append(f'UPDATE {table} SET "{col}" = TRIM("{col}");')

        elif step.step_type == StepType.CLEAN_MASKED:
            lines.append(f'-- Nettoyer les valeurs masquees')
            for col in recipe.header:
                lines.append(
                    f'UPDATE {table} SET "{col}" = NULL '
                    f'WHERE "{col}" IN (\'N/A\', \'NULL\', \'#REF!\', \'-\', \'??\', \'n/a\', \'null\');'
                )

        else:
            lines.append(f'-- {step.step_type.name} : a adapter manuellement')

        lines.append('')

    lines.append(f'-- Resultat final')
    lines.append(f'SELECT * FROM {table};')

    return '\n'.join(lines)


def export_to_dax(recipe: Recipe) -> str:
    """Exporte en DAX (colonnes calculees Power BI)."""
    lines = [
        '// ══════════════════════════════════════════════════════',
        f'// Recette DAX generee par CSV Traitement - {recipe.created_at[:10]}',
        f'// Source : {recipe.source_file}',
        '// Note : colonnes calculees a ajouter dans Power BI',
        '// ══════════════════════════════════════════════════════',
        '',
    ]

    for step in recipe.steps:
        if step.step_type == StepType.LOAD_FILE:
            continue

        lines.append(f'// {step.description}')

        if step.step_type == StepType.FIX_DATES:
            for col in recipe.header:
                safe = _safe_col_name(col)
                lines.append(f'{safe}_Clean =')
                lines.append(f'    IF(')
                lines.append(f'        [{col}] = "00.00.0000" || [{col}] = "00/00/0000",')
                lines.append(f'        BLANK(),')
                lines.append(f'        [{col}]')
                lines.append(f'    )')
                lines.append(f'')

        elif step.step_type == StepType.FIX_DECIMALS:
            lines.append(f'// Conversion decimale FR -> nombre')
            for col in recipe.header:
                safe = _safe_col_name(col)
                lines.append(f'{safe}_Num =')
                lines.append(f'    VAR rawVal = [{col}]')
                lines.append(f'    VAR cleaned = SUBSTITUTE(SUBSTITUTE(rawVal, ".", ""), ",", ".")')
                lines.append(f'    RETURN IF(ISERROR(VALUE(cleaned)), rawVal, VALUE(cleaned))')
                lines.append(f'')

        elif step.step_type == StepType.ANONYMIZE:
            rules = step.params.get('rules', [])
            for rule in rules:
                col_idx = rule.get('column_index', 0)
                col_name = recipe.header[col_idx] if col_idx < len(recipe.header) else f'col_{col_idx}'
                safe = _safe_col_name(col_name)
                lines.append(f'{safe}_Masked =')
                lines.append(f'    IF(')
                lines.append(f'        LEN([{col_name}]) > 4,')
                lines.append(f'        LEFT([{col_name}], 2) & REPT("*", LEN([{col_name}]) - 4) '
                             f'& RIGHT([{col_name}], 2),')
                lines.append(f'        "****"')
                lines.append(f'    )')
                lines.append(f'')

        elif step.step_type == StepType.NORMALIZE_CASE:
            mode = step.params.get('mode', 'lower')
            func = 'LOWER' if mode == 'lower' else 'UPPER'
            for col in step.columns:
                safe = _safe_col_name(col)
                lines.append(f'{safe}_Norm = {func}([{col}])')
                lines.append(f'')

        else:
            lines.append(f'// {step.step_type.name} : pas de traduction DAX directe')
            lines.append(f'')

    return '\n'.join(lines)


# ── Export dispatch ──

EXPORT_LANGUAGES = {
    'power_query_m': ('Power Query M', '.pq', export_to_power_query_m),
    'python': ('Python / pandas', '.py', export_to_python),
    'sql': ('SQL', '.sql', export_to_sql),
    'dax': ('DAX (Power BI)', '.dax', export_to_dax),
    'json': ('JSON (rejeu CSV Traitement)', '.json', None),  # json uses save_recipe
}


def export_recipe(recipe: Recipe, language: str, filepath: Path) -> Path:
    """Exporte une recette dans le langage choisi.

    Args:
        recipe: Recette a exporter.
        language: Cle du langage ('power_query_m', 'python', 'sql', 'dax', 'json').
        filepath: Chemin de sortie.

    Returns:
        Chemin du fichier cree.
    """
    if language == 'json':
        return save_recipe(recipe, filepath)

    if language not in EXPORT_LANGUAGES:
        raise ValueError(f"Langage inconnu : {language}. Disponibles : {list(EXPORT_LANGUAGES.keys())}")

    _, _, export_fn = EXPORT_LANGUAGES[language]
    content = export_fn(recipe)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    logger.info(f"Recette exportee en {language} : {filepath}")
    return filepath
