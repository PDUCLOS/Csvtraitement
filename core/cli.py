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
from core.exporter import export_xlsx, export_csv_clean, export_any, CSV_SEPARATORS, EXPORT_FORMATS
from core.statistics import compute_statistics, FileStats
from core.quality import compute_quality, QualityReport
from core.dedup import find_duplicates, DedupResult
from core.comparator import compare_files, ComparisonResult, RowStatus
from core.reconciler import reconcile, ReconciliationResult, MatchStatus
from core.merger import check_compatibility, merge_files
from utils.cloud import detect_cloud_locations

logger = logging.getLogger(__name__)


def setup_cli_parser(subparsers) -> argparse.ArgumentParser:
    """Configure le sous-parseur CLI."""
    cli_parser = subparsers.add_parser('cli', help='Mode ligne de commande')

    # Fichier unique ou batch (pas requis si --merge ou --cloud)
    group = cli_parser.add_mutually_exclusive_group(required=False)
    group.add_argument('--file', '-f', type=str,
                       help='Fichier à traiter (CSV, XLSX, XLS, ODS, JSON, Parquet)')
    group.add_argument('--batch', '-b', type=str, help='Dossier pour traitement batch')

    # Sélection de feuille (Excel/ODS)
    cli_parser.add_argument('--sheet', type=str, default=None,
                           help='Nom de la feuille à lire (Excel/ODS, défaut: première)')

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
    cli_parser.add_argument('--no-fix-masked', action='store_true',
                           help='Ne pas vider les valeurs masquées (N/A, NULL, #REF!...)')
    cli_parser.add_argument('--fix-whitespace', action='store_true',
                           help='Supprimer les espaces superflus dans toutes les cellules')

    # Format de sortie
    cli_parser.add_argument('--format', choices=list(EXPORT_FORMATS.keys()), default='xlsx',
                           help='Format de sortie : xlsx, csv, json, ods, parquet (défaut: xlsx)')
    sep_choices = list(CSV_SEPARATORS.keys())
    cli_parser.add_argument('--csv-separator', type=str, default='§',
                           help=f"Séparateur pour l'export CSV (défaut: § ). "
                                f"Valeurs prédéfinies : {', '.join(sep_choices)}. "
                                f"Ou tout caractère unique.")
    cli_parser.add_argument('--report', type=str,
                           help='Chemin pour le rapport JSON')

    # Nouvelles fonctionnalités
    cli_parser.add_argument('--quality', action='store_true',
                           help='Calculer le score de qualité des données')
    cli_parser.add_argument('--dedup', action='store_true',
                           help='Détecter les doublons (exacts et fuzzy)')
    cli_parser.add_argument('--compare', type=str,
                           help='Comparer avec un second fichier CSV')
    cli_parser.add_argument('--reconcile', type=str,
                           help='Rapprocher avec un second fichier CSV')
    cli_parser.add_argument('--join-columns', type=str, default='0',
                           help='Colonnes de jointure (indices séparés par virgule, défaut: 0)')
    cli_parser.add_argument('--key-columns', type=str, default=None,
                           help='Clé primaire pour --compare (indices séparés par virgule). '
                                'Sans clé, toute la ligne sert de clé : une modification '
                                'apparaît alors comme une suppression + un ajout.')
    cli_parser.add_argument('--amount-columns', type=str, default=None,
                           help='Montants à rapprocher pour --reconcile : "col_gauche,col_droite" (ex. 5,1)')
    cli_parser.add_argument('--tolerance', type=float, default=0.01,
                           help='Tolérance absolue sur les montants rapprochés (défaut: 0.01)')
    cli_parser.add_argument('--merge', type=str, nargs='+',
                           help='Fusionner plusieurs fichiers CSV')
    cli_parser.add_argument('--cloud', action='store_true',
                           help='Lister les emplacements cloud détectés')

    # Verbosité
    cli_parser.add_argument('--quiet', '-q', action='store_true',
                           help='Mode silencieux')

    return cli_parser


