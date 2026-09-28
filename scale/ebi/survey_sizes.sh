#!/usr/bin/env bash
# ERR のファイル系統別に「件数」と「総サイズ」を出す。
#
# 経路は search + limit=0 の年次スライス。/files の count=true は集計後に応答するため
# 60秒のゲートウェイ制限に当たるが、search はストリーミングなので当たらない。
#
# 検証: スライスごとに count と行数を突き合わせる。HTTP 200 でも本文が切れることがあるため、
# 一致しなければそのスライスを失敗として記録し、集計から除く（黙って欠測させない）。
#
# サイズ列は ';' 区切りで複数ファイルが入る。単純な列合計ではファイル数を数え落とす。
#
# 使い方: survey_sizes.sh [オプション] <出力先>（オプションは --help で出る）
set -uo pipefail

API="https://www.ebi.ac.uk/ena/portal/api"
usage() {
  cat <<'EOF'
使い方: survey_sizes.sh [オプション] <出力先>
  ERR のファイル系統別（fastq・submitted・sra・bam）の件数と総サイズを年単位で取る。
  --from-year N  取得する最初の年（既定 2008）
  --to-year N    取得する最後の年（既定 実行した年）
  --sleep S      年スライス間の待ち秒数（既定 5。失敗したスライスの後はその2倍）
  -h, --help     この説明を出す
EOF
}
OUT=""
# 年スライス間の待ち（秒）。失敗したスライスの後はその2倍待つ。
SLEEP=5
FROM_YEAR=2008
TO_YEAR=$(date -u +%Y)
while [ $# -gt 0 ]; do
  case "$1" in
    --from-year|--to-year|--sleep)
      [ $# -ge 2 ] || { echo "$1 に値が要る" >&2; exit 2; }
      [[ "$2" =~ ^[0-9]+$ ]] || { echo "$1 は0以上の整数で指定する: $2" >&2; exit 2; }
      case "$1" in
        --from-year) FROM_YEAR=$2 ;;
        --to-year)   TO_YEAR=$2 ;;
        --sleep)     SLEEP=$2 ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "不明なオプション: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -z "$OUT" ] || { echo "出力先は1つだけ指定する: $1" >&2; exit 2; }
      OUT=$1; shift ;;
  esac
done
[ -n "$OUT" ] || { echo "出力先を指定する。" >&2; usage >&2; exit 2; }
[ "$FROM_YEAR" -le "$TO_YEAR" ] || { echo "--from-year が --to-year より後: $FROM_YEAR > $TO_YEAR" >&2; exit 2; }
RAW="$OUT/raw"
mkdir -p "$RAW"

FIELDS='run_accession,fastq_bytes,submitted_bytes,sra_bytes,bam_bytes'

# count の応答から件数を読む。エラー時は {"message":"..."} が返るので、
# 数字を素朴に grep するとフィールド名の中の数字（bam_md5 の 5 など）を件数と
# 誤読する。count キーだけを見る。
count_runs() {
  curl -sS -G --data-urlencode "query=$1" --data 'result=read_run&format=json' "$API/count" \
  | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(-1); raise SystemExit
print(int(d["count"]) if "count" in d else -1)'
}

STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PER="$OUT/per_year.tsv"
# 年の一部だけを再実行することがあるため、上書きせず追記する。
# 同じ年が複数回現れうるので、集計では最後の行を採る。
# raw さえ残っていれば aggregate_sizes.py --per-year で作り直せる。
[ -s "$PER" ] || printf 'year\tcount\trows\tstatus\tfastq_files\tfastq_bytes\tsubmitted_files\tsubmitted_bytes\tsra_files\tsra_bytes\tbam_files\tbam_bytes\n' > "$PER"

