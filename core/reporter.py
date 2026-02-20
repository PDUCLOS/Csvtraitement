"""Generateur de rapport HTML de qualite des donnees.

Produit un fichier HTML standalone avec :
- Score de qualite global + jauge visuelle
- Tableau resume par colonne (score, note, remplissage, types)
- Histogrammes Unicode de distribution
- Avant/apres nettoyage
- Metadonnees fichier
"""

import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult
from core.fixer import FixResult
from core.quality import QualityReport, ColumnQuality
from core.statistics import FileStats

logger = logging.getLogger(__name__)


def _grade_color(grade: str) -> str:
    """Retourne la couleur CSS pour une note."""
    return {
        'A': '#a6e3a1', 'B': '#94e2d5', 'C': '#f9e2af',
        'D': '#fab387', 'F': '#f38ba8',
    }.get(grade, '#cdd6f4')


def _score_bar(score: float, width: int = 20) -> str:
    """Genere une barre de progression Unicode."""
    filled = int(score / 100 * width)
    empty = width - filled
    return '\u2588' * filled + '\u2591' * empty


def _escape(text) -> str:
    """Echappe du texte pour HTML."""
    return html.escape(str(text))


def generate_html_report(
    detection: DetectionResult,
    quality: Optional[QualityReport] = None,
    fix_result: Optional[FixResult] = None,
    file_stats: Optional[FileStats] = None,
    title: str = "Rapport de qualite des donnees",
) -> str:
    """Genere un rapport HTML complet.

    Args:
        detection: Resultat de detection du fichier.
        quality: Rapport de qualite (optionnel).
        fix_result: Resultat des corrections (optionnel).
        file_stats: Statistiques du fichier (optionnel).
        title: Titre du rapport.

    Returns:
        Contenu HTML complet.
    """
    now = datetime.now().strftime('%d/%m/%Y %H:%M')
    filename = detection.filepath.name if detection.filepath else 'Inconnu'

    # Scores globaux
    overall_score = quality.overall_score if quality else 0
    overall_grade = quality.overall_grade if quality else '?'
    grade_color = _grade_color(overall_grade)

    html_parts = [_html_head(title), '<body>', '<div class="container">']

    # ── Header ──
    html_parts.append(f'''
    <header>
        <h1>{_escape(title)}</h1>
        <p class="meta">Fichier : <strong>{_escape(filename)}</strong> | Date : {now} |
        Genere par CSV Traitement</p>
    </header>
    ''')

    # ── Score global ──
    if quality:
        html_parts.append(f'''
    <div class="score-card">
        <div class="score-circle" style="border-color: {grade_color}">
            <span class="grade" style="color: {grade_color}">{overall_grade}</span>
            <span class="score">{overall_score:.0f}/100</span>
        </div>
        <div class="score-details">
            <h2>Score de qualite global</h2>
            <div class="score-bar-container">
                <div class="score-bar" style="width: {overall_score}%; background: {grade_color}"></div>
            </div>
            <ul>
                <li>Doublons : {quality.duplicate_rate:.1f}%</li>
                <li>Lignes vides : {quality.empty_row_rate:.1f}%</li>
                <li>Problemes detectes : {quality.total_issues}</li>
            </ul>
        </div>
    </div>
        ''')

    # ── Metadonnees fichier ──
    html_parts.append(f'''
    <section class="metadata">
        <h2>Informations fichier</h2>
        <table class="info-table">
            <tr><td>Encodage</td><td>{_escape(detection.encoding)}</td></tr>
            <tr><td>Separateur</td><td>{_escape(repr(detection.separator))}</td></tr>
            <tr><td>Lignes</td><td>{detection.total_lines:,}</td></tr>
            <tr><td>Colonnes</td><td>{detection.expected_columns}</td></tr>
            <tr><td>Format decimal</td><td>{_escape(detection.decimal_format or 'Non detecte')}</td></tr>
            <tr><td>Conformite</td><td>{detection.conformity_ratio:.1%}</td></tr>
        </table>
    </section>
    ''')

    # ── Corrections (avant/apres) ──
    if fix_result:
        html_parts.append(f'''
    <section class="corrections">
        <h2>Corrections appliquees</h2>
        <div class="kpi-grid">
            <div class="kpi">
                <span class="kpi-value">{fix_result.total_corrections:,}</span>
                <span class="kpi-label">Total corrections</span>
            </div>
            <div class="kpi">
                <span class="kpi-value">{fix_result.date_fixes:,}</span>
                <span class="kpi-label">Dates corrigees</span>
            </div>
            <div class="kpi">
                <span class="kpi-value">{fix_result.decimal_fixes:,}</span>
                <span class="kpi-label">Decimales normalisees</span>
            </div>
            <div class="kpi">
                <span class="kpi-value">{fix_result.realignment_fixes:,}</span>
                <span class="kpi-label">Realignements</span>
            </div>
        </div>
        ''')

        # Exemples de corrections
        if fix_result.corrections[:10]:
            html_parts.append('<h3>Exemples de corrections</h3>')
            html_parts.append('<table class="data-table"><thead><tr>')
            html_parts.append('<th>Ligne</th><th>Colonne</th><th>Avant</th><th>Apres</th><th>Type</th>')
            html_parts.append('</tr></thead><tbody>')
            for c in fix_result.corrections[:20]:
                html_parts.append(f'<tr><td>{c.line_number}</td><td>{c.column_index}</td>')
                html_parts.append(f'<td class="before">{_escape(c.original_value[:50])}</td>')
                html_parts.append(f'<td class="after">{_escape(c.corrected_value[:50])}</td>')
                html_parts.append(f'<td>{_escape(c.correction_type)}</td></tr>')
            html_parts.append('</tbody></table>')

        html_parts.append('</section>')

    # ── Statistiques ──
    if file_stats:
        html_parts.append(f'''
    <section class="statistics">
        <h2>Statistiques</h2>
        <div class="kpi-grid">
            <div class="kpi">
                <span class="kpi-value">{file_stats.total_rows:,}</span>
                <span class="kpi-label">Lignes totales</span>
            </div>
            <div class="kpi">
                <span class="kpi-value">{file_stats.duplicate_rows:,}</span>
                <span class="kpi-label">Doublons</span>
            </div>
            <div class="kpi">
                <span class="kpi-value">{file_stats.empty_rows:,}</span>
                <span class="kpi-label">Lignes vides</span>
            </div>
            <div class="kpi">
                <span class="kpi-value">{file_stats.overall_fill_rate:.1f}%</span>
                <span class="kpi-label">Remplissage</span>
            </div>
        </div>
    </section>
        ''')

    # ── Qualite par colonne ──
    if quality and quality.column_scores:
        html_parts.append('''
    <section class="column-quality">
        <h2>Qualite par colonne</h2>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Colonne</th><th>Score</th><th>Note</th>
                    <th>Remplissage</th><th>Types</th><th>Outliers</th>
                    <th>Format</th><th>Diversite</th><th>Problemes</th>
                </tr>
            </thead>
            <tbody>
        ''')

        for cq in quality.column_scores:
            gc = _grade_color(cq.grade)
            issues_str = '; '.join(cq.issues[:2]) if cq.issues else '—'
            html_parts.append(f'''
                <tr>
                    <td><strong>[{cq.index}] {_escape(cq.header)}</strong></td>
                    <td>
                        <div class="mini-bar-container">
                            <div class="mini-bar" style="width:{cq.overall_score}%;background:{gc}"></div>
                        </div>
                        {cq.overall_score:.0f}
                    </td>
                    <td style="color:{gc};font-weight:bold">{cq.grade}</td>
                    <td>{cq.fill_score:.0f}%</td>
                    <td>{cq.type_consistency_score:.0f}%</td>
                    <td>{cq.outlier_score:.0f}%</td>
                    <td>{cq.format_homogeneity_score:.0f}%</td>
                    <td>{cq.uniqueness_score:.0f}%</td>
                    <td class="issues">{_escape(issues_str)}</td>
                </tr>
            ''')

        html_parts.append('</tbody></table></section>')

    # ── Profils colonnes ──
    html_parts.append('''
    <section class="profiles">
        <h2>Profil des colonnes</h2>
        <table class="data-table">
            <thead><tr>
                <th>#</th><th>En-tete</th><th>Type</th>
                <th>Confiance</th><th>% vides</th><th>Format dec.</th>
            </tr></thead><tbody>
    ''')

    for p in detection.column_profiles:
        header_name = detection.header[p.index].strip() if p.index < len(detection.header) else ''
        html_parts.append(f'''
            <tr>
                <td>{p.index}</td><td>{_escape(header_name)}</td>
                <td>{p.dominant_type.name}</td>
                <td>{p.confidence:.0%}</td><td>{p.empty_ratio:.0%}</td>
                <td>{_escape(p.decimal_format or '—')}</td>
            </tr>
        ''')

    html_parts.append('</tbody></table></section>')

    # ── Distribution colonnes ──
    html_parts.append('''
    <section class="distribution">
        <h2>Distribution du nombre de colonnes</h2>
        <div class="dist-chart">
    ''')

    total_data = sum(detection.column_count_distribution.values())
    for n_cols, count in sorted(detection.column_count_distribution.items()):
        pct = count / total_data * 100 if total_data > 0 else 0
        marker = ' (attendu)' if n_cols == detection.expected_columns else ''
        bar_color = '#a6e3a1' if n_cols == detection.expected_columns else '#f38ba8'
        html_parts.append(f'''
            <div class="dist-row">
                <span class="dist-label">{n_cols} cols{marker}</span>
                <div class="dist-bar-container">
                    <div class="dist-bar" style="width:{pct}%;background:{bar_color}"></div>
                </div>
                <span class="dist-count">{count:,} ({pct:.1f}%)</span>
            </div>
        ''')

    html_parts.append('</div></section>')

    # ── Footer ──
    html_parts.append(f'''
    <footer>
        <p>Rapport genere par <strong>CSV Traitement</strong> le {now}</p>
        <p>Score : {overall_score:.0f}/100 ({overall_grade}) |
        {detection.total_lines:,} lignes | {detection.expected_columns} colonnes</p>
    </footer>
    </div></body></html>
    ''')

    return '\n'.join(html_parts)


