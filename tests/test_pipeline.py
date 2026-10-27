"""Tests de bout en bout et de la logique clé ajoutée (dédup, réalignement, PII, formats, CLI).

Les données viennent de samples/erp_demo_1k.csv, export ERP SYNTHÉTIQUE produit par
scripts/generate_sample.py, avec sa vérité terrain (erp_demo_1k.truth.json).
"""

import json
import os
import random
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from core.analyzer import analyze_file
from core.anonymizer import SensitiveType, detect_sensitive_type, mask_email, mask_phone
from core.dedup import _build_blocks, _compute_similarity, find_duplicates
from core.detector import run_detection
from core.exporter import export_any
from core.fixer import apply_fixes, fix_row_types
from core.quality import _score_to_grade, compute_quality

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "samples" / "erp_demo_1k.csv"
TRUTH = json.loads(DEMO.with_suffix(".truth.json").read_text(encoding="utf-8"))
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def pipeline():
    det = run_detection(DEMO)
    ana = analyze_file(det)
    fix = apply_fixes(det, ana)
    return det, ana, fix


class TestDetectionSurExportERP:
    def test_structure_detectee(self, pipeline):
        det, _, _ = pipeline
        assert det.separator == ";"
        assert det.expected_columns == 11
        assert det.decimal_format == "FR"
        types = [p.dominant_type.name for p in det.column_profiles]
        assert types[3:7] == ["DATE", "DATE", "DECIMAL", "INTEGER"]

    def test_tous_les_decalages_trouves_sans_faux_positif(self, pipeline):
        _, ana, _ = pipeline
        truth = set(TRUTH["extra_column"]) | set(TRUTH["missing_column"])
        found = {s.line_number for s in ana.shifts}
        assert found == truth

    def test_type_de_decalage(self, pipeline):
        _, ana, _ = pipeline
        for s in ana.shifts:
            expected = "extra_column" if s.line_number in TRUTH["extra_column"] else "missing_column"
            assert s.shift_type == expected


class TestCorrections:
    def test_lignes_decalees_retrouvent_leurs_valeurs(self, pipeline):
        det, _, fix = pipeline
        for ln_s, clean in TRUTH["shift_expected"].items():
            ln = int(ln_s)
            clean = list(clean)
            if ln in TRUTH["missing_column"]:
                clean[6] = ""
            expected, _ = fix_row_types(clean, det.column_profiles, ln)
            got = fix.corrected_lines[ln]
            # aux espaces près (skipinitialspace du lecteur CSV)
            assert ["".join(c.split()) for c in got] == ["".join(c.split()) for c in expected], ln

    def test_champ_texte_coupe_est_recolle(self, pipeline):
        _, _, fix = pipeline
        ln = TRUTH["extra_column"][0]
        assert ";" in fix.corrected_lines[ln][2]

    def test_dates_invalides_videes(self, pipeline):
        _, _, fix = pipeline
        lines = {c.line_number for c in fix.corrections if c.correction_type == "date_invalid"}
        assert lines == set(TRUTH["invalid_date"])

    def test_parentheses_comptables_negatives(self, pipeline):
        _, _, fix = pipeline
        for c in fix.corrections:
            if c.line_number in TRUTH["accounting_neg"] and c.column_index == 5:
                assert c.corrected_value.startswith("-")


class TestDedup:
    def test_doublons_exacts_et_flous(self):
        det = run_detection(DEMO)
        dd = find_duplicates(det)
        exact = {ln for g in dd.groups if g.is_exact for ln in g.line_numbers[1:]}
        fuzzy = {max(g.line_numbers) for g in dd.groups if not g.is_exact}
        assert exact == set(TRUTH["exact_duplicate"])
        assert fuzzy == set(TRUTH["near_duplicate"])

    def test_prefiltre_ne_change_pas_la_decision(self):
        rng = random.Random(0)
        alphabet = "abcdefgh |;0123"
        for _ in range(3000):
            a = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 60)))
            b = list(a)
            for _ in range(rng.randint(0, 20)):
                b[rng.randrange(len(b))] = rng.choice(alphabet)
            b = "".join(b) + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 10)))
            for t in (0.5, 0.85, 0.95):
                ref = SequenceMatcher(None, a, b).ratio() >= t if a != b else True
                assert (_compute_similarity(a, b, t) >= t) == ref

    def test_blocage_repli_sans_colonne_discriminante(self):
        entries = [(f"x|{v}", (i, [])) for i, v in enumerate(["a", "a", "b", "b"])]
        # cardinalité 2/4 = 0.5 pour la colonne 1 -> discriminante ; colonne 0 constante
        blocks = _build_blocks(entries, [0, 1])
        assert sorted(map(sorted, blocks)) == [[0, 1], [2, 3]]
        entries = [("abc|z", (i, [])) for i in range(10)]  # aucune colonne discriminante
        assert _build_blocks(entries, [0, 1]) == [list(range(10))]


