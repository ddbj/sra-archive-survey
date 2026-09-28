# usecases/ のラッパーが共有する関数。source して使い、単独では実行しない。

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
DATE_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'

die() { echo "$*" >&2; exit 2; }

# 使い方: need_value "$#" "$1"
need_value() { [ "$1" -ge 2 ] || die "$2 に値が要る"; }

need_int() { [[ "$2" =~ ^[0-9]+$ ]] || die "$1 は0以上の整数で指定する: $2"; }

need_number() { [[ "$2" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "$1 は0以上の数で指定する: $2"; }

need_date() { [[ "$2" =~ $DATE_PATTERN ]] || die "$1 は YYYY-MM-DD で指定する: $2"; }

unknown_option() { echo "不明なオプション: $1" >&2; usage >&2; exit 2; }

show() { printf '+'; printf ' %q' "$@"; printf '\n'; }

# 実行するコマンドを表示してから実行する。--dry-run では表示だけにする。
run() {
  show "$@"
  [ "$DRY_RUN" = 1 ] && return 0
  "$@"
}

# run と同じだが、標準出力をファイルにも残す。
run_to() {
  local file=$1; shift
  show "$@"
  echo "  → $file"
  [ "$DRY_RUN" = 1 ] && return 0
  "$@" | tee "$file"
}

# survey_sizes.sh の --from-year の既定と揃える。
EBI_FIRST_YEAR=2008

# per_year.tsv で、first..last のうち取れていない年と、最後の行が成功でない年を出す。
# survey_*_sizes.sh は同じ年を取り直すと追記するので、年ごとに最後の行を採る。
# 使い方: incomplete_years <per_year.tsv> <first> <last>
incomplete_years() {
  if [ ! -e "$1" ]; then seq "$2" "$3"; return; fi
  awk -F'\t' -v first="$2" -v last_year="$3" '
    function bad(status) { return status != "OK" && status != "OK_MOVING" && status != "EMPTY" }
    NR > 1 { last[$1] = $4 }
    END {
      for (y = first; y <= last_year; y++) if (!(y in last)) print y
      for (y in last) if (bad(last[y])) print y
    }' "$1" | sort -un
}
