"""Templates de profil réutilisables en JSON.

Permet de sauvegarder et recharger des profils de colonnes
pour traiter des exports récurrents du même système (SAP, etc.)
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult, ColumnProfile
from utils.patterns import CellType
from utils.validators import ValidationRule

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path.home() / '.csvtraitement' / 'templates'


def ensure_templates_dir() -> Path:
    """Crée le dossier de templates s'il n'existe pas."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    return TEMPLATES_DIR


def save_template(
    detection: DetectionResult,
    name: str,
    description: str = "",
    validation_rules: Optional[list[ValidationRule]] = None
) -> Path:
    """Sauvegarde un profil de détection comme template JSON.

    Args:
        detection: Résultat de détection à sauvegarder.
        name: Nom du template (utilisé comme nom de fichier).
        description: Description libre du template.
        validation_rules: Règles de validation custom à inclure.

    Returns:
        Chemin du fichier template créé.
    """
    ensure_templates_dir()

    template = {
        'meta': {
            'name': name,
            'description': description,
            'created_at': datetime.now().isoformat(),
            'source_file': str(detection.filepath.name),
            'version': '1.0',
        },
        'structure': {
            'separator': detection.separator,
            'expected_columns': detection.expected_columns,
            'encoding': detection.encoding,
            'decimal_format': detection.decimal_format,
        },
        'header': [h.strip() for h in detection.header],
        'columns': [],
        'validation_rules': [],
    }

    for profile in detection.column_profiles:
        col_data = {
            'index': profile.index,
            'header': detection.header[profile.index].strip() if profile.index < len(detection.header) else '',
            'dominant_type': profile.dominant_type.name,
            'dominant_category': profile.dominant_category,
            'confidence': profile.confidence,
            'empty_ratio': profile.empty_ratio,
            'decimal_format': profile.decimal_format,
            'sample_values': profile.sample_values[:5],
            'type_distribution': {k.name: v for k, v in profile.type_distribution.items()},
        }
        template['columns'].append(col_data)

    if validation_rules:
        for rule in validation_rules:
            rule_data = {
                'column_index': rule.column_index,
                'column_name': rule.column_name,
                'required': rule.required,
                'allowed_types': [t.name for t in rule.allowed_types] if rule.allowed_types else [],
                'min_value': rule.min_value,
                'max_value': rule.max_value,
                'regex_pattern': rule.regex_pattern,
                'allowed_values': list(rule.allowed_values) if rule.allowed_values else None,
                'min_length': rule.min_length,
                'max_length': rule.max_length,
                'custom_message': rule.custom_message,
            }
            template['validation_rules'].append(rule_data)

    # Sauvegarder
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in name)
    filepath = TEMPLATES_DIR / f"{safe_name}.json"

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    logger.info(f"Template sauvegardé : {filepath}")
    return filepath


def load_template(filepath: Path) -> dict:
    """Charge un template JSON.

    Args:
        filepath: Chemin vers le fichier template.

    Returns:
        Dictionnaire du template.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        template = json.load(f)

    logger.info(f"Template chargé : {template['meta']['name']}")
    return template


def template_to_profiles(template: dict) -> list[ColumnProfile]:
    """Reconstruit les ColumnProfile depuis un template.

    Args:
        template: Dictionnaire template chargé.

    Returns:
        Liste de ColumnProfile.
    """
    profiles = []
    for col_data in template['columns']:
        profile = ColumnProfile(index=col_data['index'])
        profile.dominant_type = CellType[col_data['dominant_type']]
        profile.dominant_category = col_data['dominant_category']
        profile.empty_ratio = col_data.get('empty_ratio', 0.0)
        profile.decimal_format = col_data.get('decimal_format')
        profile.sample_values = col_data.get('sample_values', [])

        # Reconstruire type_distribution
        for type_name, count in col_data.get('type_distribution', {}).items():
            try:
                cell_type = CellType[type_name]
                profile.type_distribution[cell_type] = count
            except KeyError:
                pass

        profiles.append(profile)

    return profiles


def template_to_rules(template: dict) -> list[ValidationRule]:
    """Reconstruit les ValidationRule depuis un template.

    Args:
        template: Dictionnaire template chargé.

    Returns:
        Liste de ValidationRule.
    """
    rules = []
    for rule_data in template.get('validation_rules', []):
        allowed_types = []
        for type_name in rule_data.get('allowed_types', []):
            try:
                allowed_types.append(CellType[type_name])
            except KeyError:
                pass

        allowed_values = set(rule_data['allowed_values']) if rule_data.get('allowed_values') else None

        rule = ValidationRule(
            column_index=rule_data['column_index'],
            column_name=rule_data.get('column_name', ''),
            required=rule_data.get('required', False),
            allowed_types=allowed_types,
            min_value=rule_data.get('min_value'),
            max_value=rule_data.get('max_value'),
            regex_pattern=rule_data.get('regex_pattern'),
            allowed_values=allowed_values,
            min_length=rule_data.get('min_length'),
            max_length=rule_data.get('max_length'),
            custom_message=rule_data.get('custom_message', ''),
        )
        rules.append(rule)

    return rules


def list_templates() -> list[dict]:
    """Liste tous les templates disponibles.

    Returns:
        Liste de métadonnées des templates.
    """
    ensure_templates_dir()
    templates = []

    for f in sorted(TEMPLATES_DIR.glob('*.json')):
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            templates.append({
                'filepath': f,
                'name': data['meta']['name'],
                'description': data['meta'].get('description', ''),
                'created_at': data['meta'].get('created_at', ''),
                'columns': data['structure']['expected_columns'],
                'separator': data['structure']['separator'],
            })
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Template invalide ignoré : {f} ({e})")

    return templates


def delete_template(filepath: Path) -> bool:
    """Supprime un template."""
    try:
        filepath.unlink()
        logger.info(f"Template supprimé : {filepath}")
        return True
    except OSError as e:
        logger.error(f"Erreur suppression template : {e}")
        return False
