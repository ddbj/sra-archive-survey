#!/usr/bin/env bash
# ユースケース5: NCBI と DDBJ の比較。DDBJ にある DRX を NCBI カタログと突き合わせ、DDBJ にしか無い DRX の DRR を引く。
set -euo pipefail
source "$(dirname "$0")/_common.sh"

usage() {
  cat <<'EOF'
使い方: ncbi_vs_ddbj.sh [オプション] <出力先>
  DDBJ にある DRX を NCBI カタログと突き合わせ、DDBJ にしか無い DRX の DRR を引く。
    1. DDBJ FTP から DRX 一覧を取る（936ディレクトリ、約16分）→ <出力先>/ddbj_experiments.tsv
    2. NCBI カタログと突き合わせる（約10〜30分）             → <出力先>/ddbj-only.tsv
                                                                <出力先>/ncbi-only.tsv
    3. DDBJ にしか無い DRX の DRR を DDBJ Search API で引く（100件で1リクエスト）
                                                             → <出力先>/ddbj-only-runs.tsv
                                                                <出力先>/ddbj-only-not-found.txt
    4. DDBJ の status ファイル（約260 MB）を1つ取る         → <出力先>/<YYYYMMDD>.dra.status.txt
    5. DRR 単位にまとめ、DDBJ 側の状態を添える              → <出力先>/ddbj-only-drr.tsv
                                                                <出力先>/ncbi-only-drr.tsv
       列は drx・drr・ddbj_status・source。
  中断しても同じコマンドで続きから再開する。
  --delay S   DDBJ へのリクエスト間隔の秒数（1・3・4 へ渡す。既定 1.0）
  --dry-run   実行するコマンドを表示するだけで、何も実行しない
  -h, --help  この説明を出す
EOF
}

OUT=""
DELAY_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --delay)    need_value "$#" "$1"; need_number "$1" "$2"; DELAY_ARGS=(--delay "$2"); shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         unknown_option "$1" ;;
    *)
      [ -z "$OUT" ] || die "出力先は1つだけ指定する: $1"
      OUT=$1; shift ;;
  esac
done
[ -n "$OUT" ] || { echo "出力先を指定する。" >&2; usage >&2; exit 2; }

CROSS="$ROOT/cross_archive"
EXPERIMENTS="$OUT/ddbj_experiments.tsv"
DDBJ_ONLY="$OUT/ddbj-only.tsv"
DRX_LIST="$OUT/ddbj-only-drx.txt"

[ "$DRY_RUN" = 1 ] || mkdir -p "$OUT"
# 一覧が欠けたまま突き合わせると、DDBJ にあるのに NCBI に無いものを取りこぼす。
run python3 "$CROSS/scan_ddbj_experiments.py" --output "$EXPERIMENTS" \
  ${DELAY_ARGS[@]+"${DELAY_ARGS[@]}"} \
  || die "DDBJ FTP の一覧に失敗したディレクトリがある。同じコマンドをもう一度実行して取り直す。"

run python3 "$CROSS/compare_archives.py" --archive ddbj --peer "$EXPERIMENTS" \
  --output "$DDBJ_ONLY" --ncbi-only-output "$OUT/ncbi-only.tsv"

write_drx_list() { tail -n +2 "$DDBJ_ONLY" | cut -f1 > "$DRX_LIST"; }
run write_drx_list

run python3 "$CROSS/fetch_ddbj_runs.py" --input "$DRX_LIST" --output "$OUT/ddbj-only-runs.tsv" \
  --missing "$OUT/ddbj-only-not-found.txt" ${DELAY_ARGS[@]+"${DELAY_ARGS[@]}"} \
  || die "DDBJ Search API への問い合わせに失敗したものがある。同じコマンドをもう一度実行して取り直す。"

run python3 "$CROSS/fetch_ddbj_status.py" --output-directory "$OUT" \
  ${DELAY_ARGS[@]+"${DELAY_ARGS[@]}"}

# fetch_ddbj_status.py は既存の status ファイルがあれば取り直さないので、ここで名前を決める。
STATUS=$(ls "$OUT"/*.dra.status.txt 2>/dev/null | tail -1 || true)
[ "$DRY_RUN" = 1 ] && STATUS=${STATUS:-"$OUT/<YYYYMMDD>.dra.status.txt"}
run python3 "$CROSS/label_ddbj_results.py" --status "$STATUS" \
  --ddbj-only "$DDBJ_ONLY" --runs "$OUT/ddbj-only-runs.tsv" \
  --not-found "$OUT/ddbj-only-not-found.txt" --ncbi-only "$OUT/ncbi-only.tsv" \
  --output-ddbj-only "$OUT/ddbj-only-drr.tsv" --output-ncbi-only "$OUT/ncbi-only-drr.tsv"
