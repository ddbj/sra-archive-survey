#!/usr/bin/env bash
# livelist.gz から RUN 行の必要列だけを抜き出してローカルへ保存する。
#
# livelist.gz は 2.16 GB あり、プレーンgzipの単一メンバーなのでランダムアクセスできない。
# 集計のたびに流し直すのは無駄なので、1回だけ流して抽出物を残す。以後の集計はローカルで行う。
#
# RUN 行は accession 順に並んでいない（out_of_order を 552万件観測した）。
# したがって distinct を取るには全体をソートする必要があり、ストリーム中には数えられない。
#
# ALIAS にタブを含む行があるため SRA_FILE / FASTQ_FILE は行末2列（$(NF-1), $NF）で取る。
#
# 使い方: extract_livelist_runs.sh [--livelist FILE] <出力先>
set -uo pipefail

URL="https://ftp.ebi.ac.uk/pub/databases/ena/report/livelist.gz"
usage() {
  cat <<'EOF'
使い方: extract_livelist_runs.sh [オプション] <出力先>
  EBI の livelist.gz から RUN 行を抜き出し、重複を除いた一覧も作る。
  --livelist FILE  手元の livelist.gz を読む（既定は EBI から 2.16 GB を取得する）
  -h, --help       この説明を出す
EOF
}
OUT=""
LIVELIST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --livelist)
      [ $# -ge 2 ] || { echo "$1 に値が要る" >&2; exit 2; }
      LIVELIST=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "不明なオプション: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -z "$OUT" ] || { echo "出力先は1つだけ指定する: $1" >&2; exit 2; }
      OUT=$1; shift ;;
  esac
done
[ -n "$OUT" ] || { echo "出力先を指定する。" >&2; usage >&2; exit 2; }
[ -z "$LIVELIST" ] || [ -s "$LIVELIST" ] || { echo "入力が無い: $LIVELIST" >&2; exit 1; }
mkdir -p "$OUT"
DEST="$OUT/livelist_run.tsv.gz"
DEDUP="$OUT/livelist_run_dedup.tsv.gz"

source_stream() {
  if [ -n "$LIVELIST" ]; then cat "$LIVELIST"; else curl -sS --retry 2 "$URL"; fi
}

# 完成してから名前を付ける。途中で止まった一覧が完成品の名前で残ると、
# 再実行時に取得済みとみなされ、欠けたまま後段へ渡る。
echo "抽出中: ${LIVELIST:-$URL} → $DEST"
if ! source_stream \
| gzip -dc \
| awk -F'\t' '
    NR == 1 { for (i = 1; i <= NF; i++) col[$i] = i; print "accession\tstatus\tupdated\tsra_file\tfastq_file"; next }
    $col["TYPE"] == "RUN" {
      print $col["ACCESSION"] "\t" $col["STATUS"] "\t" $col["UPDATED"] "\t" $(NF-1) "\t" $NF
    }' \
| gzip -1 > "$DEST.tmp"; then
  rm -f "$DEST.tmp"
  echo "抽出に失敗した。入力が途中で切れている可能性がある" >&2
  exit 1
fi
mv "$DEST.tmp" "$DEST"

echo "--- 保存結果 ---"
ls -lh "$DEST"
echo "行数: $(gzip -dc "$DEST" | wc -l)"
echo "--- 先頭5行 ---"
gzip -dc "$DEST" | head -5

# RUN 行は同じ内容が重複して現れる。survey_ebi_sra_sizes.sh はこの重複除去済みの
# 一覧を入力に取る。並び順がロケールで変わると差分が取れないため C ロケールに固定する。
echo "重複除去中: $DEDUP"
if ! { gzip -dc "$DEST" | head -1; gzip -dc "$DEST" | tail -n +2 | LC_ALL=C sort -u; } \
| gzip -1 > "$DEDUP.tmp"; then
  rm -f "$DEDUP.tmp"
  echo "重複除去に失敗した" >&2
  exit 1
fi
mv "$DEDUP.tmp" "$DEDUP"
echo "重複除去後の行数: $(gzip -dc "$DEDUP" | wc -l)"
