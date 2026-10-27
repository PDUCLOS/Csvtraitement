#!/usr/bin/env bash
# Campagne de mesures complète (~25 min sur Ryzen 9 3900X). Résultats : bench_out/*.jsonl
# Chaque mesure tourne dans un processus séparé (pic mémoire propre).
set -e
cd "$(dirname "$0")/.."
PY=${PY:-.venv/Scripts/python}
for n in 10000 100000 500000 1000000; do
  [ -f samples/erp_$n.csv ] || $PY scripts/generate_sample.py --rows $n --out samples/erp_$n
done
# 1. Réalignement d'origine (sans recollage) vs actuel
$PY scripts/benchmark.py samples/erp_10000.csv --legacy-realign --label legacy-realign
# 2. Pipeline complet par taille
$PY scripts/benchmark.py samples/erp_10000.csv   --xlsx --dedup --label final
$PY scripts/benchmark.py samples/erp_100000.csv  --xlsx --dedup --label final
$PY scripts/benchmark.py samples/erp_500000.csv  --xlsx --dedup --label final
$PY scripts/benchmark.py samples/erp_1000000.csv --xlsx --label final
# 3. Déduplication : version d'origine (git cca1ef8) vs actuelle, mêmes fichiers
$PY scripts/bench_dedup_legacy.py samples/erp_demo_1k.csv --keys all
$PY scripts/bench_dedup_legacy.py samples/erp_10000.csv --keys 1,2,3,4,5,6,7,8,9,10
$PY scripts/bench_dedup_legacy.py samples/erp_10000.csv --keys all --skip-legacy
