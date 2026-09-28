#!/usr/bin/env bash
# ユースケース3: EBI CRAM 規模。集計まで survey_cram_sizes.sh 1本で済むので、引数をそのまま渡す。
set -euo pipefail
source "$(dirname "$0")/_common.sh"

case "${1:-}" in
  -h|--help) echo "ユースケース3: EBI CRAM 規模。scale/ebi/survey_cram_sizes.sh をそのまま呼ぶ。"
             echo ;;
esac
exec bash "$ROOT/scale/ebi/survey_cram_sizes.sh" "$@"