def export_html_report(
    detection: DetectionResult,
    output_path: Path,
    quality: Optional[QualityReport] = None,
    fix_result: Optional[FixResult] = None,
    file_stats: Optional[FileStats] = None,
    title: str = "Rapport de qualite des donnees",
) -> Path:
    """Genere et sauvegarde le rapport HTML.

    Args:
        detection: Resultat de detection.
        output_path: Chemin du fichier HTML de sortie.
        quality: Rapport qualite (optionnel).
        fix_result: Corrections (optionnel).
        file_stats: Statistiques (optionnel).
        title: Titre du rapport.

    Returns:
        Chemin du fichier cree.
    """
    content = generate_html_report(detection, quality, fix_result, file_stats, title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    logger.info(f"Rapport HTML genere : {output_path}")
    return output_path


def _html_head(title: str) -> str:
    """Retourne le head HTML avec styles inline."""
    return f'''<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_escape(title)}</title>
    <style>
        :root {{
            --bg: #1e1e2e; --bg-alt: #252536; --bg-card: #2a2a3d;
            --fg: #cdd6f4; --fg-dim: #6c7086;
            --accent: #89b4fa; --success: #a6e3a1; --error: #f38ba8;
            --warning: #fab387; --border: #45475a;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: var(--bg); color: var(--fg);
            font-family: 'Segoe UI', system-ui, sans-serif;
            line-height: 1.6;
        }}
        .container {{ max-width: 1100px; margin: 0 auto; padding: 30px 20px; }}
        header {{
            text-align: center; margin-bottom: 30px;
            border-bottom: 2px solid var(--border); padding-bottom: 20px;
        }}
        header h1 {{ color: var(--accent); font-size: 1.8em; margin-bottom: 8px; }}
        .meta {{ color: var(--fg-dim); font-size: 0.9em; }}
        h2 {{
            color: var(--accent); font-size: 1.2em; margin: 25px 0 15px;
            border-left: 3px solid var(--accent); padding-left: 10px;
        }}
        h3 {{ color: var(--fg); font-size: 1em; margin: 15px 0 10px; }}

        /* Score card */
        .score-card {{
            display: flex; align-items: center; gap: 30px;
            background: var(--bg-card); border-radius: 12px;
            padding: 25px 30px; margin-bottom: 25px;
            border: 1px solid var(--border);
        }}
        .score-circle {{
            width: 100px; height: 100px; border-radius: 50%;
            border: 4px solid; display: flex; flex-direction: column;
            align-items: center; justify-content: center; flex-shrink: 0;
        }}
        .grade {{ font-size: 2em; font-weight: bold; }}
        .score {{ font-size: 0.85em; color: var(--fg-dim); }}
        .score-details {{ flex: 1; }}
        .score-details h2 {{ border: none; margin: 0 0 10px; padding: 0; }}
        .score-bar-container {{
            background: var(--bg); border-radius: 6px; height: 12px;
            overflow: hidden; margin-bottom: 12px;
        }}
        .score-bar {{ height: 100%; border-radius: 6px; transition: width 0.5s; }}
        .score-details ul {{ list-style: none; display: flex; gap: 20px; }}
        .score-details li {{ color: var(--fg-dim); font-size: 0.9em; }}

        /* KPI grid */
        .kpi-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px; margin: 15px 0;
        }}
        .kpi {{
            background: var(--bg-card); border-radius: 8px; padding: 15px;
            text-align: center; border: 1px solid var(--border);
        }}
        .kpi-value {{ display: block; font-size: 1.5em; font-weight: bold; color: var(--accent); }}
        .kpi-label {{ display: block; font-size: 0.85em; color: var(--fg-dim); margin-top: 4px; }}

        /* Tables */
        .data-table {{
            width: 100%; border-collapse: collapse; margin: 10px 0;
            font-size: 0.85em;
        }}
        .data-table th {{
            background: var(--bg-alt); color: var(--accent);
            padding: 10px 12px; text-align: left; font-weight: 600;
            border-bottom: 2px solid var(--border);
        }}
        .data-table td {{
            padding: 8px 12px; border-bottom: 1px solid var(--border);
        }}
        .data-table tr:nth-child(even) {{ background: var(--bg-alt); }}
        .data-table tr:hover {{ background: rgba(137, 180, 250, 0.08); }}
        .before {{ color: var(--error); text-decoration: line-through; }}
        .after {{ color: var(--success); font-weight: 500; }}
        .issues {{ color: var(--warning); font-size: 0.85em; }}

        /* Info table */
        .info-table {{ width: auto; }}
        .info-table td:first-child {{
            font-weight: 600; color: var(--fg-dim); padding-right: 20px; width: 150px;
        }}

        /* Mini progress bars */
        .mini-bar-container {{
            display: inline-block; width: 60px; height: 8px;
            background: var(--bg); border-radius: 4px; overflow: hidden;
            vertical-align: middle; margin-right: 6px;
        }}
        .mini-bar {{ height: 100%; border-radius: 4px; }}

        /* Distribution chart */
        .dist-chart {{ margin: 10px 0; }}
        .dist-row {{
            display: flex; align-items: center; gap: 10px;
            margin: 6px 0; font-size: 0.9em;
        }}
        .dist-label {{ width: 140px; text-align: right; color: var(--fg-dim); }}
        .dist-bar-container {{
            flex: 1; height: 20px; background: var(--bg-alt);
            border-radius: 4px; overflow: hidden;
        }}
        .dist-bar {{ height: 100%; border-radius: 4px; }}
        .dist-count {{ width: 130px; font-family: 'JetBrains Mono', monospace; font-size: 0.85em; }}

        /* Sections */
        section {{
            background: var(--bg-card); border-radius: 10px;
            padding: 20px; margin-bottom: 20px;
            border: 1px solid var(--border);
        }}

        footer {{
            text-align: center; margin-top: 30px; padding-top: 20px;
            border-top: 1px solid var(--border); color: var(--fg-dim);
            font-size: 0.85em;
        }}

        @media print {{
            body {{ background: white; color: #333; }}
            .container {{ max-width: 100%; }}
            section {{ border: 1px solid #ddd; }}
            .score-card {{ border: 1px solid #ddd; }}
            :root {{
                --bg: white; --bg-alt: #f5f5f5; --bg-card: #fafafa;
                --fg: #333; --fg-dim: #666; --accent: #2563eb;
                --success: #16a34a; --error: #dc2626; --warning: #d97706;
                --border: #e5e5e5;
            }}
        }}
    </style>
</head>'''
