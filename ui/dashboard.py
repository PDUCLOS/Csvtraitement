"""Dashboard principal Tkinter pour CSV Traitement.

Features: thème sombre, drag & drop, stats, templates, historique, undo,
qualité des données, doublons, comparaison, anonymisation, fusion,
gestion colonnes, connexions cloud.
"""

import logging
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult, run_detection
from core.analyzer import AnalysisResult, analyze_file
from core.fixer import FixResult, apply_fixes
from core.exporter import export_xlsx, export_csv_clean, CSV_SEPARATORS
from core.statistics import compute_statistics, FileStats
from core.quality import compute_quality, QualityReport
from core.dedup import find_duplicates, DedupResult, SelectionStrategy
from core.comparator import compare_files, ComparisonResult, RowStatus
from core.reconciler import reconcile, ReconciliationResult, MatchStatus
from core.anonymizer import (
    AnonymizationRule, SensitiveType, MaskMode,
    detect_column_sensitivity, anonymize_rows, AnonymizationResult
)
from core.merger import check_compatibility, merge_files, MergeResult
from core.columns import (
    reorder_columns, delete_columns, rename_columns,
    delete_rows, RowFilter, apply_column_operations
)
from utils.history import CorrectionHistory
from utils.cloud import detect_cloud_locations, CloudLocation

logger = logging.getLogger(__name__)

# ── Palette sombre ──
DARK = {
    'bg': '#1e1e2e',
    'bg_alt': '#252536',
    'bg_card': '#2a2a3d',
    'fg': '#cdd6f4',
    'fg_dim': '#6c7086',
    'accent': '#89b4fa',
    'accent2': '#a6e3a1',
    'warning': '#fab387',
    'error': '#f38ba8',
    'success': '#a6e3a1',
    'border': '#45475a',
    'select': '#313244',
    'header_bg': '#363650',
    'corrected_bg': '#3d3800',
}


