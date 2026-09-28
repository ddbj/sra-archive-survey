"""DDBJ Search API で DRX に対応する DRR を引く。

DRX 起点の FTP 構造から DRR を得るには末端までLISTが要るが、Search API の
`dbXrefs` に `type="sra-run"` として入っているので、そちらを使う。**これは DDBJ
自身が持つ対応関係であり、status ファイルの Submission 列（DRA）を介した推測とは
確度が違う。** 1 DRA に複数の DRX/DRR が入るケースが72%あるため、DRA 経由では
一意に決まらない。

`keywords` にカンマ区切りで **100件まで**指定でき、指定したものだけが返る。
19,000 DRX なら190リクエストで済む。

**suppressed / withdrawn の DRX は 404 になり、DRR を引けない。** FTP には実体が
残っているのに検索系からは消えている。取りこぼしを黙って落とさないよう、
返らなかった DRX は `--missing` へ書き出す。

動作確認には `--limit` で DRX の数を絞る。

使い方:
  python3 fetch_ddbj_runs.py --input target_drx.txt --output drx_to_drr.tsv --dry-run
  python3 fetch_ddbj_runs.py --input target_drx.txt --output drx_to_drr.tsv --limit 5
  python3 fetch_ddbj_runs.py --input target_drx.txt --output drx_to_drr.tsv \\
      --missing not_found.txt
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from accession_index import normalize_accession
from ddbj_client import (
    DEFAULT_DELAY,
    MAX_KEYWORDS,
    build_search_url,
    chunked,
    extract_runs,
    plan,
    validate_delay,
)
USER_AGENT = "sra-archive-survey/1.0 (research; contact via repository)"
HEADER = "drx\tdrr\tdrr_count"


def fetch_json(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_targets(path):
    with open(path) as handle:
        return [normalize_accession(line) for line in handle if line.strip()]


def load_done(path):
    if not os.path.exists(path):
        return set()
    done = set()
    with open(path) as handle:
        for line in handle:
            drx = line.split("\t", 1)[0]
            if drx.startswith("DRX"):
                done.add(drx)
    return done


def announce(arguments, targets, batches, skipped):
    print("=== このscriptが行うこと ===")
    print(f"  読む      : {arguments.input}（{len(targets):,} DRX）")
    print(f"  通信      : DDBJ Search API へ {batches} リクエスト"
          f"（逐次・{arguments.delay}秒間隔・{MAX_KEYWORDS}件/回）")
    print(f"  書く      : {arguments.output}")
    if arguments.missing:
        print(f"              {arguments.missing}（APIが返さなかったDRX）")
    print( "  外部process: 起動しない")
    if skipped:
        print(f"  再開      : 取得済み {skipped:,} DRX を飛ばす")
    print(flush=True)


def run(arguments):
    targets = load_targets(arguments.input)
    done = load_done(arguments.output)
    remaining = plan(targets, done, arguments.limit)
    batches = (len(remaining) + MAX_KEYWORDS - 1) // MAX_KEYWORDS
    announce(arguments, remaining, batches, len(done))
    if arguments.dry_run:
        return 0

    started = time.monotonic()
    rows = failed = 0
    missing = []
    write_header = not os.path.exists(arguments.output) or os.path.getsize(arguments.output) == 0
    with open(arguments.output, "a") as out:
        if write_header:
            out.write(HEADER + "\n")
        for index, chunk in enumerate(chunked(remaining, MAX_KEYWORDS), 1):
            try:
                body = fetch_json(build_search_url(chunk))
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
                print(f"  失敗: {chunk[0]}〜 ({error})", file=sys.stderr)
                failed += len(chunk)
                continue
            returned = set()
            for drx, drr, count in extract_runs(body):
                out.write(f"{drx}\t{drr}\t{count if count is not None else ''}\n")
                returned.add(drx)
                rows += 1
            missing.extend(a for a in chunk if a not in returned)
            out.flush()
            if index % 20 == 0:
                print(f"  {index}/{batches} batch  {rows:,} 行  "
                      f"{time.monotonic() - started:.0f}s", flush=True)
            time.sleep(arguments.delay)

    if arguments.missing:
        with open(arguments.missing, "w") as handle:
            for accession in missing:
                handle.write(accession + "\n")

    print(f"\n=== 結果（所要 {time.monotonic() - started:.0f}s） ===")
    print(f"  対象 DRX       : {len(remaining):,}")
    print(f"  書き出した行   : {rows:,}")
    print(f"  APIが返さない  : {len(missing):,}  ※suppressed / withdrawn は404になる")
    print(f"  リクエスト失敗 : {failed:,}")
    print(f"  出力           : {arguments.output}")
    return 0 if failed == 0 else 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="DRX一覧（1行1accession）")
    parser.add_argument("--output", required=True, help="DRX→DRR の書き出し先（TSV）")
    parser.add_argument("--missing", help="APIが返さなかったDRXの書き出し先")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"リクエスト間隔（秒）。既定 {DEFAULT_DELAY}")
    parser.add_argument("--limit", type=int, help="引く DRX の上限。動作確認用")
    parser.add_argument("--dry-run", action="store_true", help="通信せず計画だけ表示する")
    arguments = parser.parse_args(argv)
    validate_delay(arguments.delay)
    try:
        return run(arguments)
    except KeyboardInterrupt:
        print("\n中断した。同じコマンドで続きから再開できる。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