for y in $(seq "$FROM_YEAR" "$TO_YEAR"); do
  q="first_public>=$y-01-01 AND first_public<=$((y+1))-01-01 AND run_accession=\"ERR*\""

  n=$(count_runs "$q")
  if [ "$n" = 0 ]; then
    printf '%d\t0\t0\tEMPTY\t0\t0\t0\t0\t0\t0\t0\t0\n' "$y" | tee -a "$PER"
    continue
  fi

  f="$RAW/err_$y.tsv.gz"
  code=$(curl -sS -G --data-urlencode "query=$q" \
        --data "result=read_run&fields=$FIELDS&limit=0&format=tsv" "$API/search" \
        -o "$RAW/.tmp.tsv" -w '%{http_code}')
  rows=$(( $(wc -l < "$RAW/.tmp.tsv") - 1 ))

  # 進行中の期間では、count と search の間にもデータが増える。厳密一致を求めると
  # 当年のスライスは必ず失敗する。取得の前後で count を挟み、行数がその区間に
  # 収まっていれば「動いている最中の正常な取得」として受け入れる。
  # 本文が途中で切れた場合は区間の下限を割るので、取りこぼしは検出できる。
  n_after=$(count_runs "$q")
  status=MISMATCH
  # count の失敗（-1）を区間の端に使うと、途中で切れた本文も区間に収まってしまう。
  if [ "$code" = 200 ] && [ "$n" -ge 0 ] && [ "$n_after" -ge 0 ]; then
    if [ "$rows" = "$n" ]; then
      status=OK
    elif [ "$rows" -ge "$n" ] && [ "$rows" -le "$n_after" ]; then
      status=OK_MOVING
    elif [ "$rows" -ge "$n_after" ] && [ "$rows" -le "$n" ]; then
      status=OK_MOVING
    fi
  fi

  if [ "$status" = MISMATCH ]; then
    printf '%d\t%s\t%d\tMISMATCH(http=%s,after=%s)\t\t\t\t\t\t\t\t\n' \
      "$y" "$n" "$rows" "$code" "$n_after" | tee -a "$PER"
    # 同じ年の前回の raw が残ると、per_year.tsv では失敗なのに合計にだけ入る。
    rm -f "$RAW/.tmp.tsv" "$f"
    sleep $((SLEEP * 2))
    continue
  fi

  gzip -1 -c "$RAW/.tmp.tsv" > "$f"; rm -f "$RAW/.tmp.tsv"

  # ';' の展開と多倍長加算は aggregate_sizes.py に一本化してある。awk は倍精度で足すため、
  # 2^53 を超える年の合計が1バイト単位で狂う。
  # --per-year の出力列は year rows fastq_files fastq_bytes ... bam_files bam_bytes
  agg=$(python3 "$(dirname "$0")/aggregate_sizes.py" --per-year "$f" \
        | awk -F'\t' 'NR==2 { print $3"\t"$4"\t"$5"\t"$6"\t"$7"\t"$8"\t"$9"\t"$10 }')
  printf '%d\t%s\t%d\t%s\t%s\n' "$y" "$n" "$rows" "$status" "$agg" | tee -a "$PER"
  sleep "$SLEEP"
done

echo "--- 合計（rawの全ファイルから多倍長で加算。取得時刻(UTC): $STAMP） ---"
if compgen -G "$RAW/err_*.tsv.gz" > /dev/null; then
  { echo "取得時刻(UTC): $STAMP"; python3 "$(dirname "$0")/aggregate_sizes.py" "$RAW"/err_*.tsv.gz; } \
  | tee "$OUT/total.txt"
else
  echo "取得できた年が無いので合計を出さない"
fi

echo "--- 失敗したスライス ---"
FAILED_YEARS=$(awk -F'\t' 'NR>1 { last[$1]=$4 } END { for (y in last)
  if (last[y]!="OK" && last[y]!="OK_MOVING" && last[y]!="EMPTY") print y }' "$PER" | sort -n)
awk -F'\t' 'NR>1 { last[$1]=$0 } END { for (y in last) { split(last[y], c, "\t")
  if (c[4]!="OK" && c[4]!="OK_MOVING" && c[4]!="EMPTY") print last[y] } }' "$PER" || true

# 合計は取れた年だけで出るので、欠けた年があることを終了コードでも知らせる。
if [ -n "$FAILED_YEARS" ]; then
  {
    echo "取得に失敗した年がある。その年だけ取り直すと合計も作り直される。例:"
    for year in $FAILED_YEARS; do
      echo "  bash $0 --from-year $year --to-year $year $OUT"
    done
  } >&2
  exit 1
fi