class TestAnonymisation:
    def test_detection(self):
        assert detect_sensitive_type("j.dupont@exemple.fr") == SensitiveType.EMAIL
        assert detect_sensitive_type("06 12 34 56 78") == SensitiveType.PHONE_FR
        assert detect_sensitive_type("Lyon") is None

    def test_masquage(self):
        assert mask_email("jean.dupont@mail.com") == "j***@m***.com"
        assert mask_phone("06 12 34 56 78") == "06 ** ** ** 78"


class TestQualite:
    def test_notes(self):
        assert _score_to_grade(95) == "A"
        assert _score_to_grade(10) == "F"

    def test_score_export_erp(self):
        q = compute_quality(run_detection(DEMO))
        assert 0 < q.overall_score <= 100


@pytest.mark.parametrize("fmt", ["csv", "json", "parquet", "xlsx", "ods"])
def test_export_puis_relecture(pipeline, tmp_path, fmt):
    pytest.importorskip({"parquet": "pyarrow", "ods": "odf"}.get(fmt, "csv"))
    det, ana, fix = pipeline
    out = export_any(det, ana, fix, tmp_path / f"out.{fmt}", fmt=fmt, csv_separator=";")
    det2 = run_detection(out)
    assert det2.expected_columns == 11
    assert det2.total_lines == det.total_lines


def test_cli_analyse_ne_plante_pas():
    """Régression : `sheet_name` était lu avant d'être défini dans run_single."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "main.py"), "cli", "--file", str(FIXTURES / "shifted.csv")],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "Traitement terminé" in r.stdout


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    return path


def test_comparaison_avec_cle_primaire(tmp_path):
    """3 lignes supprimées, 2 modifiées, 1 ajoutée : la clé (col 0) doit le voir."""
    from core.comparator import compare_files
    lines = DEMO.read_text(encoding="utf-8").splitlines()
    v2 = [l for i, l in enumerate(lines) if i not in (10, 20, 30)]
    v2[5] = v2[5].rsplit(";", 1)[0] + ";VilleModifiee"
    v2[6] = v2[6].rsplit(";", 1)[0] + ";AutreVille"
    v2.append("CMD4599999999;C11111;Nouvelle Societe SAS;01.01.2025;02.01.2025;1.000,00;1;"
              "EN_COURS;x@exemple-fictif.fr;01 00 00 00 00;Lyon")
    det_b = run_detection(_write(tmp_path / "v2.csv", v2))
    cmp = compare_files(run_detection(DEMO), det_b, key_columns=[0])
    assert (cmp.added, cmp.removed, cmp.modified) == (1, 3, 2)


def test_rapprochement_detecte_les_ecarts_de_montant(tmp_path):
    from core.reconciler import reconcile
    lines = DEMO.read_text(encoding="utf-8").splitlines()
    pay = ["Num_Commande;Montant_Paye"] + [
        l.split(";")[0] + ";" + l.split(";")[5] for l in lines[1:101] if l.count(";") == 10
        and l.split(";")[5][0].isdigit()]
    n = len(pay) - 1
    pay[1] = pay[1].split(";")[0] + ";1,00"
    pay[2] = pay[2].split(";")[0] + ";2,00"
    rec = reconcile(run_detection(DEMO), run_detection(_write(tmp_path / "pay.csv", pay)),
                    join_columns=[0], amount_columns=(5, 1))
    assert rec.summary["mismatched"] == 2
    assert rec.summary["matched"] == n - 2


def test_valeurs_masquees_videes(pipeline):
    """#REF!, N/A, NULL, -, ?? injectés dans Montant_HT doivent être vidés (fix_masked=True par défaut)."""
    _, _, fix = pipeline
    cleaned = {c.line_number for c in fix.corrections if c.correction_type == "masked_clean"}
    assert set(TRUTH["masked_value"]) <= cleaned
    for ln in TRUTH["masked_value"]:
        assert fix.corrected_lines[ln][5] == ""


def test_normalisation_des_espaces(tmp_path):
    src = tmp_path / "esp.csv"
    src.write_text("A;B;C\n1;Jean   Dupont ;x\n2;Marie;y\n3;Paul;z\n", encoding="utf-8")
    det = run_detection(src)
    fix = apply_fixes(det, analyze_file(det), fix_whitespace=True)
    assert fix.corrected_lines[2][1] == "Jean Dupont"
    assert fix.whitespace_fixes == 1
