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
from core.joiner import (
    join_files, detect_key_candidates, smart_join, JoinType, MatchStrategy, KeyCandidate
)
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

    # Format de sortie
    cli_parser.add_argument('--format', choices=list(EXPORT_FORMATS.keys()), default='xlsx',
                           help='Format de sortie (défaut: xlsx). '
                                'Fichiers : xlsx, csv, json, ods, parquet, sql, sqlite. '
                                'Serveur : postgres (nécessite psycopg2).')
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
    cli_parser.add_argument('--merge', type=str, nargs='+',
                           help='Fusionner plusieurs fichiers CSV')
    cli_parser.add_argument('--cloud', action='store_true',
                           help='Lister les emplacements cloud détectés')

    # Jointure intelligente
    cli_parser.add_argument('--smart-join', type=str,
                           help='Jointure intelligente avec un second fichier (tout format)')
    cli_parser.add_argument('--join-type', type=str, default='inner',
                           choices=['inner', 'left', 'right', 'full'],
                           help='Type de jointure (défaut: inner)')
    cli_parser.add_argument('--join-strategy', type=str, default='exact',
                           choices=['exact', 'case_insensitive', 'normalized', 'fuzzy', 'numeric'],
                           help='Stratégie de matching sur la clé (défaut: exact)')
    cli_parser.add_argument('--join-key-left', type=str, default=None,
                           help='Colonnes-clés fichier gauche (indices séparés par virgule)')
    cli_parser.add_argument('--join-key-right', type=str, default=None,
                           help='Colonnes-clés fichier droit (indices séparés par virgule)')
    cli_parser.add_argument('--join-cols-left', type=str, default=None,
                           help='Colonnes à reprendre du fichier gauche (indices, défaut: toutes)')
    cli_parser.add_argument('--join-cols-right', type=str, default=None,
                           help='Colonnes à reprendre du fichier droit (indices, défaut: toutes sauf clé)')
    cli_parser.add_argument('--detect-keys', action='store_true',
                           help='Afficher les clés candidates sans effectuer la jointure')

    # Options SQL
    cli_parser.add_argument('--table-name', type=str, default=None,
                           help='Nom de la table SQL (déduit du fichier si omis)')

    # Options PostgreSQL
    cli_parser.add_argument('--pg-host', type=str, default='localhost',
                           help='Hôte PostgreSQL (défaut: localhost)')
    cli_parser.add_argument('--pg-port', type=int, default=5432,
                           help='Port PostgreSQL (défaut: 5432)')
    cli_parser.add_argument('--pg-database', type=str, default='csvtraitement',
                           help='Base PostgreSQL (défaut: csvtraitement)')
    cli_parser.add_argument('--pg-user', type=str, default='postgres',
                           help='Utilisateur PostgreSQL (défaut: postgres)')
    cli_parser.add_argument('--pg-password', type=str, default='',
                           help='Mot de passe PostgreSQL')
    cli_parser.add_argument('--pg-schema', type=str, default='public',
                           help='Schéma PostgreSQL (défaut: public)')

    # Options Snowflake
    cli_parser.add_argument('--sf-account', type=str, default=None,
                           help='Identifiant du compte Snowflake (ex: xy12345.eu-west-1)')
    cli_parser.add_argument('--sf-user', type=str, default=None,
                           help='Utilisateur Snowflake')
    cli_parser.add_argument('--sf-password', type=str, default=None,
                           help='Mot de passe Snowflake')
    cli_parser.add_argument('--sf-warehouse', type=str, default=None,
                           help='Entrepôt de calcul Snowflake')
    cli_parser.add_argument('--sf-database', type=str, default=None,
                           help='Base de données Snowflake')
    cli_parser.add_argument('--sf-schema', type=str, default='PUBLIC',
                           help='Schéma Snowflake (défaut: PUBLIC)')
    cli_parser.add_argument('--sf-role', type=str, default=None,
                           help='Rôle Snowflake')
    cli_parser.add_argument('--sf-query', type=str, default=None,
                           help='Import Snowflake : requête SQL (SELECT ...)')

    # Options Databricks
    cli_parser.add_argument('--db-host', type=str, default=None,
                           help='Hostname du workspace Databricks (ex: adb-xxx.azuredatabricks.net)')
    cli_parser.add_argument('--db-http-path', type=str, default=None,
                           help='Chemin HTTP du SQL warehouse (ex: /sql/1.0/warehouses/xxx)')
    cli_parser.add_argument('--db-token', type=str, default=None,
                           help='Token d\'accès personnel Databricks')
    cli_parser.add_argument('--db-catalog', type=str, default=None,
                           help='Catalogue Unity Databricks')
    cli_parser.add_argument('--db-schema', type=str, default='default',
                           help='Schéma Databricks (défaut: default)')
    cli_parser.add_argument('--db-query', type=str, default=None,
                           help='Import Databricks : requête SQL (SELECT ...)')

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

    # Import Snowflake (pas besoin de fichier local)
    if getattr(args, 'sf_query', None):
        _run_snowflake_import(args)
        return

    # Import Databricks (pas besoin de fichier local)
    if getattr(args, 'db_query', None):
        _run_databricks_import(args)
        return

    # Jointure intelligente (nécessite --file + --smart-join)
    if getattr(args, 'smart_join', None):
        if not getattr(args, 'file', None):
            print("❌ --file est requis avec --smart-join (fichier gauche)")
            sys.exit(1)
        _run_smart_join(args)
        return

    # Vérifier qu'un fichier ou batch est fourni
    if not getattr(args, 'file', None) and not getattr(args, 'batch', None):
        print("❌ Veuillez spécifier --file, --batch, --merge, --cloud, --sf-query ou --db-query")
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
    report = {}
    sheet_name = getattr(args, 'sheet', None)

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
        comparison = compare_files(detection, detection_b)
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
        recon = reconcile(detection, detection_r, join_columns=join_cols)
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
        )

        report['corrections'] = fix_result.summary

        if not quiet:
            print(f"  Total corrections  : {fix_result.total_corrections:,}")
            print(f"  Dates corrigées    : {fix_result.date_fixes:,}")
            print(f"  Décimales normalisées : {fix_result.decimal_fixes:,}")
            print(f"  Réalignements      : {fix_result.realignment_fixes:,}")

        # ── Export ──
        fmt = getattr(args, 'format', 'xlsx')
        output = getattr(args, 'output', None)

        if fmt == 'postgres':
            # Export direct PostgreSQL (pas besoin de fichier de sortie)
            from core.exporter import export_postgres
            if not quiet:
                pg_host = getattr(args, 'pg_host', 'localhost')
                pg_db = getattr(args, 'pg_database', 'csvtraitement')
                print(f"\n🐘 Export PostgreSQL → {pg_host}/{pg_db}")

            full_table = export_postgres(
                detection, fix_result,
                host=getattr(args, 'pg_host', 'localhost'),
                port=getattr(args, 'pg_port', 5432),
                database=getattr(args, 'pg_database', 'csvtraitement'),
                user=getattr(args, 'pg_user', 'postgres'),
                password=getattr(args, 'pg_password', ''),
                table_name=getattr(args, 'table_name', None),
                schema=getattr(args, 'pg_schema', 'public'),
            )
            if not quiet:
                print(f"  ✅ Table créée : {full_table}")

        elif fmt == 'snowflake':
            # Export direct Snowflake
            from core.exporter import export_snowflake
            sf_account = getattr(args, 'sf_account', None)
            sf_user = getattr(args, 'sf_user', None)
            sf_password = getattr(args, 'sf_password', None)
            if not sf_account or not sf_user or not sf_password:
                print("❌ --sf-account, --sf-user et --sf-password sont requis pour l'export Snowflake")
                sys.exit(1)
            if not quiet:
                print(f"\n❄ Export Snowflake → {sf_account}")

            full_table = export_snowflake(
                detection, fix_result,
                account=sf_account, user=sf_user, password=sf_password,
                table_name=getattr(args, 'table_name', None),
                warehouse=getattr(args, 'sf_warehouse', None),
                database=getattr(args, 'sf_database', None),
                schema=getattr(args, 'sf_schema', 'PUBLIC'),
                role=getattr(args, 'sf_role', None),
            )
            if not quiet:
                print(f"  ✅ Table créée : {full_table}")

        elif fmt == 'databricks':
            # Export direct Databricks
            from core.exporter import export_databricks
            db_host = getattr(args, 'db_host', None)
            db_http_path = getattr(args, 'db_http_path', None)
            db_token = getattr(args, 'db_token', None)
            if not db_host or not db_http_path or not db_token:
                print("❌ --db-host, --db-http-path et --db-token sont requis pour l'export Databricks")
                sys.exit(1)
            if not quiet:
                print(f"\n🔶 Export Databricks → {db_host}")

            full_table = export_databricks(
                detection, fix_result,
                server_hostname=db_host, http_path=db_http_path,
                access_token=db_token,
                table_name=getattr(args, 'table_name', None),
                catalog=getattr(args, 'db_catalog', None),
                schema=getattr(args, 'db_schema', 'default'),
            )
            if not quiet:
                print(f"  ✅ Table créée : {full_table}")

        elif output:
            output_path = Path(output)
            csv_sep = getattr(args, 'csv_separator', '§')

            if not quiet:
                fmt_desc = EXPORT_FORMATS.get(fmt, fmt.upper())
                print(f"\n💾 Export {fmt_desc} → {output_path}")

            # Passer le table_name pour SQL/SQLite
            if fmt in ('sql', 'sqlite'):
                from core.exporter import export_sql, export_sqlite
                tbl = getattr(args, 'table_name', None)
                if fmt == 'sql':
                    export_sql(detection, fix_result, output_path, table_name=tbl)
                else:
                    export_sqlite(detection, fix_result, output_path, table_name=tbl)
            else:
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


