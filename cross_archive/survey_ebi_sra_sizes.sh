#!/usr/bin/env bash
# SRA形式（/vol1/err/ 配下の拡張子なしファイル）の件数と総サイズを集計する。
#
# なぜFTPのLISTを使うか:
#   HTTPSのディレクトリ一覧はサイズが "64M" のように丸められる。FTPのLISTは正確な
#   バイト数を返し、HEAD の Content-Length と一致することを確認済みである。
#   1ファイル1リクエスト（1,893,321回）ではなく1ディレクトリ1リクエスト（4,214回）で済む。
#
# 対象ディレクトリは livelist の SRA_FILE=Y から算出する（推定ではない）。
#
# 負荷への配慮:
#   - 1回のcurlで --batch 個のディレクトリを引き、FTP制御接続を再利用する
#   - バッチ間に --sleep 秒あける。既定で約0.3 req/秒であり、公式上限50 req/秒に遠い
#   - 中断しても done.txt から再開できる
#
# 使い方: survey_ebi_sra_sizes.sh --dedup <livelist_run_dedup.tsv.gz> [オプション] <出力先>
set -uo pipefail

FTP_BASE="ftp://ftp.sra.ebi.ac.uk/vol1/err"
usage() {
  cat <<'EOF'
使い方: survey_ebi_sra_sizes.sh --dedup FILE [オプション] <出力先>
  EBI FTP の /vol1/err/ にある SRA 形式ファイルの件数と総サイズを取る。
  中断しても同じコマンドで続きから再開し、失敗したディレクトリも取り直す。
  --dedup FILE  extract_livelist_runs.sh が作る livelist_run_dedup.tsv.gz（必須）
  --batch N     1回の curl で引くディレクトリ数（既定 10）
  --sleep S     バッチ間の待ち秒数（既定 3）
  --limit N     動作確認用。対象ディレクトリを先頭 N 個に絞る
  -h, --help    この説明を出す
EOF
}
OUT=""
DEDUP=""
BATCH=10
SLEEP=3
LIMIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dedup)
      [ $# -ge 2 ] || { echo "$1 に値が要る" >&2; exit 2; }
      DEDUP=$2; shift 2 ;;
    --batch|--sleep|--limit)
      [ $# -ge 2 ] || { echo "$1 に値が要る" >&2; exit 2; }
      [[ "$2" =~ ^[0-9]+$ ]] || { echo "$1 は0以上の整数で指定する: $2" >&2; exit 2; }
      case "$1" in
        --batch) BATCH=$2 ;;
        --sleep) SLEEP=$2 ;;
        --limit) LIMIT=$2 ;;
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
[ -n "$DEDUP" ] || { echo "--dedup に extract_livelist_runs.sh が作る livelist_run_dedup.tsv.gz を指定する" >&2; exit 2; }
[ "$BATCH" -ge 1 ] || { echo "--batch は1以上の整数で指定する: $BATCH" >&2; exit 2; }
[ -s "$DEDUP" ] || { echo "入力が無い: $DEDUP（extract_livelist_runs.sh で作る）" >&2; exit 1; }

mkdir -p "$OUT"
DIRS="$OUT/dirs.txt"
DONE="$OUT/done.txt"
RESULT="$OUT/files.tsv"
VDB="$OUT/vdb_dirs.txt"
FAILED="$OUT/failed.txt"
touch "$DONE" "$FAILED" "$VDB"
[ -s "$RESULT" ] || printf 'accession\tbytes\n' > "$RESULT"

# 1. 対象ディレクトリを算出する（初回のみ）
if [ ! -s "$DIRS" ]; then
  echo "対象ディレクトリを算出中..."
  gzip -dc "$DEDUP" | awk -F'\t' '
    NR == 1 { next }
    $4 == "Y" {
      n = substr($1, 4); L = length(n); pre = "ERR" substr(n, 1, 3)
      if (L <= 6)      d = pre
      else if (L == 7) d = pre "/00" substr(n, L, 1)
      else if (L == 8) d = pre "/0"  substr(n, L-1, 2)
      else             d = pre "/"   substr(n, L-2, 3)
      seen[d] = 1
    }
    END { for (d in seen) print d }' | sort > "$DIRS"
