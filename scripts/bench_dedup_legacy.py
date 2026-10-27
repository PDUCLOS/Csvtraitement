"""Compare la déduplication d'origine (commit cca1ef8) et la version actuelle, mêmes données.

L'ancienne version est lue depuis l'historique git (git show cca1ef8:core/dedup.py) et
chargée comme module séparé : rien n'est modifié dans le dépôt.
Usage :  python scripts/bench_dedup_legacy.py samples/erp_10000.csv [--rows-limit N]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import dedup as new_dedup  # noqa: E402
from core.detector import run_detection  # noqa: E402


def load_legacy():
    src = subprocess.run(["git", "show", "cca1ef8:core/dedup.py"], cwd=ROOT, check=True,
                         capture_output=True, text=True, encoding="utf-8").stdout
    path = ROOT / "bench_out" / "dedup_legacy.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("dedup_legacy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def legacy_pairs(det, key_columns) -> int:
    """Nombre de paires que l'ancien blocage (préfixe 3 caractères) fait comparer."""
    from utils.chunked_reader import read_chunks
    blocks: dict[str, int] = defaultdict(int)
    seen = set()
    for chunk in read_chunks(det.filepath, det.separator, det.encoding, skip_header=True):
        for cells in chunk:
            k = new_dedup._build_key(cells, key_columns)
            if k not in seen:
                seen.add(k)
                blocks[k[:3]] += 1
    return sum(n * (n - 1) // 2 for n in blocks.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--keys", default="all", help="'all' (défaut CLI) ou liste d'indices, ex. 1,2,3")
    ap.add_argument("--skip-legacy", action="store_true")
    a = ap.parse_args()
    truth = json.loads(a.csv.with_suffix(".truth.json").read_text(encoding="utf-8"))
    det = run_detection(a.csv)
    keys = list(range(det.expected_columns)) if a.keys == "all" else [int(x) for x in a.keys.split(",")]
    res = {"file": a.csv.name, "rows": truth["rows"], "keys": a.keys,
           "legacy_pairs": legacy_pairs(det, keys)}
    runs = [("new", new_dedup)] + ([] if a.skip_legacy else [("legacy", load_legacy())])
    for name, mod in runs:
        t0 = time.perf_counter()
        dd = mod.find_duplicates(det, key_columns=keys)
        el = time.perf_counter() - t0
        fuzzy = {max(g.line_numbers) for g in dd.groups if g.match_type == "fuzzy"}
        exact = {ln for g in dd.groups if g.match_type == "exact" for ln in g.line_numbers[1:]}
        res[name] = {"time_s": round(el, 2), "exact_found": len(exact),
                     "exact_tp": len(exact & set(truth["exact_duplicate"])),
                     "fuzzy_found": len(fuzzy), "fuzzy_tp": len(fuzzy & set(truth["near_duplicate"])),
                     "comparisons": getattr(dd, "fuzzy_comparisons", None)}
        print(name, res[name], flush=True)
    res["truth"] = {"exact": len(truth["exact_duplicate"]), "near": len(truth["near_duplicate"])}
    with open(ROOT / "bench_out" / "dedup_legacy.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
