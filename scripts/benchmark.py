"""Banc de mesure : volumétrie, temps par étape, mémoire, qualité des contrôles.

Exécute le pipeline réel (détection -> analyse -> corrections -> qualité -> export)
sur un fichier généré par scripts/generate_sample.py, puis confronte les résultats
à la vérité terrain (<fichier>.truth.json).

Chaque taille doit être lancée dans un processus séparé pour une mesure mémoire propre :
    python scripts/benchmark.py samples/erp_100000.csv --xlsx --dedup
Résultat : une ligne JSON ajoutée à bench_out/results.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.analyzer import analyze_file  # noqa: E402
from core.dedup import find_duplicates  # noqa: E402
from core.detector import run_detection  # noqa: E402
from core.exporter import export_csv_clean, export_xlsx  # noqa: E402
from core.fixer import apply_fixes, fix_row_types  # noqa: E402
from core.quality import compute_quality  # noqa: E402
from core.anonymizer import detect_column_sensitivity  # noqa: E402
from utils.patterns import normalize_decimal  # noqa: E402

PROC = psutil.Process()


def rss_mb() -> float:
    return PROC.memory_info().rss / 1e6


def peak_mb() -> float:
    mi = PROC.memory_info()
    return getattr(mi, "peak_wset", mi.rss) / 1e6  # peak_wset : Windows


def prf(found: set, truth: set) -> dict:
    tp = len(found & truth)
    return {
        "truth": len(truth), "found": len(found), "tp": tp,
        "precision": round(tp / len(found), 4) if found else None,
        "recall": round(tp / len(truth), 4) if truth else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--xlsx", action="store_true", help="mesurer aussi l'export XLSX")
    ap.add_argument("--dedup", action="store_true", help="mesurer aussi la déduplication")
    ap.add_argument("--label", default="")
    ap.add_argument("--legacy-realign", action="store_true",
                    help="réalignement d'origine (suppression de la cellule en trop, sans recollage)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if a.legacy_realign:
        import functools
        import core.fixer as fixer_mod
        fixer_mod.realign_row = functools.partial(fixer_mod.realign_row, merge_text=False)

    truth = json.loads(a.csv.with_suffix(".truth.json").read_text(encoding="utf-8"))
    out_dir = ROOT / "bench_out"
    out_dir.mkdir(exist_ok=True)
    res: dict = {"file": a.csv.name, "rows": truth["rows"], "size_mb": round(a.csv.stat().st_size / 1e6, 1),
                 "label": a.label, "rss_start_mb": round(rss_mb(), 1)}
    t = {}

    t0 = time.perf_counter()
    det = run_detection(a.csv)
    t["detection"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    ana = analyze_file(det)
    t["analysis"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    fix = apply_fixes(det, ana)
    t["fixes"] = time.perf_counter() - t0
    res["rss_after_fix_mb"] = round(rss_mb(), 1)

    t0 = time.perf_counter()
    q = compute_quality(det)
    t["quality"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    export_csv_clean(det, fix, out_dir / f"{a.csv.stem}_clean.csv", separator=";")
    t["export_csv"] = time.perf_counter() - t0

    if a.xlsx:
        t0 = time.perf_counter()
        export_xlsx(det, ana, fix, out_dir / f"{a.csv.stem}_clean.xlsx")
        t["export_xlsx"] = time.perf_counter() - t0

    if a.dedup:
        t0 = time.perf_counter()
        dd = find_duplicates(det)
        t["dedup"] = time.perf_counter() - t0
        dup_lines = set(dd.lines_to_remove)
        exact_lines = {ln for g in dd.groups if g.is_exact for ln in g.line_numbers[1:]}
        res["dedup_exact"] = prf(exact_lines, set(truth["exact_duplicate"]))
        fuzzy_lines = {max(g.line_numbers) for g in dd.groups if not g.is_exact}
        res["dedup_fuzzy"] = prf(fuzzy_lines, set(truth.get("near_duplicate", [])))
        res["dedup_fuzzy_comparisons"] = getattr(dd, "fuzzy_comparisons", None)
        res["dedup_total_remove"] = len(dup_lines)

    t["pipeline_core"] = t["detection"] + t["analysis"] + t["fixes"] + t["export_csv"]
    res["time_s"] = {k: round(v, 2) for k, v in t.items()}
    res["rows_per_s_core"] = int(truth["rows"] / t["pipeline_core"])
    res["peak_mb"] = round(peak_mb(), 1)

    # ── Détection ──
    res["detected"] = {
        "encoding": det.encoding, "separator": det.separator, "columns": det.expected_columns,
        "decimal_format": det.decimal_format,
        "types": [p.dominant_type.name for p in det.column_profiles],
    }

    # ── Qualité des contrôles : décalages ──
    shift_truth = set(truth["extra_column"]) | set(truth["missing_column"])
    shift_found = {s.line_number for s in ana.shifts}
    res["shift_detection"] = prf(shift_found, shift_truth)
    res["shift_type_ok"] = sum(
        1 for s in ana.shifts
        if (s.shift_type == "extra_column" and s.line_number in set(truth["extra_column"]))
        or (s.shift_type == "missing_column" and s.line_number in set(truth["missing_column"]))
    )
    res["type_mismatch_lines"] = len(ana.type_mismatches)
    res["conformity_pct"] = round(ana.conformity_percentage, 2)

    # Réalignement : la ligne corrigée retrouve-t-elle les valeurs d'origine ?
    profiles = det.column_profiles
    exact_rows = cell_ok = cell_tot = 0
    missing_set = set(truth["missing_column"])
    lost_examples = []
    for ln_s, clean in truth["shift_expected"].items():
        ln = int(ln_s)
        fixed = fix.corrected_lines.get(ln)
        if fixed is None:
            continue
        clean = list(clean)
        if ln in missing_set:
            clean[6] = ""  # la Quantite perdue est irrécupérable : l'idéal est une cellule vide
        expected, _ = fix_row_types(clean, profiles, ln)
        # comparaison aux espaces près : le lecteur CSV du projet (skipinitialspace=True)
        # supprime l'espace qui suit un séparateur, y compris dans un champ recollé
        same = ["".join(x.split()) == "".join(y.split()) for x, y in zip(fixed, expected)]
        cell_ok += sum(same)
        cell_tot += len(expected)
        if all(same):
            exact_rows += 1
        elif len(lost_examples) < 3:
            lost_examples.append({"line": ln, "expected": expected, "got": fixed})
    n_shift_rows = len(truth["shift_expected"])
    res["realign"] = {
        "rows_checked": n_shift_rows,
        "rows_identical": exact_rows,
        "rows_identical_pct": round(100 * exact_rows / n_shift_rows, 2) if n_shift_rows else None,
        "cells_identical_pct": round(100 * cell_ok / cell_tot, 2) if cell_tot else None,
        "examples_ko": lost_examples,
    }

    # Dates invalides et montants
    date_fixed = {c.line_number for c in fix.corrections if c.correction_type == "date_invalid"}
    res["invalid_date_fix"] = prf(date_fixed, set(truth["invalid_date"]))
    amt_col = 5
    us_ok = us_tot = 0
    for c in fix.corrections:
        if c.column_index == amt_col and c.line_number in set(truth["us_decimal"]):
            us_tot += 1
            if abs(float(c.corrected_value) - normalize_decimal(c.original_value)) < 1e-6 \
                    and "," not in c.corrected_value:
                us_ok += 1
    res["us_decimal_normalized"] = {"truth": len(truth["us_decimal"]), "converted": us_tot, "exact": us_ok}
    neg = sum(1 for c in fix.corrections if c.column_index == amt_col
              and c.line_number in set(truth["accounting_neg"]) and c.corrected_value.startswith("-"))
    res["accounting_neg_to_negative"] = {"truth": len(truth["accounting_neg"]), "ok": neg}
    res["fix_summary"] = fix.summary

    # Qualité
    res["quality"] = {"score": round(q.overall_score, 1), "grade": q.overall_grade,
                      "columns": {det.header[c.index].strip() if hasattr(c, "index") else str(i):
                                  round(c.overall_score, 1) for i, c in enumerate(q.column_scores)}}

    # PII (sur l'échantillon de profilage)
    res["pii"] = {det.header[p.index].strip(): (lambda s: s.name if s else None)(
        detect_column_sensitivity(p.sample_values)) for p in det.column_profiles}

    with open(out_dir / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
