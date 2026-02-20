"""Fusion de fichiers CSV avec contrôle de compatibilité.

Vérifie avant fusion :
- Nombre de colonnes identique
- Noms de colonnes identiques (ou mapping fourni)
- Détection des colonnes permutées
- Gestion des colonnes manquantes/supplémentaires

Fonctionnalités :
- Fusion verticale (append) de N fichiers
- Réalignement automatique des colonnes par nom
- Rapport de compatibilité avant fusion
- Suppression de lignes vides / en erreur
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult, run_detection
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)


@dataclass
class ColumnMapping:
    """Correspondance de colonnes entre deux fichiers."""
    source_index: int
    source_name: str
    target_index: int
    target_name: str
    match_type: str = 'exact'  # 'exact', 'case_insensitive', 'position', 'manual', 'missing'


@dataclass
class CompatibilityIssue:
    """Problème de compatibilité détecté."""
    severity: str            # 'error', 'warning', 'info'
    message: str
    details: str = ""


@dataclass
class CompatibilityReport:
    """Rapport de compatibilité entre fichiers."""
    compatible: bool = True
    issues: list[CompatibilityIssue] = field(default_factory=list)
    column_mappings: list[ColumnMapping] = field(default_factory=list)
    swapped_columns: list[tuple[int, int, str, str]] = field(default_factory=list)
    missing_columns: list[tuple[int, str]] = field(default_factory=list)
    extra_columns: list[tuple[int, str]] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == 'error' for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == 'warning' for i in self.issues)


@dataclass
class MergeResult:
    """Résultat de la fusion."""
    total_files: int = 0
    total_rows: int = 0
    rows_per_file: list[int] = field(default_factory=list)
    skipped_empty_rows: int = 0
    skipped_error_rows: int = 0
    column_realignments: int = 0
    output_headers: list[str] = field(default_factory=list)
    merged_rows: list[list[str]] = field(default_factory=list)


def check_compatibility(
    reference: DetectionResult,
    target: DetectionResult
) -> CompatibilityReport:
    """Vérifie la compatibilité entre deux fichiers pour la fusion.

    Args:
        reference: Fichier de référence (structure attendue).
        target: Fichier à fusionner.

    Returns:
        CompatibilityReport avec les problèmes détectés.
    """
    report = CompatibilityReport()
    ref_headers = [h.strip().lower() for h in reference.header]
    tgt_headers = [h.strip().lower() for h in target.header]
    ref_headers_orig = [h.strip() for h in reference.header]
    tgt_headers_orig = [h.strip() for h in target.header]

    # Vérifier le nombre de colonnes
    if reference.expected_columns != target.expected_columns:
        report.issues.append(CompatibilityIssue(
            severity='warning',
            message=f"Nombre de colonnes différent: {reference.expected_columns} vs {target.expected_columns}",
            details=f"Référence: {reference.expected_columns}, Cible: {target.expected_columns}"
        ))

    # Construire le mapping de colonnes
    tgt_used = set()
    for i, ref_name in enumerate(ref_headers):
        mapping = None

        # 1. Match exact
        if i < len(tgt_headers) and tgt_headers[i] == ref_name:
            mapping = ColumnMapping(
                source_index=i, source_name=ref_headers_orig[i],
                target_index=i, target_name=tgt_headers_orig[i],
                match_type='exact'
            )
        else:
            # 2. Chercher par nom (case insensitive) - colonne échangée ?
            for j, tgt_name in enumerate(tgt_headers):
                if j not in tgt_used and tgt_name == ref_name:
                    mapping = ColumnMapping(
                        source_index=i, source_name=ref_headers_orig[i],
                        target_index=j, target_name=tgt_headers_orig[j],
                        match_type='case_insensitive'
                    )
                    if i != j:
                        report.swapped_columns.append((i, j, ref_headers_orig[i], tgt_headers_orig[j]))
                        report.issues.append(CompatibilityIssue(
                            severity='warning',
                            message=f"Colonne '{ref_headers_orig[i]}' échangée: position {i} → {j}",
                            details=f"Réalignement automatique possible"
                        ))
                    break

        if mapping:
            report.column_mappings.append(mapping)
            tgt_used.add(mapping.target_index)
        else:
            # Colonne de référence non trouvée dans la cible
            report.missing_columns.append((i, ref_headers_orig[i]))
            report.issues.append(CompatibilityIssue(
                severity='warning',
                message=f"Colonne '{ref_headers_orig[i]}' absente du fichier cible",
                details=f"Sera remplie avec des valeurs vides"
            ))
            report.column_mappings.append(ColumnMapping(
                source_index=i, source_name=ref_headers_orig[i],
                target_index=-1, target_name='(absent)',
                match_type='missing'
            ))

    # Colonnes supplémentaires dans la cible
    for j in range(len(tgt_headers)):
        if j not in tgt_used:
            report.extra_columns.append((j, tgt_headers_orig[j]))
            report.issues.append(CompatibilityIssue(
                severity='info',
                message=f"Colonne supplémentaire dans la cible: '{tgt_headers_orig[j]}' (position {j})",
                details="Sera ignorée lors de la fusion"
            ))

    # Vérifier le séparateur
    if reference.separator != target.separator:
        report.issues.append(CompatibilityIssue(
            severity='info',
            message=f"Séparateurs différents: {repr(reference.separator)} vs {repr(target.separator)}",
            details="Géré automatiquement"
        ))

    # Vérifier l'encodage
    if reference.encoding != target.encoding:
        report.issues.append(CompatibilityIssue(
            severity='info',
            message=f"Encodages différents: {reference.encoding} vs {target.encoding}",
            details="Géré automatiquement"
        ))

    report.compatible = not report.has_errors
    return report


def merge_files(
    detections: list[DetectionResult],
    skip_empty_rows: bool = True,
    skip_error_rows: bool = False,
    realign_columns: bool = True,
    progress_callback=None
) -> MergeResult:
    """Fusionne plusieurs fichiers CSV.

    Le premier fichier sert de référence pour la structure.

    Args:
        detections: Liste des résultats de détection.
        skip_empty_rows: Ignorer les lignes entièrement vides.
        skip_error_rows: Ignorer les lignes avec un nombre incorrect de colonnes.
        realign_columns: Réaligner les colonnes par nom si nécessaire.
        progress_callback: Fonction de suivi.

    Returns:
        MergeResult avec les données fusionnées.
    """
    if not detections:
        return MergeResult()

    reference = detections[0]
    n_cols = reference.expected_columns
    result = MergeResult(
        total_files=len(detections),
        output_headers=[h.strip() for h in reference.header[:n_cols]],
    )

    total_lines = sum(d.total_lines for d in detections)
    processed = 0

    for file_idx, detection in enumerate(detections):
        file_rows = 0
        compatibility = check_compatibility(reference, detection) if file_idx > 0 else None

        # Construire la table de réalignement
        col_remap: Optional[dict[int, int]] = None
        if realign_columns and compatibility and compatibility.swapped_columns:
            col_remap = {}
            for mapping in compatibility.column_mappings:
                if mapping.target_index >= 0:
                    col_remap[mapping.source_index] = mapping.target_index

        for chunk in read_chunks(
            detection.filepath, detection.separator,
            detection.encoding, chunk_size=10_000, skip_header=True
        ):
            for cells in chunk:
                processed += 1

                # Ligne vide ?
                if skip_empty_rows and all(not c.strip() for c in cells):
                    result.skipped_empty_rows += 1
                    continue

                # Ligne en erreur ?
                if skip_error_rows and len(cells) != detection.expected_columns:
                    result.skipped_error_rows += 1
                    continue

                # Réaligner les colonnes si nécessaire
                if col_remap:
                    aligned = [''] * n_cols
                    for ref_idx, tgt_idx in col_remap.items():
                        if tgt_idx < len(cells):
                            aligned[ref_idx] = cells[tgt_idx]
                    result.column_realignments += 1
                else:
                    aligned = list(cells[:n_cols])

                # Padder si nécessaire
                while len(aligned) < n_cols:
                    aligned.append('')

                result.merged_rows.append(aligned)
                file_rows += 1

            if progress_callback:
                progress_callback(processed, total_lines,
                                  f"Fusion: fichier {file_idx + 1}/{len(detections)}")

        result.rows_per_file.append(file_rows)
        result.total_rows += file_rows

    logger.info(
        f"Fusion: {result.total_files} fichiers, {result.total_rows} lignes, "
        f"{result.skipped_empty_rows} vides ignorées, "
        f"{result.skipped_error_rows} erreurs ignorées"
    )
    return result