def run_cli(args: argparse.Namespace):
    """Exécute le mode CLI."""
    # Commande cloud (pas besoin de fichier)
    if getattr(args, 'cloud', False):
        _run_cloud()
        return

    # Commande merge (fichiers multiples)
    if getattr(args, 'merge', None):
        _run_merge(args)
        return

    # Vérifier qu'un fichier ou batch est fourni
    if not getattr(args, 'file', None) and not getattr(args, 'batch', None):
        print("❌ Veuillez spécifier --file, --batch, --merge ou --cloud")
        sys.exit(1)

    if getattr(args, 'batch', None):
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
    sheet_name = getattr(args, 'sheet', None)
    report = {}

    # ── Détection ──
    if not quiet:
        from utils.universal_reader import get_file_format
        fmt_source = get_file_format(filepath)
        fmt_display = f" [{fmt_source.upper()}]" if fmt_source != 'csv' else ''
        print(f"\n📂 Fichier : {filepath.name}{fmt_display}")
        if sheet_name:
            print(f"   Feuille : {sheet_name}")
        print("─" * 50)
        print("🔍 Détection en cours...")

    detection = run_detection(filepath, sheet_name=sheet_name)
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

    # ── Qualité ──
    if getattr(args, 'quality', False):
        if not quiet:
            print("\n🏆 Score de qualité...")
        quality = compute_quality(detection)
        report['quality'] = {
            'overall_score': quality.overall_score,
            'overall_grade': quality.overall_grade,
            'duplicate_rate': quality.duplicate_rate,
            'empty_row_rate': quality.empty_row_rate,
            'columns': [
                {
                    'index': cq.index, 'header': cq.header,
                    'score': cq.overall_score, 'grade': cq.grade,
                    'fill': cq.fill_score, 'type_consistency': cq.type_consistency_score,
                    'outlier': cq.outlier_score, 'format': cq.format_homogeneity_score,
                    'issues': cq.issues,
                }
                for cq in quality.column_scores
            ],
        }
        if not quiet:
            _print_quality(quality)

    # ── Doublons ──
    if getattr(args, 'dedup', False):
        if not quiet:
            print("\n🔗 Recherche de doublons...")
        dedup = find_duplicates(detection, enable_fuzzy=True)
        report['dedup'] = {
            'exact_groups': dedup.exact_duplicate_groups,
            'exact_rows': dedup.exact_duplicate_rows,
            'fuzzy_groups': dedup.fuzzy_duplicate_groups,
            'fuzzy_rows': dedup.fuzzy_duplicate_rows,
            'duplicate_rate': dedup.duplicate_rate,
        }
        if not quiet:
            _print_dedup(dedup)

    # ── Comparaison ──
    compare_file = getattr(args, 'compare', None)
    if compare_file:
        if not quiet:
            print(f"\n⚡ Comparaison avec {Path(compare_file).name}...")
        detection_b = run_detection(Path(compare_file))
        key_arg = getattr(args, 'key_columns', None)
        key_cols = [int(c.strip()) for c in key_arg.split(',')] if key_arg else None
        comparison = compare_files(detection, detection_b, key_columns=key_cols)
        report['comparison'] = {
            'unchanged': comparison.unchanged,
            'added': comparison.added,
            'removed': comparison.removed,
            'modified': comparison.modified,
            'total_cell_changes': comparison.total_cell_changes,
            'similarity': comparison.similarity_percentage,
        }
        if not quiet:
            _print_comparison(comparison)

    # ── Rapprochement ──
    reconcile_file = getattr(args, 'reconcile', None)
    if reconcile_file:
        if not quiet:
            print(f"\n🔄 Rapprochement avec {Path(reconcile_file).name}...")
        detection_r = run_detection(Path(reconcile_file))
        join_cols = [int(c.strip()) for c in getattr(args, 'join_columns', '0').split(',')]
        amount_arg = getattr(args, 'amount_columns', None)
        amount_cols = tuple(int(c.strip()) for c in amount_arg.split(',')) if amount_arg else None
        if amount_cols is not None and len(amount_cols) != 2:
            print("❌ --amount-columns attend deux indices : col_gauche,col_droite")
            sys.exit(1)
        recon = reconcile(detection, detection_r, join_columns=join_cols,
                          amount_columns=amount_cols,
                          tolerance=getattr(args, 'tolerance', 0.01))
        report['reconciliation'] = recon.summary
        if not quiet:
            _print_reconciliation(recon)

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
            fix_masked=not getattr(args, 'no_fix_masked', False),
            fix_whitespace=getattr(args, 'fix_whitespace', False),
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
            csv_sep = getattr(args, 'csv_separator', '§')

            if not quiet:
                fmt_desc = EXPORT_FORMATS.get(fmt, fmt.upper())
                print(f"\n💾 Export {fmt_desc} → {output_path}")

            export_any(
                detection, analysis, fix_result, output_path,
                fmt=fmt, csv_separator=csv_sep,
            )

            if fmt == 'csv' and not quiet:
                sep_display = CSV_SEPARATORS.get(csv_sep, csv_sep)
                print(f"  Séparateur CSV : {sep_display}")

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


def _print_quality(quality: QualityReport):
    """Affiche le score de qualité dans le terminal."""
    print(f"\n  🏆 Score global : {quality.overall_score:.1f}/100 ({quality.overall_grade})")
    print(f"  Doublons : {quality.duplicate_rate:.1f}% | Lignes vides : {quality.empty_row_rate:.1f}%")

    for cq in quality.column_scores:
        grade_icon = {'A': '🟢', 'B': '🟢', 'C': '🟡', 'D': '🟠', 'F': '🔴'}.get(cq.grade, '⚪')
        print(f"  {grade_icon} [{cq.index}] {cq.header:20s} {cq.overall_score:5.1f}/100 ({cq.grade})"
              f"  rempli={cq.fill_score:.0f}% types={cq.type_consistency_score:.0f}%"
              f" outliers={cq.outlier_score:.0f}% format={cq.format_homogeneity_score:.0f}%")
        if cq.issues:
            for issue in cq.issues[:2]:
                print(f"       ⚠️ {issue}")

    if quality.summary:
        print(f"\n  Résumé:")
        for s in quality.summary:
            print(f"    • {s}")


