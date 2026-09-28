#!/usr/bin/env bash
# ユースケース2: EBI の規模。ERR のファイル系統別の件数と総サイズを取り、前回との差分を出す。
set -euo pipefail
source "$(dirname "$0")/_common.sh"

usage() {
  cat <<'EOF'
使い方: ebi_scale.sh [オプション] <出力先>
  ENA Portal API から ERR のファイル系統別（fastq・submitted・sra・bam）の件数と
  総サイズを年単位で取り、前回の取得結果との差分を出す。
  出力は <出力先>/<日付>/ に溜まる。同じ日付で再実行すると、取得済みなら取り直さない。
  --previous DIR  比べる前回の取得結果（<出力先>/<日付>）。
                  既定は今回より前で、全年がそろった最新の取得結果
  --label DATE    取得結果のディレクトリ名（YYYY-MM-DD。既定 実行日）
  --sleep S       年スライス間の待ち秒数（survey_sizes.sh へ渡す。既定 5）
  --dry-run       実行するコマンドを表示するだけで、何も実行しない
  -h, --help      この説明を出す
EOF
}

OUT=""
PREVIOUS=""
LABEL=$(date +%Y-%m-%d)
SURVEY_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --previous) need_value "$#" "$1"; PREVIOUS=${2%/}; shift 2 ;;
    --label)    need_value "$#" "$1"; need_date "$1" "$2"; LABEL=$2; shift 2 ;;
    --sleep)    need_value "$#" "$1"; need_int "$1" "$2"; SURVEY_ARGS+=(--sleep "$2"); shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         unknown_option "$1" ;;
    *)
      [ -z "$OUT" ] || die "出力先は1つだけ指定する: $1"
      OUT=$1; shift ;;
  esac
done
[ -n "$OUT" ] || { echo "出力先を指定する。" >&2; usage >&2; exit 2; }
[ -z "$PREVIOUS" ] || [ -e "$PREVIOUS/total.txt" ] || die "前回の取得結果が無い: $PREVIOUS"

DIR="$OUT/$LABEL"
EBI="$ROOT/scale/ebi"

# total.txt は survey_sizes.sh が全年を回し終えたときにだけ書くので、完了の印に使う。
# ただし失敗した年があっても、--from-year で一部の年だけ取っても書かれるので、
# その日付の年までそろっていない取得結果は前回の候補から外す。
find_previous() {
  local latest="" candidate label
  for candidate in "$OUT"/*/; do
    candidate=${candidate%/}
    label=$(basename "$candidate")
    [[ "$label" =~ $DATE_PATTERN ]] || continue
    [[ "$label" < "$LABEL" ]] || continue
    [ -e "$candidate/total.txt" ] || continue
    [ -z "$(incomplete_years "$candidate/per_year.tsv" "$EBI_FIRST_YEAR" "${label:0:4}")" ] \
      || continue
    latest=$candidate
  done
  echo "$latest"
}
[ -n "$PREVIOUS" ] || PREVIOUS=$(find_previous)

echo "出力先   : $DIR"
echo "前回     : ${PREVIOUS:-前回の取得結果は無い。差分を出さずに今回の規模だけを出す}"
echo

if [ -e "$DIR/total.txt" ]; then
  echo "取得済み : $DIR/total.txt があるので取り直さない"
else
  # 失敗年があると survey_sizes.sh は 1 で終わる。ここで止めず、下の確認で案内する。
  SURVEY_STATUS=0
  run bash "$EBI/survey_sizes.sh" ${SURVEY_ARGS[@]+"${SURVEY_ARGS[@]}"} "$DIR" || SURVEY_STATUS=$?
fi

# 欠けた年は差分で全件「消滅」に見えるので、差分を出さずに止める。
if [ -e "$DIR/per_year.tsv" ]; then
  mapfile -t FAILED < <(incomplete_years "$DIR/per_year.tsv" "$EBI_FIRST_YEAR" "${LABEL:0:4}")
  if [ "${#FAILED[@]}" -gt 0 ]; then
    {
      echo "取得できていない年がある: ${FAILED[*]}"
      echo "その年だけ取り直してから、このコマンドをもう一度実行する。例:"
      for year in "${FAILED[@]}"; do
        echo "  bash $EBI/survey_sizes.sh --from-year $year --to-year $year $DIR"
      done
    } >&2
    exit 1
  fi
fi
[ "${SURVEY_STATUS:-0}" = 0 ] || exit "$SURVEY_STATUS"

[ -n "$PREVIOUS" ] || exit 0
run_to "$DIR/scale_diff_$(basename "$PREVIOUS")--$LABEL.txt" \
  python3 "$EBI/scale_diff.py" "$PREVIOUS"/raw/err_*.tsv.gz -- "$DIR"/raw/err_*.tsv.gz
