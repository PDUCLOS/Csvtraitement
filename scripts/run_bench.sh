#!/usr/bin/env bash
# Campagne de mesures complète (≈ 10-20 min sur Ryzen 9 3900X). Résultats : bench_out/results.jsonl
set -e
cd "$(dirname "$0")/.."
PY=${PY:-.venv/Scripts/python}
for n in 10000 100000 500000 1000000; do
  [ -f samples/erp_$n.csv ] || $PY scripts/generate_sample.py --rows $n --out samples/erp_$n
done
$PY scripts/benchmark.py samples/erp_10000.csv --legacy-realign --label legacy-realign
$PY scripts/benchmark.py samples/erp_10000.csv  --xlsx --dedup --label final
$PY scripts/benchmark.py samples/erp_100000.csv --xlsx --dedup --label final
$PY scripts/benchmark.py samples/erp_500000.csv --xlsx --dedup --label final
$PY scripts/benchmark.py samples/erp_1000000.csv --label final