def _print_dedup(dedup: DedupResult):
    """Affiche les doublons dans le terminal."""
    print(f"  Groupes exacts : {dedup.exact_duplicate_groups} ({dedup.exact_duplicate_rows} lignes en double)")
    print(f"  Groupes fuzzy  : {dedup.fuzzy_duplicate_groups} ({dedup.fuzzy_duplicate_rows} lignes similaires)")
    print(f"  Taux doublons  : {dedup.duplicate_rate:.1f}%")

    for g in dedup.groups[:10]:
        lines = ', '.join(str(l) for l in g.line_numbers[:5])
        print(f"    [{g.group_id}] {g.match_type} ({g.similarity:.0%}) lignes {lines}"
              f" — clé: {g.key[:50]}")


def _print_comparison(comp: ComparisonResult):
    """Affiche la comparaison dans le terminal."""
    print(f"  Fichier A : {comp.file_a} ({comp.total_a:,} lignes)")
    print(f"  Fichier B : {comp.file_b} ({comp.total_b:,} lignes)")
    print(f"  Identiques   : {comp.unchanged:,}")
    print(f"  ➕ Ajoutées  : {comp.added:,}")
    print(f"  ➖ Supprimées: {comp.removed:,}")
    print(f"  ✏️ Modifiées : {comp.modified:,} ({comp.total_cell_changes:,} cellules)")
    print(f"  Similarité   : {comp.similarity_percentage:.1f}%")

    if comp.header_changes:
        print(f"\n  En-têtes modifiés :")
        for col, ha, hb in comp.header_changes[:5]:
            print(f"    Col {col}: '{ha}' → '{hb}'")


def _print_reconciliation(recon: ReconciliationResult):
    """Affiche le rapprochement dans le terminal."""
    print(f"  Gauche  : {recon.file_left} ({recon.total_left:,} lignes)")
    print(f"  Droite  : {recon.file_right} ({recon.total_right:,} lignes)")
    print(f"  ✅ Matchés     : {recon.matched:,}")
    print(f"  ⚠️ Écarts      : {recon.mismatched:,}")
    print(f"  ◀ Gauche seul  : {recon.left_only:,}")
    print(f"  ▶ Droite seul  : {recon.right_only:,}")
    print(f"  Taux match     : {recon.match_rate:.1f}%")
    if recon.total_difference > 0:
        print(f"  Écart total    : {recon.total_difference:,.2f}")


def _run_cloud():
    """Liste les emplacements cloud détectés."""
    print("\n☁ Détection des emplacements cloud...")
    locations = detect_cloud_locations()

    if not locations:
        print("  Aucun service cloud détecté.")
        print("  Services supportés: OneDrive, SharePoint, pCloud, Google Drive, Dropbox")
        return

    for loc in locations:
        print(f"\n  ☁ {loc.name} ({loc.provider})")
        print(f"    Chemin: {loc.local_path}")
        print(f"    Disponible: {'✅' if loc.is_available else '❌'}")


def _run_merge(args: argparse.Namespace):
    """Fusionne plusieurs fichiers CSV."""
    files = [Path(f) for f in args.merge]
    quiet = getattr(args, 'quiet', False)

    for f in files:
        if not f.exists():
            print(f"❌ Fichier non trouvé : {f}")
            sys.exit(1)

    if not quiet:
        print(f"\n📎 Fusion de {len(files)} fichiers...")

    detections = [run_detection(f) for f in files]

    # Vérifier compatibilité
    ref = detections[0]
    if not quiet:
        print(f"  Référence: {ref.filepath.name} ({ref.expected_columns} colonnes)")

    for i, det in enumerate(detections[1:], 1):
        compat = check_compatibility(ref, det)
        if not quiet:
            print(f"  [{i+1}] {det.filepath.name}: ", end='')
            if compat.issues:
                for issue in compat.issues:
                    print(f"\n      {issue.severity}: {issue.message}", end='')
                print()
            else:
                print("✅ compatible")

    result = merge_files(detections, skip_empty_rows=True)

    if not quiet:
        print(f"\n  Lignes fusionnées   : {result.total_rows:,}")
        print(f"  Lignes vides ignorées : {result.skipped_empty_rows:,}")
        print(f"  Par fichier: {result.rows_per_file}")

    # Export
    output = getattr(args, 'output', None)
    if output:
        output_path = Path(output)
        csv_sep = getattr(args, 'csv_separator', '§')
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            f.write(csv_sep.join(result.output_headers) + '\n')
            for row in result.merged_rows:
                f.write(csv_sep.join(row) + '\n')
        if not quiet:
            print(f"\n  💾 Exporté : {output_path}")
    elif not quiet:
        print("\n  Utilisez -o pour exporter le résultat.")