class DashboardApp:
    """Application principale avec dashboard."""

    def __init__(self, root: tk.Tk, initial_file: Optional[str] = None):
        self.root = root
        self.root.title("CSV Traitement")
        self.root.geometry("1100x800")
        self.root.minsize(900, 650)
        self.root.configure(bg=DARK['bg'])

        # État
        self.detection: Optional[DetectionResult] = None
        self.analysis: Optional[AnalysisResult] = None
        self.fix_result: Optional[FixResult] = None
        self.file_stats: Optional[FileStats] = None
        self.current_file: Optional[Path] = None
        self.history = CorrectionHistory()

        self.quality_report: Optional[QualityReport] = None
        self.dedup_result: Optional[DedupResult] = None

        self._setup_dark_theme()
        self._build_toolbar()
        self._build_main_area()
        self._build_status_bar()
        self._setup_drag_drop()

        if initial_file:
            self.root.after(100, lambda: self._load_file(Path(initial_file)))

    def _setup_dark_theme(self):
        """Configure le thème sombre complet."""
        style = ttk.Style()
        style.theme_use('clam')

        # Frames & general
        style.configure('.', background=DARK['bg'], foreground=DARK['fg'])
        style.configure('TFrame', background=DARK['bg'])
        style.configure('Card.TFrame', background=DARK['bg_card'])
        style.configure('TLabel', background=DARK['bg'], foreground=DARK['fg'], font=('Segoe UI', 10))
        style.configure('Header.TLabel', font=('Segoe UI', 11, 'bold'), foreground=DARK['accent'])
        style.configure('KPI.TLabel', font=('JetBrains Mono', 13, 'bold'), foreground=DARK['fg'])
        style.configure('Good.TLabel', foreground=DARK['success'], font=('JetBrains Mono', 13, 'bold'))
        style.configure('Bad.TLabel', foreground=DARK['error'], font=('JetBrains Mono', 13, 'bold'))
        style.configure('Warning.TLabel', foreground=DARK['warning'], font=('JetBrains Mono', 13, 'bold'))
        style.configure('Dim.TLabel', foreground=DARK['fg_dim'], font=('Segoe UI', 9))
        style.configure('CardBg.TLabel', background=DARK['bg_card'], foreground=DARK['fg'])
        style.configure('CardHeader.TLabel', background=DARK['bg_card'], foreground=DARK['accent'], font=('Segoe UI', 11, 'bold'))
        style.configure('CardKPI.TLabel', background=DARK['bg_card'], foreground=DARK['fg'], font=('JetBrains Mono', 13, 'bold'))
        style.configure('CardGood.TLabel', background=DARK['bg_card'], foreground=DARK['success'], font=('JetBrains Mono', 13, 'bold'))
        style.configure('CardBad.TLabel', background=DARK['bg_card'], foreground=DARK['error'], font=('JetBrains Mono', 13, 'bold'))
        style.configure('CardWarning.TLabel', background=DARK['bg_card'], foreground=DARK['warning'], font=('JetBrains Mono', 13, 'bold'))

        # Buttons
        style.configure('TButton', background=DARK['bg_alt'], foreground=DARK['fg'],
                        font=('Segoe UI', 10), borderwidth=1, relief='flat', padding=(12, 6))
        style.map('TButton',
                  background=[('active', DARK['accent']), ('pressed', DARK['accent'])],
                  foreground=[('active', DARK['bg']), ('pressed', DARK['bg'])])
        style.configure('Accent.TButton', background=DARK['accent'], foreground=DARK['bg'])

        # Notebook
        style.configure('TNotebook', background=DARK['bg'], borderwidth=0)
        style.configure('TNotebook.Tab', background=DARK['bg_alt'], foreground=DARK['fg_dim'],
                        padding=(14, 6), font=('Segoe UI', 10))
        style.map('TNotebook.Tab',
                  background=[('selected', DARK['bg_card'])],
                  foreground=[('selected', DARK['accent'])])

        # Treeview
        style.configure('Treeview', background=DARK['bg_alt'], foreground=DARK['fg'],
                        fieldbackground=DARK['bg_alt'], borderwidth=0, font=('JetBrains Mono', 9))
        style.configure('Treeview.Heading', background=DARK['header_bg'], foreground=DARK['accent'],
                        font=('Segoe UI', 9, 'bold'))
        style.map('Treeview', background=[('selected', DARK['select'])])

        # Progressbar
        style.configure('TProgressbar', troughcolor=DARK['bg_alt'], background=DARK['accent'],
                        borderwidth=0, thickness=6)

        # LabelFrame
        style.configure('TLabelframe', background=DARK['bg_card'], foreground=DARK['accent'],
                        borderwidth=1, relief='solid')
        style.configure('TLabelframe.Label', background=DARK['bg_card'], foreground=DARK['accent'],
                        font=('Segoe UI', 10, 'bold'))

        # Checkbutton
        style.configure('TCheckbutton', background=DARK['bg_card'], foreground=DARK['fg'])

        # Scrollbar
        style.configure('TScrollbar', background=DARK['bg_alt'], troughcolor=DARK['bg'],
                        borderwidth=0, arrowsize=12)

        # Entry
        style.configure('TEntry', fieldbackground=DARK['bg_alt'], foreground=DARK['fg'],
                        insertcolor=DARK['fg'], borderwidth=1)

    def _build_toolbar(self):
        """Barre d'outils."""
        toolbar = ttk.Frame(self.root, padding=(10, 8))
        toolbar.pack(fill='x')

        buttons = [
            ('btn_load', '📂 Charger', self._on_load, 'normal'),
            ('btn_analyze', '🔍 Analyser', self._on_analyze, 'disabled'),
            ('btn_stats', '📊 Stats', self._on_stats, 'disabled'),
            ('btn_preview', '👁 Preview', self._on_preview, 'disabled'),
            ('btn_fix', '🔧 Corriger', self._on_fix, 'disabled'),
            ('btn_undo', '↩ Undo', self._on_undo, 'disabled'),
            ('btn_export', '💾 Export', self._on_export, 'disabled'),
        ]

        for attr, text, cmd, state in buttons:
            btn = ttk.Button(toolbar, text=text, command=cmd, state=state)
            btn.pack(side='left', padx=3)
            setattr(self, attr, btn)

        # Séparateur + nouveaux boutons
        ttk.Separator(toolbar, orient='vertical').pack(side='left', padx=8, fill='y')

        extra_buttons = [
            ('btn_quality', '🏆 Qualité', self._on_quality, 'disabled'),
            ('btn_dedup', '🔗 Doublons', self._on_dedup, 'disabled'),
            ('btn_compare', '⚡ Comparer', self._on_compare, 'disabled'),
            ('btn_merge', '📎 Fusionner', self._on_merge, 'normal'),
            ('btn_anonymize', '🔒 Anonymiser', self._on_anonymize, 'disabled'),
            ('btn_cloud', '☁ Cloud', self._on_cloud, 'normal'),
        ]

        for attr, text, cmd, state in extra_buttons:
            btn = ttk.Button(toolbar, text=text, command=cmd, state=state)
            btn.pack(side='left', padx=2)
            setattr(self, attr, btn)

        # Template buttons
        ttk.Separator(toolbar, orient='vertical').pack(side='left', padx=8, fill='y')
        self.btn_save_tpl = ttk.Button(toolbar, text='📑 Template', command=self._on_save_template, state='disabled')
        self.btn_save_tpl.pack(side='left', padx=3)
        self.btn_load_tpl = ttk.Button(toolbar, text='📄 Charger', command=self._on_load_template)
        self.btn_load_tpl.pack(side='left', padx=3)

        # File label
        self.lbl_file = ttk.Label(toolbar, text="Glisser-déposer un fichier CSV ici", style='Dim.TLabel')
        self.lbl_file.pack(side='right', padx=10)

    def _build_main_area(self):
        """Zone principale avec onglets."""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=(0, 5))

        # Onglet Diagnostic
        self.tab_diag = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_diag, text='  Diagnostic  ')
        self._build_diagnostic_tab()

        # Onglet Colonnes
        self.tab_columns = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_columns, text='  Colonnes  ')
        self._build_columns_tab()

        # Onglet Anomalies
        self.tab_issues = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_issues, text='  Anomalies  ')
        self._build_issues_tab()

        # Onglet Statistiques
        self.tab_stats = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_stats, text='  Statistiques  ')
        self._build_stats_tab()

        # Onglet Qualité
        self.tab_quality = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_quality, text='  Qualité  ')
        self._build_quality_tab()

        # Onglet Doublons
        self.tab_dedup = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_dedup, text='  Doublons  ')
        self._build_dedup_tab()

        # Onglet Corrections
        self.tab_fixes = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_fixes, text='  Corrections  ')
        self._build_fixes_tab()

        # Onglet Historique
        self.tab_history = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_history, text='  Historique  ')
        self._build_history_tab()

    def _build_diagnostic_tab(self):
        """KPIs en cartes."""
        # Grille de cartes KPI
        cards_frame = ttk.Frame(self.tab_diag, padding=10)
        cards_frame.pack(fill='x')

        self.kpi_labels = {}
        kpis = [
            ('separator', 'Séparateur', '—'),
            ('encoding', 'Encodage', '—'),
            ('total_lines', 'Lignes', '—'),
            ('expected_cols', 'Colonnes', '—'),
            ('conforming', 'Conformes', '—'),
            ('non_conforming', 'À corriger', '—'),
            ('conformity_pct', 'Validées', '—'),
            ('decimal_format', 'Format déc.', '—'),
        ]

        for i, (key, label, default) in enumerate(kpis):
            row, col = divmod(i, 4)
            card = ttk.Frame(cards_frame, style='Card.TFrame', padding=12)
            card.grid(row=row, column=col, padx=5, pady=5, sticky='nsew')
            cards_frame.columnconfigure(col, weight=1)

            ttk.Label(card, text=label, style='CardHeader.TLabel').pack(anchor='w')
            val_label = ttk.Label(card, text=default, style='CardKPI.TLabel')
            val_label.pack(anchor='w', pady=(4, 0))
            self.kpi_labels[key] = val_label

        # Distribution colonnes
        dist_frame = ttk.LabelFrame(self.tab_diag, text="Distribution nombre de colonnes", padding=10)
        dist_frame.pack(fill='both', expand=True, padx=10, pady=5)
        self.dist_text = tk.Text(dist_frame, height=6, font=('JetBrains Mono', 10),
                                bg=DARK['bg_alt'], fg=DARK['fg'], insertbackground=DARK['fg'],
                                state='disabled', relief='flat', borderwidth=0)
        self.dist_text.pack(fill='both', expand=True)

    def _build_columns_tab(self):
        """Profil des colonnes en treeview."""
        cols = ('Index', 'En-tête', 'Type', 'Confiance', '% vides', 'Format', 'Exemples')
        self.col_tree = ttk.Treeview(self.tab_columns, columns=cols, show='headings', height=20)
        for c in cols:
            self.col_tree.heading(c, text=c)
            self.col_tree.column(c, width=120 if c != 'Exemples' else 280)
        sb = ttk.Scrollbar(self.tab_columns, orient='vertical', command=self.col_tree.yview)
        self.col_tree.configure(yscrollcommand=sb.set)
        self.col_tree.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=10)
        sb.pack(side='right', fill='y', pady=10, padx=(0, 10))

    def _build_issues_tab(self):
        """Anomalies avec filtre."""
        # Barre de filtre
        filter_frame = ttk.Frame(self.tab_issues, padding=(10, 5))
        filter_frame.pack(fill='x')
        ttk.Label(filter_frame, text="Filtre :").pack(side='left')
        self.issue_filter_var = tk.StringVar()
        filter_entry = ttk.Entry(filter_frame, textvariable=self.issue_filter_var, width=30)
        filter_entry.pack(side='left', padx=5)
        ttk.Button(filter_frame, text="Filtrer", command=self._filter_issues).pack(side='left')
        ttk.Button(filter_frame, text="Reset", command=self._reset_issues_filter).pack(side='left', padx=3)

        cols = ('Ligne', 'Colonnes', 'Type', 'Col. décalage', 'Détail')
        self.issues_tree = ttk.Treeview(self.tab_issues, columns=cols, show='headings', height=20)
        for c in cols:
            self.issues_tree.heading(c, text=c)
            self.issues_tree.column(c, width=120 if c != 'Détail' else 350)
        sb = ttk.Scrollbar(self.tab_issues, orient='vertical', command=self.issues_tree.yview)
        self.issues_tree.configure(yscrollcommand=sb.set)
        self.issues_tree.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=(0, 10))
        sb.pack(side='right', fill='y', pady=(0, 10), padx=(0, 10))

        self.shift_summary_lbl = ttk.Label(self.tab_issues, text="", style='Warning.TLabel')
        self._all_issues_data = []  # Cache pour le filtre

    def _build_stats_tab(self):
        """Statistiques détaillées."""
        cols = ('Colonne', 'Type', 'Rempli%', 'Uniques', 'Min', 'Max', 'Moyenne', 'Outliers', 'Top valeurs')
        self.stats_tree = ttk.Treeview(self.tab_stats, columns=cols, show='headings', height=20)
        for c in cols:
            self.stats_tree.heading(c, text=c)
            w = 100 if c != 'Top valeurs' else 280
            self.stats_tree.column(c, width=w)
        sb = ttk.Scrollbar(self.tab_stats, orient='vertical', command=self.stats_tree.yview)
        self.stats_tree.configure(yscrollcommand=sb.set)
        self.stats_tree.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=10)
        sb.pack(side='right', fill='y', pady=10, padx=(0, 10))

        # Résumé global
        self.stats_summary = ttk.Label(self.tab_stats, text="Lancer 📊 Stats pour analyser", style='Dim.TLabel')
        self.stats_summary.pack(pady=5)

    def _build_fixes_tab(self):
        """Corrections avec options."""
        opts_frame = ttk.LabelFrame(self.tab_fixes, text="Options de correction", padding=12)
        opts_frame.pack(fill='x', padx=10, pady=10)

        self.var_fix_dates = tk.BooleanVar(value=True)
        self.var_fix_decimals = tk.BooleanVar(value=True)
        self.var_fix_shifts = tk.BooleanVar(value=True)
        self.var_fix_whitespace = tk.BooleanVar(value=True)
        self.var_fix_masked = tk.BooleanVar(value=True)

        checks = [
            (self.var_fix_dates, "Corriger dates invalides (00.00.0000 → vide)"),
            (self.var_fix_decimals, "Normaliser les décimales (FR/US → float)"),
            (self.var_fix_shifts, "Réaligner les colonnes décalées"),
            (self.var_fix_whitespace, "Normaliser les espaces"),
            (self.var_fix_masked, "Nettoyer valeurs masquées (N/A, NULL → vide)"),
        ]
        for var, text in checks:
            ttk.Checkbutton(opts_frame, text=text, variable=var).pack(anchor='w', pady=2)

        # Résumé corrections
        summary_frame = ttk.LabelFrame(self.tab_fixes, text="Résumé", padding=12)
        summary_frame.pack(fill='x', padx=10, pady=5)

        self.fix_labels = {}
        for i, (key, label) in enumerate([
            ('total', 'Corrections totales'),
            ('dates', 'Dates corrigées'),
            ('decimals', 'Décimales normalisées'),
            ('realignments', 'Réalignements'),
        ]):
            ttk.Label(summary_frame, text=f"{label} :").grid(row=i, column=0, sticky='w', padx=10, pady=2)
            val = ttk.Label(summary_frame, text="—", style='KPI.TLabel')
            val.grid(row=i, column=1, sticky='w', padx=10, pady=2)
            self.fix_labels[key] = val

    def _build_history_tab(self):
        """Historique des corrections."""
        cols = ('Horodatage', 'Opération', 'Description', 'Lignes', 'Corrections')
        self.history_tree = ttk.Treeview(self.tab_history, columns=cols, show='headings', height=15)
        for c in cols:
            self.history_tree.heading(c, text=c)
            self.history_tree.column(c, width=180 if c == 'Description' else 120)
        self.history_tree.pack(fill='both', expand=True, padx=10, pady=10)

        btn_frame = ttk.Frame(self.tab_history, padding=5)
        btn_frame.pack(fill='x', padx=10)
        ttk.Button(btn_frame, text="↩ Undo dernière opération", command=self._on_undo).pack(side='left', padx=3)
        ttk.Button(btn_frame, text="↪ Redo", command=self._on_redo).pack(side='left', padx=3)
        ttk.Button(btn_frame, text="🗑 Effacer historique", command=self._on_clear_history).pack(side='right', padx=3)

        self.history_summary_lbl = ttk.Label(self.tab_history, text="Aucune opération", style='Dim.TLabel')
        self.history_summary_lbl.pack(pady=5)

    def _build_status_bar(self):
        """Barre de statut."""
        status_frame = ttk.Frame(self.root, padding=(10, 4))
        status_frame.pack(fill='x', side='bottom')
        self.progress = ttk.Progressbar(status_frame, mode='determinate', length=300)
        self.progress.pack(side='left', padx=(0, 10))
        self.status_label = ttk.Label(status_frame, text="Prêt — glissez un fichier CSV", style='Dim.TLabel')
        self.status_label.pack(side='left')

    def _setup_drag_drop(self):
        """Configure le drag & drop de fichier."""
        # Tkinter natif ne supporte pas le DnD, on utilise tkinterdnd2 si disponible
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            # Si root est TkinterDnD.Tk, enregistrer le drop
            if hasattr(self.root, 'drop_target_register'):
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind('<<Drop>>', self._on_drop)
                logger.info("Drag & drop activé (tkinterdnd2)")
                return
        except ImportError:
            pass

        # Fallback: zone de drop visuelle avec double-clic
        self.root.bind('<Double-Button-1>', lambda e: self._on_load())
        logger.info("Drag & drop non disponible — double-clic pour charger")

    def _on_drop(self, event):
        """Callback drag & drop."""
        filepath = event.data.strip('{}')  # Tkinter DnD encapsule dans {}
        if filepath.lower().endswith(('.csv', '.txt', '.tsv')):
            self._load_file(Path(filepath))
        else:
            messagebox.showwarning("Format", "Fichier CSV/TXT/TSV attendu")

    # ── Actions ──

    def _on_load(self):
        filepath = filedialog.askopenfilename(
            title="Sélectionner un fichier CSV",
            filetypes=[("CSV", "*.csv"), ("Texte", "*.txt"), ("TSV", "*.tsv"), ("Tous", "*.*")]
        )
        if filepath:
            self._load_file(Path(filepath))

    def _load_file(self, filepath: Path):
        self.current_file = filepath
        self.lbl_file.config(text=f"📄 {filepath.name}", style='TLabel')
        self._set_status("Détection en cours...")
        self.progress['value'] = 0
        self.history.clear()

        def task():
            try:
                result = run_detection(filepath, progress_callback=self._update_progress)
                self.root.after(0, lambda: self._on_detection_done(result))
            except Exception as e:
                logger.exception("Erreur détection")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_detection_done(self, result: DetectionResult):
        self.detection = result
        self.analysis = None
        self.fix_result = None
        self.file_stats = None

        # KPIs
        sep_display = repr(result.separator).replace("'", "")
        self.kpi_labels['separator'].config(text=sep_display, style='CardKPI.TLabel')
        self.kpi_labels['encoding'].config(text=result.encoding, style='CardKPI.TLabel')
        self.kpi_labels['total_lines'].config(text=f"{result.total_lines:,}", style='CardKPI.TLabel')
        self.kpi_labels['expected_cols'].config(text=str(result.expected_columns), style='CardKPI.TLabel')
        self.kpi_labels['decimal_format'].config(text=result.decimal_format or '—', style='CardKPI.TLabel')

        conformity = result.conformity_ratio * 100
        kpi_style = 'CardGood.TLabel' if conformity > 95 else ('CardWarning.TLabel' if conformity > 80 else 'CardBad.TLabel')
        self.kpi_labels['conformity_pct'].config(text=f"{conformity:.1f}%", style=kpi_style)

        total_data = sum(result.column_count_distribution.values())
        expected_count = result.column_count_distribution.get(result.expected_columns, 0)
        non_conforming = total_data - expected_count
        self.kpi_labels['conforming'].config(text=f"{expected_count:,}", style='CardGood.TLabel')
        nc_style = 'CardBad.TLabel' if non_conforming > 0 else 'CardGood.TLabel'
        self.kpi_labels['non_conforming'].config(text=f"{non_conforming:,}", style=nc_style)

        # Distribution
        self.dist_text.config(state='normal')
        self.dist_text.delete('1.0', 'end')
        for n_cols, count in sorted(result.column_count_distribution.items()):
            marker = " ← attendu" if n_cols == result.expected_columns else ""
            pct = count / total_data * 100 if total_data > 0 else 0
            bar = "█" * int(pct / 2)
            self.dist_text.insert('end', f"  {n_cols} colonnes : {count:>8,} ({pct:5.1f}%) {bar}{marker}\n")
        self.dist_text.config(state='disabled')

        # Colonnes
        self.col_tree.delete(*self.col_tree.get_children())
        for p in result.column_profiles:
            header_name = result.header[p.index].strip() if p.index < len(result.header) else ''
            examples = ', '.join(p.sample_values[:3])
            self.col_tree.insert('', 'end', values=(
                p.index, header_name, p.dominant_type.name,
                f"{p.confidence:.0%}", f"{p.empty_ratio:.0%}",
                p.decimal_format or '—', examples
            ))

        # Enable buttons
        for btn in [self.btn_analyze, self.btn_stats, self.btn_preview, self.btn_save_tpl,
                     self.btn_quality, self.btn_dedup, self.btn_compare, self.btn_anonymize]:
            btn.config(state='normal')
        for btn in [self.btn_fix, self.btn_export, self.btn_undo]:
            btn.config(state='disabled')

        self._set_status(f"Détection OK — {result.total_lines:,} lignes, {result.expected_columns} colonnes")
        self.progress['value'] = 100

    def _on_analyze(self):
        if not self.detection:
            return
        self._set_status("Analyse en cours...")
        self.progress['value'] = 0

        def task():
            try:
                result = analyze_file(self.detection, progress_callback=self._update_progress)
                self.root.after(0, lambda: self._on_analysis_done(result))
            except Exception as e:
                logger.exception("Erreur analyse")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_analysis_done(self, result: AnalysisResult):
        self.analysis = result

        self.kpi_labels['conforming'].config(text=f"{result.conforming_lines:,}")
        nc_style = 'CardBad.TLabel' if result.non_conforming_lines > 0 else 'CardGood.TLabel'
        self.kpi_labels['non_conforming'].config(text=f"{result.non_conforming_lines:,}", style=nc_style)
        pct_style = 'CardGood.TLabel' if result.conformity_percentage > 95 else (
            'CardWarning.TLabel' if result.conformity_percentage > 80 else 'CardBad.TLabel')
        self.kpi_labels['conformity_pct'].config(text=f"{result.conformity_percentage:.1f}%", style=pct_style)

        # Issues list
        self.issues_tree.delete(*self.issues_tree.get_children())
        self._all_issues_data = []
        for issue in result.all_issues[:5000]:
            vals = (issue.line_number, issue.actual_columns, issue.shift_type,
                    issue.shift_start_column, issue.details)
            self._all_issues_data.append(vals)
            self.issues_tree.insert('', 'end', values=vals)

        self.btn_fix.config(state='normal')
        self.notebook.select(self.tab_issues)
        self._set_status(f"Analyse OK — {result.non_conforming_lines:,} anomalies")
        self.progress['value'] = 100

    def _on_stats(self):
        if not self.detection:
            return
        self._set_status("Calcul des statistiques...")
        self.progress['value'] = 0

        def task():
            try:
                result = compute_statistics(self.detection, progress_callback=self._update_progress)
                self.root.after(0, lambda: self._on_stats_done(result))
            except Exception as e:
                logger.exception("Erreur stats")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_stats_done(self, result: FileStats):
        self.file_stats = result
        self.stats_tree.delete(*self.stats_tree.get_children())

        for cs in result.column_stats:
            min_val = f"{cs.numeric_min:,.2f}" if cs.numeric_min is not None else (cs.date_min or '—')
            max_val = f"{cs.numeric_max:,.2f}" if cs.numeric_max is not None else (cs.date_max or '—')
            mean_val = f"{cs.numeric_mean:,.2f}" if cs.numeric_mean is not None else (
                f"{cs.text_avg_length:.0f} car." if cs.text_avg_length is not None else '—')
            outliers = str(cs.outliers_count) if cs.outliers_count > 0 else '—'
            top = ', '.join(f"{v} ({c})" for v, c in cs.top_values[:3]) if cs.top_values else '—'

            self.stats_tree.insert('', 'end', values=(
                f"[{cs.index}] {cs.header}", cs.cell_type.name,
                f"{cs.completeness:.1f}%", f"{cs.unique_count:,}",
                min_val, max_val, mean_val, outliers, top
            ))

        self.stats_summary.config(
            text=f"Lignes: {result.total_rows:,} | Doublons: {result.duplicate_rows:,} | "
                 f"Vides: {result.empty_rows:,} | Remplissage: {result.overall_fill_rate:.1f}%",
            style='TLabel'
        )
        self.notebook.select(self.tab_stats)
        self._set_status(f"Statistiques OK — {result.duplicate_rows:,} doublons détectés")
        self.progress['value'] = 100

    def _on_fix(self):
        if not self.detection or not self.analysis:
            return
        self._set_status("Corrections en cours...")
        self.progress['value'] = 0

        def task():
            try:
                result = apply_fixes(
                    self.detection, self.analysis,
                    fix_dates=self.var_fix_dates.get(),
                    fix_decimals=self.var_fix_decimals.get(),
                    fix_shifts=self.var_fix_shifts.get(),
                    progress_callback=self._update_progress
                )
                self.root.after(0, lambda: self._on_fix_done(result))
            except Exception as e:
                logger.exception("Erreur correction")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_fix_done(self, result: FixResult):
        self.fix_result = result

        # Record in history
        changes = {}
        for line_num, cells in result.corrected_lines.items():
            changes[line_num] = ([], cells)  # before unknown in batch mode
        self.history.record(
            'batch_fix',
            f"Corrections: {result.date_fixes} dates, {result.decimal_fixes} déc., {result.realignment_fixes} align.",
            changes
        )

        for key, value in result.summary.items():
            if key in self.fix_labels:
                self.fix_labels[key].config(text=f"{value:,}")

        self.btn_export.config(state='normal')
        self.btn_undo.config(state='normal')
        self.notebook.select(self.tab_fixes)
        self._update_history_display()
        self._set_status(f"Corrections OK — {result.total_corrections:,} corrections")
        self.progress['value'] = 100

    def _on_preview(self):
        if not self.detection:
            return
        from ui.preview import PreviewWindow
        PreviewWindow(self.root, self.detection, self.fix_result, dark_theme=DARK)

    def _on_export(self):
        if not self.detection or not self.analysis or not self.fix_result:
            return

        # ── Dialogue choix format + séparateur ──
        dialog = _ExportDialog(self.root, self.fix_result)
        self.root.wait_window(dialog)

        if not dialog.result:
            return

        fmt = dialog.result['format']
        csv_sep = dialog.result.get('separator', '§')

        ext = '.xlsx' if fmt == 'xlsx' else '.csv'
        default_name = self.current_file.stem + "_corrigé" + ext

        if fmt == 'xlsx':
            filetypes = [("Excel", "*.xlsx"), ("Tous", "*.*")]
        else:
            filetypes = [("CSV", "*.csv"), ("Tous", "*.*")]

        output = filedialog.asksaveasfilename(
            title="Enregistrer", defaultextension=ext, initialfile=default_name,
            filetypes=filetypes
        )
        if not output:
            return

        self._set_status("Export en cours...")
        self.progress['value'] = 0

        def task():
            try:
                if fmt == 'xlsx':
                    path = export_xlsx(self.detection, self.analysis, self.fix_result,
                                      Path(output), progress_callback=self._update_progress)
                else:
                    path = export_csv_clean(self.detection, self.fix_result,
                                           Path(output), progress_callback=self._update_progress,
                                           separator=csv_sep)
                self.root.after(0, lambda: self._on_export_done(path, fmt, csv_sep))
            except Exception as e:
                logger.exception("Erreur export")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_export_done(self, path: Path, fmt: str, csv_sep: str = '§'):
        self._set_status(f"Export OK : {path.name}")
        self.progress['value'] = 100

        if fmt == 'csv':
            sep_display = CSV_SEPARATORS.get(csv_sep, csv_sep)
            msg = (f"Fichier exporté :\n{path}\n\n"
                   f"Séparateur utilisé : {sep_display}\n"
                   f"Pensez à utiliser ce séparateur lors de la réouverture.\n\n"
                   f"Ouvrir le fichier maintenant ?")
        else:
            msg = f"Fichier exporté :\n{path}\n\nOuvrir le fichier maintenant ?"

        if messagebox.askyesno("Export terminé", msg):
            self._open_file(path)

    @staticmethod
    def _open_file(path: Path):
        """Ouvre un fichier avec l'application par défaut du système."""
        import subprocess
        import sys
        try:
            if sys.platform == 'win32':
                import os
                os.startfile(str(path))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(path)])
            else:
                subprocess.Popen(['xdg-open', str(path)])
        except Exception as e:
            logger.warning(f"Impossible d'ouvrir {path}: {e}")

    def _on_undo(self):
        entry = self.history.undo()
        if entry:
            # Remove corrected lines
            if self.fix_result:
                for line_num in entry.changes:
                    self.fix_result.corrected_lines.pop(line_num, None)
            self._update_history_display()
            self._set_status(f"Undo: {entry.operation}")
            self.btn_undo.config(state='normal' if self.history.can_undo else 'disabled')

    def _on_redo(self):
        entry = self.history.redo()
        if entry:
            if self.fix_result:
                for line_num, (_, after) in entry.changes.items():
                    self.fix_result.corrected_lines[line_num] = after
            self._update_history_display()
            self._set_status(f"Redo: {entry.operation}")

    def _on_clear_history(self):
        if messagebox.askyesno("Confirmer", "Effacer tout l'historique ?"):
            self.history.clear()
            self._update_history_display()

    def _on_save_template(self):
        if not self.detection:
            return
        name = tk.simpledialog.askstring("Template", "Nom du template :", parent=self.root) if hasattr(tk, 'simpledialog') else None
        if not name:
            from tkinter import simpledialog
            name = simpledialog.askstring("Template", "Nom du template :", parent=self.root)
        if name:
            from utils.templates import save_template
            path = save_template(self.detection, name)
            messagebox.showinfo("Template", f"Sauvegardé : {path}")

    def _on_load_template(self):
        filepath = filedialog.askopenfilename(
            title="Charger un template", filetypes=[("JSON", "*.json")],
            initialdir=str(Path.home() / '.csvtraitement' / 'templates')
        )
        if filepath:
            from utils.templates import load_template, template_to_profiles
            template = load_template(Path(filepath))
            messagebox.showinfo("Template",
                f"Template chargé : {template['meta']['name']}\n"
                f"Colonnes: {template['structure']['expected_columns']}\n"
                f"Séparateur: {template['structure']['separator']}")

    # ── Qualité ──

    def _build_quality_tab(self):
        """Onglet score de qualité."""
        # Score global en haut
        top = ttk.Frame(self.tab_quality, padding=10)
        top.pack(fill='x')
        self.quality_grade_lbl = ttk.Label(top, text="—", style='CardKPI.TLabel',
                                           font=('JetBrains Mono', 28, 'bold'))
        self.quality_grade_lbl.pack(side='left', padx=20)
        self.quality_score_lbl = ttk.Label(top, text="Lancer 🏆 Qualité pour scorer",
                                           style='Dim.TLabel')
        self.quality_score_lbl.pack(side='left', padx=10)
        self.quality_summary_lbl = ttk.Label(top, text="", style='Dim.TLabel')
        self.quality_summary_lbl.pack(side='left', padx=10)

        # Tableau par colonne
        cols = ('Colonne', 'Score', 'Note', 'Rempli', 'Types', 'Outliers', 'Format', 'Diversité', 'Problèmes')
        self.quality_tree = ttk.Treeview(self.tab_quality, columns=cols, show='headings', height=18)
        for c in cols:
            self.quality_tree.heading(c, text=c)
            w = 80 if c not in ('Colonne', 'Problèmes') else 180
            self.quality_tree.column(c, width=w)
        sb = ttk.Scrollbar(self.tab_quality, orient='vertical', command=self.quality_tree.yview)
        self.quality_tree.configure(yscrollcommand=sb.set)
        self.quality_tree.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=(0, 10))
        sb.pack(side='right', fill='y', pady=(0, 10), padx=(0, 10))

    def _build_dedup_tab(self):
        """Onglet détection de doublons."""
        # Options
        opts = ttk.LabelFrame(self.tab_dedup, text="Options de détection", padding=10)
        opts.pack(fill='x', padx=10, pady=5)

        self.var_dedup_fuzzy = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Fuzzy matching (similarité > 85%)",
                        variable=self.var_dedup_fuzzy).pack(anchor='w')

        self.var_dedup_normalize = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Normaliser (accents, casse, espaces)",
                        variable=self.var_dedup_normalize).pack(anchor='w')

        # Résumé
        self.dedup_summary_lbl = ttk.Label(self.tab_dedup, text="Lancer 🔗 Doublons pour détecter",
                                           style='Dim.TLabel')
        self.dedup_summary_lbl.pack(pady=5)

        # Tableau
        cols = ('Groupe', 'Type', 'Similarité', 'Lignes', 'Nb', 'Clé', 'Différences')
        self.dedup_tree = ttk.Treeview(self.tab_dedup, columns=cols, show='headings', height=16)
        for c in cols:
            self.dedup_tree.heading(c, text=c)
            w = 60 if c in ('Groupe', 'Nb') else (80 if c in ('Type', 'Similarité') else 200)
            self.dedup_tree.column(c, width=w)
        sb = ttk.Scrollbar(self.tab_dedup, orient='vertical', command=self.dedup_tree.yview)
        self.dedup_tree.configure(yscrollcommand=sb.set)
        self.dedup_tree.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=(0, 10))
        sb.pack(side='right', fill='y', pady=(0, 10), padx=(0, 10))

    def _on_quality(self):
        if not self.detection:
            return
        self._set_status("Calcul de la qualité...")
        self.progress['value'] = 0

        def task():
            try:
                report = compute_quality(self.detection, progress_callback=self._update_progress)
                self.root.after(0, lambda: self._on_quality_done(report))
            except Exception as e:
                logger.exception("Erreur qualité")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_quality_done(self, report: QualityReport):
        self.quality_report = report

        # Score global
        grade_colors = {'A': 'CardGood.TLabel', 'B': 'CardGood.TLabel',
                        'C': 'CardWarning.TLabel', 'D': 'CardBad.TLabel', 'F': 'CardBad.TLabel'}
        self.quality_grade_lbl.config(text=report.overall_grade,
                                      style=grade_colors.get(report.overall_grade, 'CardKPI.TLabel'))
        self.quality_score_lbl.config(
            text=f"{report.overall_score:.1f}/100 — Doublons: {report.duplicate_rate:.1f}%, "
                 f"Vides: {report.empty_row_rate:.1f}%",
            style='TLabel'
        )
        if report.summary:
            self.quality_summary_lbl.config(text=' | '.join(report.summary[:3]))

        # Tableau par colonne
        self.quality_tree.delete(*self.quality_tree.get_children())
        for cq in report.column_scores:
            issues_str = '; '.join(cq.issues[:2]) if cq.issues else '—'
            self.quality_tree.insert('', 'end', values=(
                f"[{cq.index}] {cq.header}", f"{cq.overall_score:.0f}",
                cq.grade, f"{cq.fill_score:.0f}%",
                f"{cq.type_consistency_score:.0f}%", f"{cq.outlier_score:.0f}%",
                f"{cq.format_homogeneity_score:.0f}%", f"{cq.uniqueness_score:.0f}%",
                issues_str
            ))

        self.notebook.select(self.tab_quality)
        self._set_status(f"Qualité: {report.overall_score:.1f}/100 ({report.overall_grade})")
        self.progress['value'] = 100

    def _on_dedup(self):
        if not self.detection:
            return
        self._set_status("Recherche de doublons...")
        self.progress['value'] = 0

        def task():
            try:
                result = find_duplicates(
                    self.detection,
                    enable_fuzzy=self.var_dedup_fuzzy.get(),
                    normalize=self.var_dedup_normalize.get(),
                    progress_callback=self._update_progress
                )
                self.root.after(0, lambda: self._on_dedup_done(result))
            except Exception as e:
                logger.exception("Erreur doublons")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_dedup_done(self, result: DedupResult):
        self.dedup_result = result
        self.dedup_summary_lbl.config(
            text=f"{result.exact_duplicate_groups} groupes exacts ({result.exact_duplicate_rows} lignes), "
                 f"{result.fuzzy_duplicate_groups} groupes fuzzy ({result.fuzzy_duplicate_rows} lignes) — "
                 f"Taux: {result.duplicate_rate:.1f}%",
            style='TLabel'
        )

        self.dedup_tree.delete(*self.dedup_tree.get_children())
        for g in result.groups[:5000]:
            lines_str = ', '.join(str(l) for l in g.line_numbers[:5])
            if len(g.line_numbers) > 5:
                lines_str += f'... (+{len(g.line_numbers)-5})'
            diffs_str = ', '.join(f"col{d[0]}" for d in g.differences[:3]) if g.differences else '—'
            self.dedup_tree.insert('', 'end', values=(
                g.group_id, g.match_type, f"{g.similarity:.0%}",
                lines_str, g.count, g.key[:60], diffs_str
            ))

        self.notebook.select(self.tab_dedup)
        self._set_status(f"Doublons: {result.total_duplicate_rows} lignes dupliquées")
        self.progress['value'] = 100

    def _on_compare(self):
        """Ouvre un dialogue pour comparer avec un second fichier."""
        if not self.detection:
            return
        filepath = filedialog.askopenfilename(
            title="Sélectionner le second fichier (B) à comparer",
            filetypes=[("CSV", "*.csv"), ("Texte", "*.txt"), ("TSV", "*.tsv"), ("Tous", "*.*")]
        )
        if not filepath:
            return

        self._set_status("Comparaison en cours...")
        self.progress['value'] = 0

        def task():
            try:
                detection_b = run_detection(Path(filepath))
                result = compare_files(
                    self.detection, detection_b,
                    progress_callback=self._update_progress
                )
                self.root.after(0, lambda: self._on_compare_done(result))
            except Exception as e:
                logger.exception("Erreur comparaison")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_compare_done(self, result: ComparisonResult):
        status_icons = {
            RowStatus.ADDED: '➕', RowStatus.REMOVED: '➖',
            RowStatus.MODIFIED: '✏️', RowStatus.UNCHANGED: '✓'
        }

        msg = (
            f"Comparaison: {result.file_a} vs {result.file_b}\n\n"
            f"Identiques   : {result.unchanged:,}\n"
            f"Ajoutées     : {result.added:,}\n"
            f"Supprimées   : {result.removed:,}\n"
            f"Modifiées    : {result.modified:,}\n"
            f"Cellules modifiées : {result.total_cell_changes:,}\n"
            f"Similarité   : {result.similarity_percentage:.1f}%\n"
        )

        if result.modified > 0 and result.diffs:
            msg += f"\nPremières différences :\n"
            for diff in result.diffs[:10]:
                icon = status_icons.get(diff.status, '?')
                if diff.cell_diffs:
                    changes = ', '.join(f"{d.column_name}: '{d.value_a}'→'{d.value_b}'" for d in diff.cell_diffs[:3])
                    msg += f"  {icon} L{diff.line_a or diff.line_b}: {changes}\n"
                else:
                    msg += f"  {icon} L{diff.line_a or diff.line_b}: {diff.status}\n"

        messagebox.showinfo("Résultat de la comparaison", msg)
        self._set_status(f"Comparaison OK — {result.total_differences:,} différences")
        self.progress['value'] = 100

    def _on_merge(self):
        """Ouvre un dialogue pour sélectionner des fichiers à fusionner."""
        files = filedialog.askopenfilenames(
            title="Sélectionner les fichiers CSV à fusionner",
            filetypes=[("CSV", "*.csv"), ("Texte", "*.txt"), ("TSV", "*.tsv"), ("Tous", "*.*")]
        )
        if not files or len(files) < 2:
            messagebox.showinfo("Fusion", "Sélectionnez au moins 2 fichiers.")
            return

        self._set_status("Fusion en cours...")
        self.progress['value'] = 0

        def task():
            try:
                detections = []
                for f in files:
                    detections.append(run_detection(Path(f)))

                # Vérifier compatibilité
                issues = []
                ref = detections[0]
                for i, det in enumerate(detections[1:], 1):
                    compat = check_compatibility(ref, det)
                    for issue in compat.issues:
                        issues.append(f"[{det.filepath.name}] {issue.severity}: {issue.message}")

                merge_result = merge_files(
                    detections, skip_empty_rows=True,
                    progress_callback=self._update_progress
                )
                self.root.after(0, lambda: self._on_merge_done(merge_result, issues))
            except Exception as e:
                logger.exception("Erreur fusion")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_merge_done(self, result: MergeResult, issues: list[str]):
        msg = (
            f"Fusion terminée:\n\n"
            f"Fichiers: {result.total_files}\n"
            f"Lignes totales: {result.total_rows:,}\n"
            f"Lignes vides ignorées: {result.skipped_empty_rows:,}\n"
            f"Lignes erreurs ignorées: {result.skipped_error_rows:,}\n"
        )
        if issues:
            msg += f"\nAvertissements:\n" + '\n'.join(f"  • {i}" for i in issues[:10])

        msg += "\n\nExporter le résultat fusionné ?"

        if messagebox.askyesno("Résultat de la fusion", msg):
            output = filedialog.asksaveasfilename(
                title="Enregistrer le fichier fusionné",
                defaultextension='.csv',
                filetypes=[("CSV", "*.csv"), ("Tous", "*.*")]
            )
            if output:
                with open(output, 'w', encoding='utf-8', newline='') as f:
                    sep = '§'
                    f.write(sep.join(result.output_headers) + '\n')
                    for row in result.merged_rows:
                        f.write(sep.join(row) + '\n')
                messagebox.showinfo("Export", f"Fichier fusionné exporté:\n{output}")

        self._set_status(f"Fusion OK — {result.total_rows:,} lignes")
        self.progress['value'] = 100

    def _on_anonymize(self):
        """Anonymise les données sensibles détectées."""
        if not self.detection:
            return

        self._set_status("Détection des données sensibles...")
        self.progress['value'] = 0

        def task():
            try:
                # Détecter automatiquement les colonnes sensibles
                profiles = self.detection.column_profiles
                rules = []
                for p in profiles:
                    if p.sample_values:
                        stype = detect_column_sensitivity(p.sample_values)
                        if stype:
                            header = self.detection.header[p.index].strip() if p.index < len(self.detection.header) else ''
                            rules.append(AnonymizationRule(
                                column_index=p.index,
                                sensitive_type=stype,
                                mask_mode=MaskMode.PARTIAL,
                            ))

                self.root.after(0, lambda: self._on_anonymize_detected(rules))
            except Exception as e:
                logger.exception("Erreur anonymisation")
                self.root.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _on_anonymize_detected(self, rules: list):
        if not rules:
            messagebox.showinfo("Anonymisation",
                "Aucune donnée sensible détectée automatiquement.\n"
                "Vous pouvez configurer manuellement les colonnes à masquer.")
            self._set_status("Aucune donnée sensible détectée")
            return

        desc = '\n'.join(
            f"  • Col {r.column_index} ({self.detection.header[r.column_index].strip()}): {r.sensitive_type.name}"
            for r in rules
        )
        msg = f"Données sensibles détectées:\n{desc}\n\nAppliquer le masquage ?"

        if messagebox.askyesno("Anonymisation", msg):
            messagebox.showinfo("Anonymisation",
                f"{len(rules)} règle(s) configurées.\n"
                f"Le masquage sera appliqué lors de l'export.")
        self._set_status(f"Anonymisation: {len(rules)} colonnes sensibles détectées")
        self.progress['value'] = 100

    def _on_cloud(self):
        """Détecte et affiche les emplacements cloud disponibles."""
        locations = detect_cloud_locations()
        if not locations:
            messagebox.showinfo("Cloud",
                "Aucun service cloud détecté.\n\n"
                "Services supportés: OneDrive, SharePoint, pCloud,\n"
                "Google Drive, Dropbox.\n\n"
                "Installez le client de synchronisation du service souhaité.")
            return

        msg = "Emplacements cloud détectés:\n\n"
        for loc in locations:
            msg += f"  ☁ {loc.name} ({loc.provider})\n    {loc.local_path}\n\n"
        msg += "Ouvrir un fichier depuis le cloud ?"

        if messagebox.askyesno("Cloud", msg):
            # Proposer de naviguer dans le premier emplacement
            initial_dir = str(locations[0].local_path) if locations[0].local_path else None
            filepath = filedialog.askopenfilename(
                title="Ouvrir depuis le cloud",
                initialdir=initial_dir,
                filetypes=[("CSV", "*.csv"), ("Texte", "*.txt"), ("TSV", "*.tsv"), ("Tous", "*.*")]
            )
            if filepath:
                self._load_file(Path(filepath))

    # ── Filtres ──

    def _filter_issues(self):
        query = self.issue_filter_var.get().lower()
        self.issues_tree.delete(*self.issues_tree.get_children())
        for vals in self._all_issues_data:
            if query in str(vals).lower():
                self.issues_tree.insert('', 'end', values=vals)

    def _reset_issues_filter(self):
        self.issue_filter_var.set("")
        self.issues_tree.delete(*self.issues_tree.get_children())
        for vals in self._all_issues_data:
            self.issues_tree.insert('', 'end', values=vals)

    def _update_history_display(self):
        self.history_tree.delete(*self.history_tree.get_children())
        for entry in self.history.entries:
            self.history_tree.insert('', 'end', values=(
                entry.timestamp[:19], entry.operation,
                entry.description, entry.lines_affected, entry.corrections_count
            ))
        summary = self.history.get_summary()
        self.history_summary_lbl.config(
            text=f"{summary['total_operations']} opérations | {summary['total_corrections']} corrections"
        )

    # ── Utils ──

    def _update_progress(self, current: int, total: int, message: str):
        def update():
            if total > 0:
                self.progress['value'] = (current / total) * 100
            self.status_label.config(text=message)
        self.root.after(0, update)

    def _set_status(self, text: str):
        self.status_label.config(text=text)


