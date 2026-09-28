"""DDBJ の status ファイル（DRA の全 accession の状態、約260 MB）を1つ取る。

status ファイルは日付ごとに `<YYYYMMDD>.dra.status.txt` として置かれる。一覧ページを
解析せず、実行日から1日ずつさかのぼって最初に見つかったものを取る。当日分がまだ
出ていなくても、数リクエストで前日分に当たる。

出力先に `*.dra.status.txt` が既にあれば取り直さない。再実行で DDBJ へ同じ
260 MB を何度も要求しないためである。

使い方:
  python3 fetch_ddbj_status.py --output-directory out --dry-run
  python3 fetch_ddbj_status.py --output-directory out
"""

import argparse
import datetime
import glob
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

from ddbj_client import DEFAULT_DELAY, status_url, validate_delay

USER_AGENT = "sra-archive-survey/1.0 (research; contact via repository)"
DEFAULT_DAYS = 7


def candidate_dates(today, days):
    return [(today - datetime.timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(days)]


def existing_status(directory):
    found = sorted(glob.glob(os.path.join(directory, "*.dra.status.txt")))
    return found[-1] if found else None


def download(url, destination, timeout=900):
    """見つからなければ False を返す。途中で切れたファイルを完成品の名前で残さない。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, \
             open(destination + ".tmp", "wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise
    os.replace(destination + ".tmp", destination)
    return True


def run(arguments):
    validate_delay(arguments.delay)
    reused = existing_status(arguments.output_directory)
    dates = candidate_dates(datetime.date.today(), arguments.days)
    print("=== このscriptが行うこと ===")
    if reused:
        print(f"  再利用    : {reused}（取り直さない）")
        print(flush=True)
        return 0
    print(f"  通信      : DDBJ へ最大 {len(dates)} リクエスト（{dates[0]} から1日ずつさかのぼる。"
          f"{arguments.delay}秒間隔）")
    print(f"  書く      : {arguments.output_directory}/<YYYYMMDD>.dra.status.txt（約260 MB）")
    print(flush=True)
    if arguments.dry_run:
        return 0

    os.makedirs(arguments.output_directory, exist_ok=True)
    for date in dates:
        destination = os.path.join(arguments.output_directory, f"{date}.dra.status.txt")
        if download(status_url(date), destination):
            print(f"  取得      : {destination}")
            return 0
        print(f"  {date} は無い", flush=True)
        time.sleep(arguments.delay)
    print(f"直近 {arguments.days} 日分の status ファイルが見つからない", file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"さかのぼる日数（既定 {DEFAULT_DAYS}）")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"見つからなかったときの次のリクエストまでの秒数（既定 {DEFAULT_DELAY}）")
    parser.add_argument("--dry-run", action="store_true", help="通信せず計画だけ表示する")
    arguments = parser.parse_args(argv)
    if arguments.days < 1:
        parser.error("--days は1以上")
    return run(arguments)


if __name__ == "__main__":
    sys.exit(main())
