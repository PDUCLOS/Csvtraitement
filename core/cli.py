"""Mode CLI et traitement batch multi-fichiers.

Usage:
    python main.py cli --file data.csv                  # Analyser un fichier
    python main.py cli --file data.csv --fix --output out.xlsx  # Corriger et exporter
    python main.py cli --batch /dossier/ --pattern "*.csv"      # Batch
    python main.py cli --file data.csv --template mon_template  # Avec template
    python main.py cli --file data.csv --stats                  # Statistiques
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from core.detector import run_detection, DetectionResult
from core.analyzer import analyze_file, AnalysisResult
from core.fixer import apply_fixes, FixResult
from core.exporter import export_xlsx, export_csv_clean
from core.statistics import compute_statistics, FileStats

logger = logging.getLogger(__name__)


def setup_cli_parser(subparsers) -> argparse.ArgumentParser:
    """Configure le sous-parseur CLI."""
    cli_parser = subparsers.add_parser('cli', help='Mode ligne de commande')

    # Fichier unique ou batch
    group = cli_parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--file', '-f', type=str, help='Fichier CSV à traiter')
    group.add_argument('--batch', '-b', type=str, help='Dossier pour traitement batch')

    # Options batch
    cli_parser.add_argument('--pattern', type=str, default='*.csv',
                           help='Pattern glob pour le batch (défaut: *.csv)')

    # Actions
    cli_parser.add_argument('--fix', action='store_true',
                           help='Appliquer les corrections automatiquement')
    cli_parser.add_argument('--output', '-o', type=str,
                           help='Fichier/dossier de sortie')
    cli_parser.add_argument('--stats', action='store_true',
                           help='Afficher les statistiques détaillées')
    cli_parser.add_argument('--template', '-t', type=str,
                           help='Nom du template à utiliser')
    cli_parser.add_argument('--save-template', type=str,
                           help='Sauvegarder le profil comme template')

    # Options de correction
    cli_parser.add_argument('--no-fix-dates', action='store_true',
                           help='Ne pas corriger les dates')
    cli_parser.add_argument('--no-fix-decimals', action='store_true',
                           help='Ne pas normaliser les décimales')
    cli_parser.add_argument('--no-fix-shifts', action='store_true',
                           help='Ne pas réaligner les colonnes')

    # Format de sortie
    cli_parser.add_argument('--format', choices=['xlsx', 'csv'], default='xlsx',
                           help='Format de sortie (défaut: xlsx)')
    cli_parser.add_argument('--report', type=str,
                           help='Chemin pour le rapport JSON')

    # Verbosité
    cli_parser.add_argument('--quiet', '-q', action='store_true',
                           help='Mode silencieux')

    return cli_parser


def run_cli(args: argparse.Namespace):
    """Exécute le mode CLI."""
    if args.batch:
        run_batch(args)
    else:
        run_single(args)


def run_single(args: argparse.Namespace) -> dict:
    """Traite un fichier unique en mode CLI.

    Returns:
        Dictionnaire de rapport.
    """
    filepath = Path(args.file)
    if not filepath.exists():
        print(f"❌ Fichier non trouvé : {filepath}")
        sys.exit(1)

    quiet = getattr(args, 'quiet', False)
    report = {}

    # ── Détection ──
    if not quiet:
        print(f"\n📂 Fichier : {filepath.name}")
        print("─" * 50)
        print("🔍 Détection en cours...")

    detection = run_detection(filepath)
    report['detection'] = {
        'file': str(filepath),
        'encoding': detection.encoding,
        'separator': repr(detection.separator),
        'total_lines': detection.total_lines,
        'expected_columns': detection.expected_columns,
        'header': [h.strip() for h in detection.header],
        'decimal_format': detection.decimal_format,
        'conformity_ratio': detection.conformity_ratio,
    }

    if not quiet:
        sep_display = repr(detection.separator).replace("'", "")
        print(f"  Encodage       : {detection.encoding}")
        print(f"  Séparateur     : {sep_display}")
        print(f"  Lignes         : {detection.total_lines:,}")
        print(f"  Colonnes       : {detection.expected_columns}")
        print(f"  Format décimal : {detection.decimal_format or 'Non détecté'}")
        print(f"  Conformité     : {detection.conformity_ratio:.1%}")

        # Profils colonnes
        print(f"\n📋 Profils colonnes :")
        for p in detection.column_profiles:
            header = detection.header[p.index].strip() if p.index < len(detection.header) else f'Col {p.index}'
            print(f"  [{p.index}] {header:20s} → {p.dominant_type.name:12s} ({p.confidence:.0%})")

    # ── Statistiques ──
    if getattr(args, 'stats', False):
        if not quiet:
            print("\n📊 Calcul des statistiques...")
        file_stats = compute_statistics(detection)
        report['statistics'] = _stats_to_dict(file_stats)

        if not quiet:
            _print_stats(file_stats)

    # ── Analyse ──
    if not quiet:
        print("\n🔍 Analyse des anomalies...")

    analysis = analyze_file(detection)
    report['analysis'] = {
        'conforming_lines': analysis.conforming_lines,
        'non_conforming_lines': analysis.non_conforming_lines,
        'conformity_percentage': analysis.conformity_percentage,
        'shifts': len(analysis.shifts),
        'type_mismatches': len(analysis.type_mismatches),
        'shift_columns': analysis.shift_columns_summary,
    }

    if not quiet:
        style = '✅' if analysis.conformity_percentage > 95 else ('⚠️' if analysis.conformity_percentage > 80 else '❌')
        print(f"  {style} Conformité : {analysis.conformity_percentage:.1f}%")
        print(f"  Lignes conformes     : {analysis.conforming_lines:,}")
        print(f"  Lignes non conformes : {analysis.non_conforming_lines:,}")
        print(f"  Décalages            : {len(analysis.shifts):,}")
        print(f"  Erreurs de type      : {len(analysis.type_mismatches):,}")

        if analysis.shift_columns_summary:
            print(f"\n  Décalages par colonne :")
            for col, count in sorted(analysis.shift_columns_summary.items(), key=lambda x: -x[1])[:5]:
                print(f"    Col {col} : {count:,} occurrences")

    # ── Template ──
    if getattr(args, 'save_template', None):
        from utils.templates import save_template
        tpl_path = save_template(detection, args.save_template)
        if not quiet:
            print(f"\n💾 Template sauvegardé : {tpl_path}")

    # ── Corrections ──
    fix_result = None
    if getattr(args, 'fix', False):
        if not quiet:
            print("\n🔧 Application des corrections...")

        fix_result = apply_fixes(
            detection, analysis,
            fix_dates=not getattr(args, 'no_fix_dates', False),
            fix_decimals=not getattr(args, 'no_fix_decimals', False),
            fix_shifts=not getattr(args, 'no_fix_shifts', False),
        )

        report['corrections'] = fix_result.summary

        if not quiet:
            print(f"  Total corrections  : {fix_result.total_corrections:,}")
            print(f"  Dates corrigées    : {fix_result.date_fixes:,}")
            print(f"  Décimales normalisées : {fix_result.decimal_fixes:,}")
            print(f"  Réalignements      : {fix_result.realignment_fixes:,}")

        # ── Export ──
        output = getattr(args, 'output', None)
        if output:
            output_path = Path(output)
            fmt = getattr(args, 'format', 'xlsx')

            if not quiet:
                print(f"\n💾 Export {fmt.upper()} → {output_path}")

            if fmt == 'xlsx':
                export_xlsx(detection, analysis, fix_result, output_path)
            else:
                export_csv_clean(detection, fix_result, output_path)

            if not quiet:
                print(f"  ✅ Fichier exporté : {output_path}")

    # ── Rapport JSON ──
    report_path = getattr(args, 'report', None)
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        if not quiet:
            print(f"\n📄 Rapport JSON : {report_path}")

    if not quiet:
        print("\n" + "─" * 50)
        print("✅ Traitement terminé")

    return report


def run_batch(args: argparse.Namespace):
    """Traite un dossier de fichiers en batch."""
    batch_dir = Path(args.batch)
    if not batch_dir.is_dir():
        print(f"❌ Dossier non trouvé : {batch_dir}")
        sys.exit(1)

    pattern = getattr(args, 'pattern', '*.csv')
    files = sorted(batch_dir.glob(pattern))

    if not files:
        print(f"❌ Aucun fichier '{pattern}' trouvé dans {batch_dir}")
        sys.exit(1)

    output_dir = Path(args.output) if args.output else batch_dir / 'output'
    output_dir.mkdir(parents=True, exist_ok=True)

    quiet = getattr(args, 'quiet', False)
    if not quiet:
        print(f"\n📦 Traitement batch : {len(files)} fichiers")
        print(f"   Dossier source  : {batch_dir}")
        print(f"   Dossier sortie  : {output_dir}")
        print("═" * 50)

    reports = []
    success = 0
    errors = 0

    for i, filepath in enumerate(files, 1):
        if not quiet:
            print(f"\n[{i}/{len(files)}] {filepath.name}")

        try:
            # Configurer la sortie pour ce fichier
            fmt = getattr(args, 'format', 'xlsx')
            ext = '.xlsx' if fmt == 'xlsx' else '.csv'
            args.file = str(filepath)
            args.output = str(output_dir / (filepath.stem + '_corrigé' + ext)) if args.fix else None

            report = run_single(args)
            reports.append(report)
            success += 1

        except Exception as e:
            logger.exception(f"Erreur sur {filepath}")
            if not quiet:
                print(f"  ❌ Erreur : {e}")
            errors += 1
            reports.append({'file': str(filepath), 'error': str(e)})

    if not quiet:
        print(f"\n{'═' * 50}")
        print(f"📊 Résumé batch : {success} réussis, {errors} erreurs sur {len(files)} fichiers")

    # Rapport batch global
    report_path = getattr(args, 'report', None)
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({
                'batch': True,
                'total_files': len(files),
                'success': success,
                'errors': errors,
                'reports': reports,
            }, f, indent=2, ensure_ascii=False, default=str)


def _print_stats(stats: FileStats):
    """Affiche les statistiques dans le terminal."""
    print(f"  Lignes totales   : {stats.total_rows:,}")
    print(f"  Lignes vides     : {stats.empty_rows:,}")
    print(f"  Doublons         : {stats.duplicate_rows:,}")
    print(f"  Taux remplissage : {stats.overall_fill_rate:.1f}%")

    for cs in stats.column_stats:
        print(f"\n  📊 [{cs.index}] {cs.header}")
        print(f"     Type: {cs.cell_type.name} | Rempli: {cs.completeness:.1f}% | Uniques: {cs.unique_count:,}")

        if cs.numeric_mean is not None:
            print(f"     Min: {cs.numeric_min:,.2f} | Max: {cs.numeric_max:,.2f} | Moy: {cs.numeric_mean:,.2f}")
            if cs.outliers_count > 0:
                print(f"     ⚠️ Outliers: {cs.outliers_count}")

        if cs.text_avg_length is not None:
            print(f"     Longueur: {cs.text_min_length}-{cs.text_max_length} (moy: {cs.text_avg_length:.0f})")

        if cs.date_min:
            print(f"     Dates: {cs.date_min} → {cs.date_max}")

        if cs.top_values:
            top3 = ', '.join(f"'{v}' ({c})" for v, c in cs.top_values[:3])
            print(f"     Top valeurs: {top3}")


def _stats_to_dict(stats: FileStats) -> dict:
    """Convertit les stats en dict sérialisable."""
    return {
        'total_rows': stats.total_rows,
        'total_columns': stats.total_columns,
        'duplicate_rows': stats.duplicate_rows,
        'empty_rows': stats.empty_rows,
        'overall_fill_rate': stats.overall_fill_rate,
        'columns': [
            {
                'index': cs.index,
                'header': cs.header,
                'type': cs.cell_type.name,
                'completeness': cs.completeness,
                'unique_count': cs.unique_count,
                'numeric_min': cs.numeric_min,
                'numeric_max': cs.numeric_max,
                'numeric_mean': cs.numeric_mean,
                'outliers_count': cs.outliers_count,
                'top_values': cs.top_values[:5],
            }
            for cs in stats.column_stats
        ],
    }