def _run_snowflake_import(args: argparse.Namespace):
    """Importe des données depuis Snowflake et les sauvegarde en CSV."""
    quiet = getattr(args, 'quiet', False)
    sf_account = getattr(args, 'sf_account', None)
    sf_user = getattr(args, 'sf_user', None)
    sf_password = getattr(args, 'sf_password', None)
    sf_query = getattr(args, 'sf_query', None)

    if not sf_account or not sf_user or not sf_password:
        print("❌ --sf-account, --sf-user et --sf-password sont requis pour l'import Snowflake")
        sys.exit(1)

    if not quiet:
        print(f"\n❄ Import Snowflake depuis {sf_account}...")
        print(f"  Requête : {sf_query[:80]}...")

    from utils.db_connectors import snowflake_import_to_csv

    output = getattr(args, 'output', None)
    output_path = Path(output) if output else None

    csv_path = snowflake_import_to_csv(
        account=sf_account, user=sf_user, password=sf_password,
        query=sf_query, output_path=output_path,
        warehouse=getattr(args, 'sf_warehouse', None),
        database=getattr(args, 'sf_database', None),
        schema=getattr(args, 'sf_schema', 'PUBLIC'),
        role=getattr(args, 'sf_role', None),
    )

    if not quiet:
        print(f"  ✅ Données importées : {csv_path}")
        print(f"  Vous pouvez maintenant traiter ce fichier :")
        print(f"  python main.py cli --file {csv_path} --fix -o resultat.xlsx")