class _ExportDialog(tk.Toplevel):
    """Dialogue de choix du format d'export et du séparateur CSV."""

    def __init__(self, parent, fix_result: FixResult):
        super().__init__(parent)
        self.title("Options d'export")
        self.configure(bg=DARK['bg'])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None

        pad = {'padx': 15, 'pady': 5}

        # ── Résumé corrections ──
        summary = (
            f"{fix_result.total_corrections:,} corrections : "
            f"{fix_result.date_fixes:,} dates, "
            f"{fix_result.decimal_fixes:,} décimales, "
            f"{fix_result.realignment_fixes:,} réalignements"
        )
        tk.Label(self, text=summary, bg=DARK['bg'], fg=DARK['fg'],
                 font=('Segoe UI', 9)).pack(**pad, anchor='w')

        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=15, pady=5)

        # ── Choix format ──
        tk.Label(self, text="Format de sortie :", bg=DARK['bg'], fg=DARK['accent'],
                 font=('Segoe UI', 10, 'bold')).pack(**pad, anchor='w')

        self.format_var = tk.StringVar(value='xlsx')
        fmt_frame = tk.Frame(self, bg=DARK['bg'])
        fmt_frame.pack(**pad, anchor='w')

        tk.Radiobutton(fmt_frame, text="XLSX (Excel — typé, colorisé)",
                       variable=self.format_var, value='xlsx',
                       bg=DARK['bg'], fg=DARK['fg'], selectcolor=DARK['bg_alt'],
                       activebackground=DARK['bg'], activeforeground=DARK['fg'],
                       command=self._on_format_change).pack(anchor='w')
        tk.Radiobutton(fmt_frame, text="CSV (texte — léger, configurable)",
                       variable=self.format_var, value='csv',
                       bg=DARK['bg'], fg=DARK['fg'], selectcolor=DARK['bg_alt'],
                       activebackground=DARK['bg'], activeforeground=DARK['fg'],
                       command=self._on_format_change).pack(anchor='w')

        # ── Choix séparateur CSV ──
        self.sep_frame = tk.LabelFrame(self, text="Séparateur CSV",
                                       bg=DARK['bg_card'], fg=DARK['accent'],
                                       font=('Segoe UI', 10, 'bold'))
        self.sep_frame.pack(padx=15, pady=8, fill='x')

        self.sep_var = tk.StringVar(value='§')
        for sep_key, sep_label in CSV_SEPARATORS.items():
            tk.Radiobutton(self.sep_frame, text=sep_label,
                           variable=self.sep_var, value=sep_key,
                           bg=DARK['bg_card'], fg=DARK['fg'], selectcolor=DARK['bg_alt'],
                           activebackground=DARK['bg_card'], activeforeground=DARK['fg']
                           ).pack(anchor='w', padx=10, pady=1)

        # Champ séparateur personnalisé
        custom_frame = tk.Frame(self.sep_frame, bg=DARK['bg_card'])
        custom_frame.pack(anchor='w', padx=10, pady=3)
        tk.Radiobutton(custom_frame, text="Autre :",
                       variable=self.sep_var, value='__custom__',
                       bg=DARK['bg_card'], fg=DARK['fg'], selectcolor=DARK['bg_alt'],
                       activebackground=DARK['bg_card'], activeforeground=DARK['fg']
                       ).pack(side='left')
        self.custom_entry = tk.Entry(custom_frame, width=5, bg=DARK['bg_alt'],
                                     fg=DARK['fg'], insertbackground=DARK['fg'])
        self.custom_entry.pack(side='left', padx=5)

        # Masquer au départ (format XLSX par défaut)
        self.sep_frame.pack_forget()

        # ── Boutons ──
        btn_frame = tk.Frame(self, bg=DARK['bg'])
        btn_frame.pack(pady=12)
        ttk.Button(btn_frame, text="Exporter", command=self._on_ok,
                   style='Accent.TButton').pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Annuler", command=self.destroy).pack(side='left', padx=5)

        # Centrer
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _on_format_change(self):
        if self.format_var.get() == 'csv':
            self.sep_frame.pack(padx=15, pady=8, fill='x',
                                before=self.winfo_children()[-1])  # avant les boutons
        else:
            self.sep_frame.pack_forget()

    def _on_ok(self):
        fmt = self.format_var.get()
        sep = self.sep_var.get()
        if sep == '__custom__':
            sep = self.custom_entry.get()
            if not sep or len(sep) != 1:
                messagebox.showwarning("Séparateur",
                    "Entrez exactement un caractère comme séparateur.",
                    parent=self)
                return
        self.result = {'format': fmt, 'separator': sep}
        self.destroy()
