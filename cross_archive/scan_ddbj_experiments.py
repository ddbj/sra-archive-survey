"""DDBJ の FTP を走査し、sra ファイルを持つ DRX の一覧を作る。

DDBJ の配置は `ByExp/sra/DRX/DRX976/DRX976617/DRR999990/DRR999990.sra` という
DRX 起点の構造で、末端まで降りないと DRR 名が分からない。しかし **1 DRX あたりの
DRR は平均1.02件**しかないため、末端までLISTすると約86万リクエストになる。
ここでは上位ディレクトリまでに留め、936リクエストで DRX の一覧を得る。
DRR への変換は fetch_ddbj_runs.py が Search API で行う。

**低負荷で走らせる。** 並列化せず、1リクエストごとに間隔を空ける。既定の1秒で
約16分かかる。

動作確認には `--limit` で接頭辞ディレクトリの数を絞る。

途中で落ちても `--output` に書けた分は残り、再実行時は取得済みの接頭辞を飛ばす。

使い方:
  python3 scan_ddbj_experiments.py --output ddbj_drx.txt --dry-run
  python3 scan_ddbj_experiments.py --output ddbj_drx.txt --limit 3
  python3 scan_ddbj_experiments.py --output ddbj_drx.txt
"""

import argparse
import os
import sys
import time
import urllib.error
import urllib.request

from accession_index import parse_listing
from ddbj_client import DEFAULT_DELAY, FTP_BASE, listing_url, plan, validate_delay
USER_AGENT = "sra-archive-survey/1.0 (research; contact via repository)"


def fetch(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def read_prefix_dirs():
    """`DRX/` 直下をLISTして、存在する上位ディレクトリ名を得る。"""
    return parse_listing(fetch(f"{FTP_BASE}/DRX/"), "DRX")


def load_done(path):
    """再実行時に飛ばす接頭辞。出力済みのDRXから逆算する。"""
    if not os.path.exists(path):
        return set()
    done = set()
    with open(path) as handle:
        for line in handle:
            accession = line.strip()
            if accession.startswith("DRX"):
                done.add("DRX" + accession[3:].zfill(6)[:3])
    return done


def announce(arguments, prefixes, skipped):
    print("=== このscriptが行うこと ===")
    print(f"  読む      : {FTP_BASE}/DRX/ 配下のディレクトリ一覧")
    print(f"  通信      : DDBJへ {len(prefixes)} リクエスト（逐次・{arguments.delay}秒間隔）")
    print(f"  書く      : {arguments.output}")
    print( "  外部process: 起動しない")
    if skipped:
        print(f"  再開      : 取得済み {skipped} 接頭辞を飛ばす")
    print(flush=True)


def run(arguments):
    prefixes = read_prefix_dirs()
    done = load_done(arguments.output)
    remaining = plan(prefixes, done, arguments.limit)
    announce(arguments, remaining, len(done))
    if arguments.dry_run:
        return 0

    started = time.monotonic()
    found = failed = 0
    with open(arguments.output, "a") as out, open(arguments.output + ".failed", "a") as err:
        for index, prefix in enumerate(remaining, 1):
            try:
                entries = parse_listing(fetch(listing_url(prefix)), "DRX")
            except (urllib.error.URLError, OSError) as error:
                err.write(f"{prefix}\t{error}\n")
                err.flush()
                failed += 1
            else:
                for accession in entries:
                    out.write(accession + "\n")
                found += len(entries)
                out.flush()
            if index % 100 == 0:
                print(f"  {index}/{len(remaining)} 接頭辞  {found:,} DRX  "
                      f"{time.monotonic() - started:.0f}s", flush=True)
            time.sleep(arguments.delay)

    print(f"\n=== 結果（所要 {time.monotonic() - started:.0f}s） ===")
    print(f"  走査した接頭辞 : {len(remaining):,}")
    print(f"  見つかった DRX : {found:,}")
    print(f"  失敗           : {failed:,}")
    print(f"  出力           : {arguments.output}")
    return 0 if failed == 0 else 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, help="DRX一覧の書き出し先")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"リクエスト間隔（秒）。既定 {DEFAULT_DELAY}")
    parser.add_argument("--limit", type=int,
                        help="走査する接頭辞ディレクトリの上限。動作確認用")
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