def _run_smart_join(args: argparse.Namespace):
    """Jointure intelligente entre deux fichiers."""
    quiet = getattr(args, 'quiet', False)
    filepath_left = Path(args.file)
    filepath_right = Path(args.smart_join)

    if not filepath_left.exists():
        print(f"❌ Fichier gauche non trouvé : {filepath_left}")
        sys.exit(1)
    if not filepath_right.exists():
        print(f"❌ Fichier droit non trouvé : {filepath_right}")
        sys.exit(1)

    if not quiet:
        print(f"\n🔗 Jointure intelligente")
        print(f"  Gauche : {filepath_left.name}")
        print(f"  Droite : {filepath_right.name}")
        print("─" * 50)

    # Détection-seule (--detect-keys)
    detect_only = getattr(args, 'detect_keys', False)

    # Parser les colonnes-clés manuelles
    key_left = None
    key_right = None
    jkl = getattr(args, 'join_key_left', None)
    jkr = getattr(args, 'join_key_right', None)
    if jkl is not None:
        key_left = [int(c.strip()) for c in jkl.split(',')]
    if jkr is not None:
        key_right = [int(c.strip()) for c in jkr.split(',')]

    # Parser les colonnes de sortie
    cols_left = None
    cols_right = None
    jcl = getattr(args, 'join_cols_left', None)
    jcr = getattr(args, 'join_cols_right', None)
    if jcl is not None:
        cols_left = [int(c.strip()) for c in jcl.split(',')]
    if jcr is not None:
        cols_right = [int(c.strip()) for c in jcr.split(',')]

    # Lire les fichiers et détecter les clés
    from utils.universal_reader import read_any
    header_l, rows_l, _ = read_any(filepath_left, sheet_name=getattr(args, 'sheet', None))
    header_r, rows_r, _ = read_any(filepath_right)

    if not quiet:
        print(f"\n  Fichier gauche : {len(rows_l):,} lignes, {len(header_l)} colonnes")
        print(f"    En-têtes : {', '.join(header_l[:8])}{'...' if len(header_l) > 8 else ''}")
        print(f"  Fichier droit  : {len(rows_r):,} lignes, {len(header_r)} colonnes")
        print(f"    En-têtes : {', '.join(header_r[:8])}{'...' if len(header_r) > 8 else ''}")

    # Détecter les clés candidates
    candidates = detect_key_candidates(header_l, rows_l, header_r, rows_r)

    if not quiet:
        print(f"\n🔑 Clés candidates détectées : {len(candidates)}")
        for i, c in enumerate(candidates[:10]):
            marker = ' ⭐' if i == 0 and c.confidence > 0.5 else ''
            print(f"  [{i+1}] {c.confidence:.0%} — {c.left_name!r} (gauche:{c.left_column})"
                  f" ↔ {c.right_name!r} (droite:{c.right_column}){marker}")
            print(f"       {c.reason}")
            print(f"       Valeurs communes: {c.match_count} "
                  f"(gauche: {c.match_rate_left:.0%}, droite: {c.match_rate_right:.0%})")

    if detect_only:
        return

    # Effectuer la jointure
    join_type = getattr(args, 'join_type', 'inner')
    strategy = getattr(args, 'join_strategy', 'exact')

    if key_left is None or key_right is None:
        if candidates:
            best = candidates[0]
            key_left = key_left or [best.left_column]
            key_right = key_right or [best.right_column]
            if not quiet:
                print(f"\n  Clé utilisée : {best.left_name!r} ↔ {best.right_name!r} "
                      f"(confiance: {best.confidence:.0%})")
        else:
            key_left = key_left or [0]
            key_right = key_right or [0]
            if not quiet:
                print("\n  ⚠️ Aucune clé détectée, utilisation de la colonne 0")

    if not quiet:
        print(f"\n  Type de jointure : {join_type.upper()}")
        print(f"  Stratégie        : {strategy}")
        print(f"  Clé gauche       : colonnes {key_left}")
        print(f"  Clé droite       : colonnes {key_right}")

    join_result = smart_join(
        header_l, rows_l, header_r, rows_r,
        key_left=key_left, key_right=key_right,
        join_type=join_type, strategy=strategy,
        columns_left=cols_left, columns_right=cols_right,
    )

    if not quiet:
        print(f"\n📊 Résultat de la jointure :")
        print(f"  ✅ Matchés       : {join_result.matched_count:,}")
        print(f"  ◀ Gauche seul    : {join_result.left_only_count:,}")
        print(f"  ▶ Droite seul    : {join_result.right_only_count:,}")
        print(f"  Total lignes     : {join_result.total_rows:,}")
        print(f"  Taux de match    : {join_result.match_rate:.1f}%")
        print(f"  Colonnes sortie  : {len(join_result.header)}")
        print(f"    {', '.join(join_result.header[:10])}{'...' if len(join_result.header) > 10 else ''}")

    # Export
    output = getattr(args, 'output', None)
    if output:
        import csv
        output_path = Path(output)
        csv_sep = getattr(args, 'csv_separator', '§')
        fmt = getattr(args, 'format', 'xlsx')

        if fmt == 'csv':
            with open(output_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter=csv_sep, quotechar='"',
                                    quoting=csv.QUOTE_MINIMAL)
                writer.writerow(join_result.header)
                for row in join_result.rows:
                    writer.writerow(row)
        elif fmt == 'xlsx':
            import xlsxwriter
            wb = xlsxwriter.Workbook(str(output_path), {'constant_memory': True})
            ws = wb.add_worksheet('Jointure')
            header_fmt = wb.add_format({'bold': True, 'bg_color': '#363650',
                                        'font_color': '#cdd6f4'})
            for c, h in enumerate(join_result.header):
                ws.write(0, c, h, header_fmt)
            for r, row in enumerate(join_result.rows, 1):
                for c, val in enumerate(row):
                    ws.write(r, c, val)
            wb.close()
        elif fmt == 'json':
            import json as json_mod
            data = []
            for row in join_result.rows:
                obj = {join_result.header[c]: val
                       for c, val in enumerate(row) if c < len(join_result.header)}
                data.append(obj)
            with open(output_path, 'w', encoding='utf-8') as f:
                json_mod.dump(data, f, indent=2, ensure_ascii=False)
        else:
            # Fallback CSV
            with open(output_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter=';', quotechar='"',
                                    quoting=csv.QUOTE_MINIMAL)
                writer.writerow(join_result.header)
                for row in join_result.rows:
                    writer.writerow(row)

        if not quiet:
            print(f"\n  💾 Exporté : {output_path}")
    elif not quiet:
        print("\n  Utilisez -o pour exporter le résultat.")

    # Rapport
    report_path = getattr(args, 'report', None)
    if report_path:
        report = {
            'smart_join': {
                'file_left': str(filepath_left),
                'file_right': str(filepath_right),
                'join_type': join_type,
                'strategy': strategy,
                'key_left': key_left,
                'key_right': key_right,
                'matched': join_result.matched_count,
                'left_only': join_result.left_only_count,
                'right_only': join_result.right_only_count,
                'total_rows': join_result.total_rows,
                'match_rate': join_result.match_rate,
                'output_columns': join_result.header,
                'candidates': [
                    {
                        'left_col': c.left_column, 'left_name': c.left_name,
                        'right_col': c.right_column, 'right_name': c.right_name,
                        'confidence': c.confidence, 'match_count': c.match_count,
                        'reason': c.reason,
                    }
                    for c in candidates[:20]
                ],
            }
        }
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        if not quiet:
            print(f"  📄 Rapport JSON : {report_path}")


