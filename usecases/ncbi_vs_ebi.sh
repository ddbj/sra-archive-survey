#!/usr/bin/env bash
# ユースケース4: NCBI と EBI の比較。EBI にある ERR の SRA 形式ファイルを NCBI カタログと突き合わせる。
set -euo pipefail
source "$(dirname "$0")/_common.sh"

usage() {
  cat <<'EOF'
使い方: ncbi_vs_ebi.sh [オプション] <出力先>
  EBI にある ERR の SRA 形式ファイルと、NCBI カタログの ERR を突き合わせる。
    1. livelist.gz（2.16 GB）から RUN 一覧を作る          → <出力先>/livelist/
    2. EBI FTP を1ディレクトリ1リクエストで数える（約4,200ディレクトリ、1時間以上）
                                                          → <出力先>/ebi_sra/
    3. NCBI カタログと突き合わせる（約20分）              → <出力先>/ebi-only.tsv
                                                             <出力先>/ncbi-only.tsv
  比べるのは1ファイルの SRA 形式だけで、ディレクトリで置かれた vdb 形式の ERR は対象外。
  中断しても同じコマンドで続きから再開する。1 は作成済みなら飛ばす。
  --livelist FILE  手元の livelist.gz を使う（1 へ渡す）
  --batch N        1回の curl で引くディレクトリ数（2 へ渡す。既定 10）
  --sleep S        バッチ間の待ち秒数（2 へ渡す。既定 3）
  --dry-run        実行するコマンドを表示するだけで、何も実行しない
  -h, --help       この説明を出す
EOF
}

OUT=""
EXTRACT_ARGS=()
SURVEY_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --livelist) need_value "$#" "$1"; EXTRACT_ARGS+=(--livelist "$2"); shift 2 ;;
    --batch|--sleep)
      need_value "$#" "$1"; need_int "$1" "$2"; SURVEY_ARGS+=("$1" "$2"); shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         unknown_option "$1" ;;
    *)
      [ -z "$OUT" ] || die "出力先は1つだけ指定する: $1"
      OUT=$1; shift ;;
  esac
done
[ -n "$OUT" ] || { echo "出力先を指定する。" >&2; usage >&2; exit 2; }

DEDUP="$OUT/livelist/livelist_run_dedup.tsv.gz"
SRA="$OUT/ebi_sra"

if [ -e "$DEDUP" ]; then
  echo "作成済み : $DEDUP を使う"
  [ "${#EXTRACT_ARGS[@]}" -eq 0 ] || echo "           抽出済みなので --livelist は使わない"
else
  run bash "$ROOT/scale/ebi/extract_livelist_runs.sh" ${EXTRACT_ARGS[@]+"${EXTRACT_ARGS[@]}"} \
    "$OUT/livelist"
fi

run bash "$ROOT/cross_archive/survey_ebi_sra_sizes.sh" --dedup "$DEDUP" \
  ${SURVEY_ARGS[@]+"${SURVEY_ARGS[@]}"} "$SRA"

# 未取得のディレクトリがあると、EBI にあるのに NCBI に無いものを取りこぼす。
if [ "$DRY_RUN" = 0 ]; then
  remaining=$(comm -23 <(LC_ALL=C sort -u "$SRA/dirs.txt") <(LC_ALL=C sort -u "$SRA/done.txt") \
              | grep -c . || true)
  if [ "$remaining" -gt 0 ]; then
    echo "未取得のディレクトリが $remaining 個ある。同じコマンドをもう一度実行して取り直す。" >&2
    exit 1
  fi
fi

# 比べるのは1ファイルの SRA 形式だけにし、ディレクトリで置かれた vdb 形式の ERR
# （vdb_dirs.txt）は対象外とする。黙って落とさないよう件数だけは出す。
if [ "$DRY_RUN" = 0 ] && [ -s "$SRA/vdb_dirs.txt" ]; then
  echo "対象外   : vdb 形式の ERR $(sort -u "$SRA/vdb_dirs.txt" | grep -c .) 件（1ファイルの SRA 形式だけを比べる）"
fi
run python3 "$ROOT/cross_archive/compare_archives.py" --archive ebi --peer "$SRA/files.tsv" \
  --output "$OUT/ebi-only.tsv" --ncbi-only-output "$OUT/ncbi-only.tsv"
