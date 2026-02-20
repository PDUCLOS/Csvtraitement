"""Suggestions automatiques de regles de validation.

Analyse le profiling d'un fichier et propose des regles de validation
intelligentes basees sur les patterns detectes dans les donnees.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from core.detector import DetectionResult, ColumnProfile
from core.quality import QualityReport, ColumnQuality
from core.statistics import FileStats, ColumnStats
from utils.patterns import CellType

logger = logging.getLogger(__name__)


class SuggestionPriority(Enum):
    """Priorite de la suggestion."""
    HIGH = auto()     # Probleme majeur
    MEDIUM = auto()   # Amelioration recommandee
    LOW = auto()      # Optionnel / cosmetique


class SuggestionCategory(Enum):
    """Categorie de la suggestion."""
    FORMAT = auto()      # Incoherence de format
    MISSING = auto()     # Donnees manquantes
    OUTLIER = auto()     # Valeurs aberrantes
    DUPLICATE = auto()   # Doublons
    TYPE = auto()        # Melange de types
    NORMALIZE = auto()   # Normalisation necessaire
    VALIDATE = auto()    # Validation de contenu
    SECURITY = auto()    # Donnees sensibles


@dataclass
class Suggestion:
    """Une suggestion de regle de validation."""
    category: SuggestionCategory
    priority: SuggestionPriority
    column_index: int
    column_name: str
    message: str
    detail: str
    action: str              # Action suggere (ex: "normalize_dates")
    params: dict = field(default_factory=dict)
    icon: str = ""

    @property
    def priority_label(self) -> str:
        return {
            SuggestionPriority.HIGH: 'Haute',
            SuggestionPriority.MEDIUM: 'Moyenne',
            SuggestionPriority.LOW: 'Basse',
        }.get(self.priority, '?')


def generate_suggestions(
    detection: DetectionResult,
    quality: Optional[QualityReport] = None,
    file_stats: Optional[FileStats] = None,
) -> list[Suggestion]:
    """Genere des suggestions de validation basees sur l'analyse du fichier.

    Args:
        detection: Resultat de detection.
        quality: Rapport qualite (optionnel, enrichit les suggestions).
        file_stats: Statistiques (optionnel).

    Returns:
        Liste de suggestions triees par priorite.
    """
    suggestions = []
    n_cols = detection.expected_columns
    header = detection.header

    # ── Suggestions par colonne ──
    for p in detection.column_profiles:
        col_name = header[p.index].strip() if p.index < len(header) else f'Col {p.index}'
        col_lower = col_name.lower()

        # 1. Colonnes avec beaucoup de vides
        if p.empty_ratio > 0.5:
            suggestions.append(Suggestion(
                category=SuggestionCategory.MISSING,
                priority=SuggestionPriority.HIGH if p.empty_ratio > 0.8 else SuggestionPriority.MEDIUM,
                column_index=p.index,
                column_name=col_name,
                message=f"Colonne [{col_name}] : {p.empty_ratio:.0%} de valeurs vides",
                detail=f"Cette colonne est tres peu remplie. Verifier si elle est necessaire ou si les valeurs manquent.",
                action="check_required",
                params={'threshold': p.empty_ratio},
                icon="\u26a0",
            ))

        # 2. Melange de types
        non_empty_types = {k: v for k, v in p.type_distribution.items()
                          if k not in (CellType.EMPTY, CellType.MASKED_EMPTY)}
        if len(non_empty_types) > 1:
            total = sum(non_empty_types.values())
            dominant_pct = max(non_empty_types.values()) / total if total > 0 else 0
            if dominant_pct < 0.9:  # Moins de 90% du type dominant
                type_str = ', '.join(f"{t.name}: {c}" for t, c in non_empty_types.items())
                suggestions.append(Suggestion(
                    category=SuggestionCategory.TYPE,
                    priority=SuggestionPriority.HIGH if dominant_pct < 0.7 else SuggestionPriority.MEDIUM,
                    column_index=p.index,
                    column_name=col_name,
                    message=f"Colonne [{col_name}] : melange de types ({type_str})",
                    detail=f"Le type dominant ne represente que {dominant_pct:.0%}. Normaliser les valeurs.",
                    action="normalize_types",
                    params={'type_distribution': {t.name: c for t, c in non_empty_types.items()}},
                    icon="\u2753",
                ))

        # 3. Detection de donnees sensibles (email, telephone, IBAN)
        if p.sample_values:
            sensitive = _detect_sensitive_patterns(p.sample_values, col_lower)
            if sensitive:
                suggestions.append(Suggestion(
                    category=SuggestionCategory.SECURITY,
                    priority=SuggestionPriority.HIGH,
                    column_index=p.index,
                    column_name=col_name,
                    message=f"Colonne [{col_name}] : donnees sensibles detectees ({sensitive})",
                    detail=f"Cette colonne semble contenir des {sensitive}. Envisager l'anonymisation.",
                    action="anonymize",
                    params={'sensitive_type': sensitive},
                    icon="\U0001f512",
                ))

        # 4. Dates invalides
        if p.dominant_type == CellType.DATE_INVALID:
            suggestions.append(Suggestion(
                category=SuggestionCategory.FORMAT,
                priority=SuggestionPriority.HIGH,
                column_index=p.index,
                column_name=col_name,
                message=f"Colonne [{col_name}] : dates invalides detectees",
                detail="Des dates au format invalide (00.00.0000) ont ete detectees. Appliquer la correction.",
                action="fix_dates",
                icon="\U0001f4c5",
            ))

        # 5. Format decimal mixte
        if p.dominant_type == CellType.DECIMAL and p.decimal_format:
            # Check for mixed formats via quality report
            if quality:
                for cq in quality.column_scores:
                    if cq.index == p.index and cq.format_mix and len(cq.format_mix) > 1:
                        fmts = ', '.join(f"{k}: {v}" for k, v in cq.format_mix.items())
                        suggestions.append(Suggestion(
                            category=SuggestionCategory.FORMAT,
                            priority=SuggestionPriority.HIGH,
                            column_index=p.index,
                            column_name=col_name,
                            message=f"Colonne [{col_name}] : formats decimaux mixtes ({fmts})",
                            detail="Normaliser en un format unique (FR ou US).",
                            action="fix_decimals",
                            params={'formats': cq.format_mix},
                            icon="\U0001f522",
                        ))

        # 6. Suggestions de validation par nom de colonne
        name_suggestions = _suggest_by_column_name(p.index, col_name, col_lower, p)
        suggestions.extend(name_suggestions)

    # ── Suggestions globales ──

    # Conformite basse
    if detection.conformity_ratio < 0.95:
        non_conform = int(detection.total_lines * (1 - detection.conformity_ratio))
        suggestions.append(Suggestion(
            category=SuggestionCategory.FORMAT,
            priority=SuggestionPriority.HIGH if detection.conformity_ratio < 0.8 else SuggestionPriority.MEDIUM,
            column_index=-1,
            column_name="(global)",
            message=f"Conformite structurelle : {detection.conformity_ratio:.1%} ({non_conform:,} lignes non conformes)",
            detail="Des lignes ont un nombre de colonnes different de l'attendu. Realigner.",
            action="fix_shifts",
            icon="\u26a1",
        ))

    # Qualite globale basse
    if quality and quality.overall_score < 60:
        suggestions.append(Suggestion(
            category=SuggestionCategory.VALIDATE,
            priority=SuggestionPriority.HIGH,
            column_index=-1,
            column_name="(global)",
            message=f"Score de qualite bas : {quality.overall_score:.0f}/100 ({quality.overall_grade})",
            detail="Le fichier necessite un nettoyage approfondi.",
            action="full_cleanup",
            icon="\U0001f6a8",
        ))

    # Doublons
    if quality and quality.duplicate_rate > 1:
        suggestions.append(Suggestion(
            category=SuggestionCategory.DUPLICATE,
            priority=SuggestionPriority.MEDIUM if quality.duplicate_rate < 5 else SuggestionPriority.HIGH,
            column_index=-1,
            column_name="(global)",
            message=f"Doublons detectes : {quality.duplicate_rate:.1f}% de lignes dupliquees",
            detail="Lancer la deduplication pour identifier et supprimer les doublons.",
            action="dedup",
            params={'rate': quality.duplicate_rate},
            icon="\U0001f517",
        ))

    # Outliers par colonne (depuis stats)
    if file_stats:
        for cs in file_stats.column_stats:
            if cs.outliers_count > 0:
                col_name = cs.header
                suggestions.append(Suggestion(
                    category=SuggestionCategory.OUTLIER,
                    priority=SuggestionPriority.MEDIUM if cs.outliers_count < 10 else SuggestionPriority.HIGH,
                    column_index=cs.index,
                    column_name=col_name,
                    message=f"Colonne [{col_name}] : {cs.outliers_count} outliers detectes",
                    detail=f"Valeurs hors de la plage normale (IQR). "
                           f"Min: {cs.numeric_min}, Max: {cs.numeric_max}, "
                           f"Moyenne: {cs.numeric_mean:.2f}" if cs.numeric_mean else "",
                    action="review_outliers",
                    params={'count': cs.outliers_count},
                    icon="\U0001f4c8",
                ))

    # Trier par priorite puis par colonne
    priority_order = {SuggestionPriority.HIGH: 0, SuggestionPriority.MEDIUM: 1, SuggestionPriority.LOW: 2}
    suggestions.sort(key=lambda s: (priority_order.get(s.priority, 9), s.column_index))

    logger.info(f"Suggestions generees : {len(suggestions)}")
    return suggestions


def _detect_sensitive_patterns(sample_values: list[str], col_lower: str) -> Optional[str]:
    """Detecte si les valeurs contiennent des donnees sensibles."""
    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    phone_pattern = re.compile(r'^[\+]?[\d\s\.\-\(\)]{8,15}$')
    iban_pattern = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$')
    ssn_pattern = re.compile(r'^\d[\s\.]?\d{2}[\s\.]?\d{2}[\s\.]?\d{2}[\s\.]?\d{3}[\s\.]?\d{3}[\s\.]?\d{2}$')

    matches = {'email': 0, 'telephone': 0, 'IBAN': 0, 'n_secu': 0}

    for val in sample_values[:20]:
        v = val.strip()
        if not v:
            continue
        if email_pattern.match(v):
            matches['email'] += 1
        elif phone_pattern.match(v) and any(c.isdigit() for c in v):
            matches['telephone'] += 1
        elif iban_pattern.match(v.replace(' ', '')):
            matches['IBAN'] += 1
        elif ssn_pattern.match(v):
            matches['n_secu'] += 1

    # Detection par nom de colonne aussi
    if any(k in col_lower for k in ['email', 'courriel', 'mail']):
        matches['email'] += 5
    if any(k in col_lower for k in ['tel', 'phone', 'mobile', 'fax']):
        matches['telephone'] += 5
    if 'iban' in col_lower:
        matches['IBAN'] += 5
    if any(k in col_lower for k in ['secu', 'ssn', 'nir']):
        matches['n_secu'] += 5

    best = max(matches, key=matches.get)
    if matches[best] >= 2:
        return best
    return None


def _suggest_by_column_name(
    col_idx: int, col_name: str, col_lower: str, profile: ColumnProfile
) -> list[Suggestion]:
    """Propose des validations basees sur le nom de colonne."""
    suggestions = []

    # Code postal
    if any(k in col_lower for k in ['code_postal', 'cp', 'zip', 'postal']):
        if profile.dominant_type in (CellType.INTEGER, CellType.TEXT_SHORT):
            suggestions.append(Suggestion(
                category=SuggestionCategory.VALIDATE,
                priority=SuggestionPriority.LOW,
                column_index=col_idx,
                column_name=col_name,
                message=f"Colonne [{col_name}] semble etre un code postal",
                detail="Valider le format (5 chiffres pour la France, regex personnalisable).",
                action="validate_regex",
                params={'pattern': r'^\d{5}$', 'description': 'Code postal FR'},
                icon="\U0001f4ee",
            ))

    # Pays
    if any(k in col_lower for k in ['pays', 'country', 'land']):
        suggestions.append(Suggestion(
            category=SuggestionCategory.NORMALIZE,
            priority=SuggestionPriority.LOW,
            column_index=col_idx,
            column_name=col_name,
            message=f"Colonne [{col_name}] semble etre un pays",
            detail="Normaliser en code ISO 3166-1 (FR, DE, US...) ou en nom standard.",
            action="normalize_case",
            params={'mode': 'upper'},
            icon="\U0001f30d",
        ))

    # Montant / prix
    if any(k in col_lower for k in ['montant', 'amount', 'prix', 'price', 'total', 'ht', 'ttc']):
        if profile.dominant_type in (CellType.DECIMAL, CellType.INTEGER):
            suggestions.append(Suggestion(
                category=SuggestionCategory.VALIDATE,
                priority=SuggestionPriority.LOW,
                column_index=col_idx,
                column_name=col_name,
                message=f"Colonne [{col_name}] semble etre un montant",
                detail="Verifier les valeurs negatives et les outliers. Normaliser le format decimal.",
                action="fix_decimals",
                icon="\U0001f4b0",
            ))

    # Identifiant unique
    if any(k in col_lower for k in ['id', 'identifiant', 'reference', 'ref', 'code', 'numero', 'num']):
        suggestions.append(Suggestion(
            category=SuggestionCategory.VALIDATE,
            priority=SuggestionPriority.LOW,
            column_index=col_idx,
            column_name=col_name,
            message=f"Colonne [{col_name}] semble etre un identifiant",
            detail="Verifier l'unicite des valeurs et le format.",
            action="check_unique",
            icon="\U0001f511",
        ))

    return suggestions


def format_suggestions_text(suggestions: list[Suggestion]) -> str:
    """Formate les suggestions pour affichage texte (CLI)."""
    if not suggestions:
        return "Aucune suggestion."

    lines = []
    lines.append(f"\n\U0001f4a1 {len(suggestions)} suggestion(s) de validation :")
    lines.append("\u2500" * 60)

    current_priority = None
    for s in suggestions:
        if s.priority != current_priority:
            current_priority = s.priority
            label = {
                SuggestionPriority.HIGH: '\U0001f534 HAUTE PRIORITE',
                SuggestionPriority.MEDIUM: '\U0001f7e0 PRIORITE MOYENNE',
                SuggestionPriority.LOW: '\U0001f7e2 BASSE PRIORITE',
            }.get(s.priority, '')
            lines.append(f"\n{label}")

        lines.append(f"  {s.icon} {s.message}")
        if s.detail:
            lines.append(f"      {s.detail}")

    return '\n'.join(lines)