def _run_databricks_import(args: argparse.Namespace):
    """Importe des données depuis Databricks et les sauvegarde en CSV."""
    quiet = getattr(args, 'quiet', False)
    db_host = getattr(args, 'db_host', None)
    db_http_path = getattr(args, 'db_http_path', None)
    db_token = getattr(args, 'db_token', None)
    db_query = getattr(args, 'db_query', None)

    if not db_host or not db_http_path or not db_token:
        print("❌ --db-host, --db-http-path et --db-token sont requis pour l'import Databricks")
        sys.exit(1)

    if not quiet:
        print(f"\n🔶 Import Databricks depuis {db_host}...")
        print(f"  Requête : {db_query[:80]}...")

    from utils.db_connectors import databricks_import_to_csv

    output = getattr(args, 'output', None)
    output_path = Path(output) if output else None

    csv_path = databricks_import_to_csv(
        server_hostname=db_host, http_path=db_http_path,
        access_token=db_token, query=db_query,
        output_path=output_path,
        catalog=getattr(args, 'db_catalog', None),
        schema=getattr(args, 'db_schema', 'default'),
    )

    if not quiet:
        print(f"  ✅ Données importées : {csv_path}")
        print(f"  Vous pouvez maintenant traiter ce fichier :")
        print(f"  python main.py cli --file {csv_path} --fix -o resultat.xlsx")