fi
total=$(wc -l < "$DIRS")
echo "対象ディレクトリ: $total"

# 2. 未処理のディレクトリを BATCH 個ずつ引く
remaining=$(comm -23 "$DIRS" <(sort -u "$DONE"))
[ -n "$LIMIT" ] && remaining=$(printf '%s\n' "$remaining" | head -n "$LIMIT")
n_remaining=$(printf '%s\n' "$remaining" | grep -c . || true)
echo "未処理: $n_remaining"
[ "$n_remaining" = 0 ] && { echo "完了済み"; }

# xargs で BATCH 個ずつ 1 行にまとめ、それを配列として受ける
printf '%s\n' "$remaining" | grep . | xargs -n "$BATCH" | while read -r -a dirs; do
  urls=(); for d in "${dirs[@]}"; do urls+=("$FTP_BASE/$d/"); done

  # -w で各転送の終わりに区切りを入れ、どのディレクトリの出力かを対応づける。
  # 書式を '@' で始めてはならない。curl が「ファイルから書式を読む」と解釈する。
  out=$(curl -sS -m 300 --retry 2 --retry-delay 5 \
          -w 'CURLEND\t%{url_effective}\t%{http_code}\n' "${urls[@]}" 2>/dev/null)

  # 区切り行の直前までが、その URL の LIST 出力である。
  # 拡張子なしの ERR<digits> がSRA形式だが、世代によって実体が違う:
  #   '-' で始まる行 = 単一ファイル。サイズがそのまま取れる
  #   'd' で始まる行 = 展開されたvdbオブジェクト（col/ idx/ md/）。サイズは再帰しないと出ない
  # ステータスだけで done と記録してはならない。長時間の走査では、成功コードを
  # 返しながら本文が空になる転送が起きる。対象ディレクトリには必ず1件以上あるはずなので、
  # 「1件も取れなかったディレクトリ」は失敗として扱い、再走査に回す。
  printf '%s\n' "$out" | awk -v result="$RESULT" -v vdb="$VDB" -v done_f="$DONE" -v failed="$FAILED" '
    /^CURLEND\t/ {
      split($0, m, "\t"); url = m[2]; code = m[3]
      sub(/^ftp:\/\/[^\/]+\/vol1\/err\//, "", url); sub(/\/$/, "", url)
      ok = (code == 226 || code == 250 || code == 0)
      if (ok && n > 0)      print url >> done_f
      else if (ok)          print url "\tEMPTY" >> failed
      else                  print url "\t" code >> failed
      n = 0
      next
    }
    /^-/ && $NF ~ /^ERR[0-9]+$/ { print $NF "\t" $5 >> result; n++; next }
    /^d/ && $NF ~ /^ERR[0-9]+$/ { print $NF >> vdb; n++ }
  '
  sleep "$SLEEP"
done

echo "--- 集計 ---"
python3 - "$RESULT" <<'PY'
import sys
seen = {}
with open(sys.argv[1]) as fh:
    fh.readline()
    for line in fh:
        acc, size = line.rstrip("\n").split("\t")
        seen[acc] = int(size)          # 重複行があっても最後の値で上書きする
total = sum(seen.values())
print(f"単一ファイルのSRA : {len(seen):,} 件")
print(f"総バイト数        : {total:,}  = {total/1e15:.3f} PB")
PY
echo "vdbディレクトリ形式（サイズ未測、要再帰）: $(sort -u "$VDB" | grep -c . || true) 件"
echo "未処理ディレクトリ: $(comm -23 "$DIRS" <(sort -u "$DONE") | grep -c . || true)"
echo "失敗ディレクトリ  : $(grep -c . "$FAILED" || true)"
