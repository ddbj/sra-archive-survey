#!/usr/bin/env bash
# ユースケース1: NCBI の規模。公式Parquetカタログの snapshot を取り、前回との差分と規模レポートを出す。
set -euo pipefail
source "$(dirname "$0")/_common.sh"

usage() {
  cat <<'EOF'
使い方: ncbi_scale.sh [オプション] <出力先> <PREFIX>...
  NCBI SRA の公式Parquetカタログから、指定した PREFIX の public Run の snapshot を取り、
  前回の snapshot との差分と規模レポートを出す。
  PREFIX は SRR・ERR・DRR から1つ以上。三極合計なら SRR ERR DRR、SRRだけなら SRR。
  出力は <出力先>/<PREFIXを辞書順に-でつないだ名前>/ に日付付きで溜まる
  （例 <出力先>/DRR-ERR-SRR/2026-09-27-summary.txt）。対象ごとにディレクトリが分かれる。
  同じ日付で再実行すると、完了済みなら取り直さず、途中なら続きから取る。
  --previous FILE  比べる前回の snapshot（*-runs.jsonl.gz）。
                   既定は同じディレクトリで今回より前の、完了した最新の snapshot
  --label DATE     出力ファイル名の日付（YYYY-MM-DD。既定 実行日）
  --dry-run        実行するコマンドを表示するだけで、何も実行しない
  -h, --help       この説明を出す
EOF
}

OUT=""
PREFIXES=()
PREVIOUS=""
LABEL=$(date +%Y-%m-%d)
while [ $# -gt 0 ]; do
  case "$1" in
    --previous) need_value "$#" "$1"; PREVIOUS=$2; shift 2 ;;
    --label)    need_value "$#" "$1"; need_date "$1" "$2"; LABEL=$2; shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         unknown_option "$1" ;;
    *)
      if [ -z "$OUT" ]; then OUT=$1
      else
        case "$1" in SRR|ERR|DRR) PREFIXES+=("$1") ;;
          *) die "PREFIX は SRR・ERR・DRR から選ぶ: $1" ;;
        esac
      fi
      shift ;;
  esac
done
[ -n "$OUT" ] || { echo "出力先を指定する。" >&2; usage >&2; exit 2; }
[ "${#PREFIXES[@]}" -gt 0 ] || { echo "PREFIX を1つ以上指定する。" >&2; usage >&2; exit 2; }
[ -z "$PREVIOUS" ] || [ -e "$PREVIOUS" ] || die "前回の snapshot が無い: $PREVIOUS"

mapfile -t PREFIXES < <(printf '%s\n' "${PREFIXES[@]}" | LC_ALL=C sort -u)
DIR="$OUT/$(IFS=-; echo "${PREFIXES[*]}")"
NCBI="$ROOT/scale/ncbi"

# report.json は survey_catalog.py が走査を終えたときにだけ書くので、完了の印に使う。
find_previous() {
  local latest="" snapshot label
  for snapshot in "$DIR"/*-runs.jsonl.gz; do
    [ -e "$snapshot" ] || continue
    label=$(basename "$snapshot" -runs.jsonl.gz)
    [[ "$label" =~ $DATE_PATTERN ]] || continue
    [[ "$label" < "$LABEL" ]] || continue
    [ -e "$DIR/$label-report.json" ] || continue
    latest=$snapshot
  done
  echo "$latest"
}
[ -n "$PREVIOUS" ] || PREVIOUS=$(find_previous)

echo "対象     : ${PREFIXES[*]}"
echo "出力先   : $DIR"
if [ -n "$PREVIOUS" ]; then
  echo "前回     : $PREVIOUS"
else
  echo "前回     : 前回の snapshot は無い。差分を出さずに今回の規模だけを出す"
fi
echo

[ "$DRY_RUN" = 1 ] || mkdir -p "$DIR"
if [ -e "$DIR/$LABEL-report.json" ]; then
  echo "取得済み : $DIR/$LABEL-report.json があるので取り直さない"
else
  run python3 "$NCBI/survey_catalog.py" --prefix "${PREFIXES[@]}" \
    --output-directory "$DIR" --label "$LABEL"
fi

DIFF_ARGS=()
if [ -n "$PREVIOUS" ]; then
  run python3 "$NCBI/snapshot_diff.py" --old "$PREVIOUS" --new "$DIR/$LABEL-runs.jsonl.gz" \
    --output-directory "$DIR" --label "$LABEL"
  DIFF_ARGS=(--diff-report "$DIR/$LABEL-diff-report.json")
fi

run_to "$DIR/$LABEL-summary.txt" python3 "$NCBI/scale_report.py" \
  --catalog-report "$DIR/$LABEL-report.json" ${DIFF_ARGS[@]+"${DIFF_ARGS[@]}"} \
  --output "$DIR/$LABEL-summary.json"
