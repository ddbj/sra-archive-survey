"""DDBJ の FTP と Search API を、低負荷で叩くための部品。

**DDBJ へ投げるリクエストは最小限にする。** ここでは並列化せず、呼び出し側が
間隔を空けて逐次に使う前提の関数だけを置く。

FTP と Search API で取れるものが違う。

  FTP のディレクトリLIST : sra を持つ DRX の一覧。suppressed でも実体が残っていれば出る
  Search API            : DRX に対応する DRR。ただし suppressed / withdrawn は 404

使い方は各scriptの docstring を参照する。
"""

import urllib.parse

FTP_BASE = "https://ddbj.nig.ac.jp/public/ddbj_database/dra/sra/ByExp/sra"
SEARCH_API = "https://ddbj.nig.ac.jp/search/api/entries/sra-experiment/"
STATUS_BASE = "https://ddbj.nig.ac.jp/public/sra/status"
# Search API の perPage 上限。これを超えて指定しても100件しか返らない。
MAX_KEYWORDS = 100
STATUS_COLUMNS = 9
# DDBJ は rate limit を公開していないので、控えめに 1 秒とする。
DEFAULT_DELAY = 1.0


def plan(items, done, limit):
    """今回投げる対象を決める。処理済みを飛ばしてから limit で切る。

    limit を処理済みより先に掛けると、再開したときに処理済みの分だけで枠が埋まり、
    何も進まなくなる。
    """
    if limit is not None and limit <= 0:
        raise ValueError(f"limit が不正: {limit}")
    remaining = [item for item in items if item not in done]
    return remaining if limit is None else remaining[:limit]


def validate_delay(delay):
    if delay < 0:
        raise ValueError(f"間隔が負: {delay}")
    return delay


def chunked(items, size):
    """Search API の1リクエスト上限で分割する。"""
    if size <= 0:
        raise ValueError(f"サイズが不正: {size}")
    chunk = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def listing_url(prefix_dir):
    """FTP のディレクトリLISTのURL。"""
    return f"{FTP_BASE}/DRX/{prefix_dir}/"


def status_url(date):
    """その日の status ファイルのURL。`date` は YYYYMMDD。"""
    return f"{STATUS_BASE}/{date}.dra.status.txt"


def build_search_url(accessions):
    """DRX をまとめて引くURLを組み立てる。

    `includeProperties=false` を付けるのは、実験条件のメタデータが要らないため。
    付けないとレスポンスが数倍になり、DDBJ側の負荷も転送量も増える。
    """
    accessions = list(accessions)
    if len(accessions) > MAX_KEYWORDS:
        raise ValueError(f"1リクエストは{MAX_KEYWORDS}件まで: {len(accessions)}件")
    query = urllib.parse.urlencode({
        "keywords": ",".join(accessions),
        "perPage": MAX_KEYWORDS,
        "includeProperties": "false",
        "dbXrefsLimit": 50,
    })
    return f"{SEARCH_API}?{query}"


def extract_runs(body):
    """Search API のレスポンスから (DRX, DRR, DRR総数) を取り出す。

    DRR が無い DRX も空文字で残す。取りこぼしたのか、本当に Run が無いのかを
    呼び出し側が区別できるようにするためである。
    """
    rows = []
    for item in (body or {}).get("items", []):
        drx = item.get("identifier")
        runs = [x.get("identifier") for x in (item.get("dbXrefs") or [])
                if x.get("type") == "sra-run"]
        count = (item.get("dbXrefsCount") or {}).get("sra-run")
        if not runs:
            rows.append((drx, "", count))
        for run in runs:
            rows.append((drx, run, count))
    return rows


def parse_status_line(line):
    """status ファイルの1行から (accession, type, status, visibility) を取る。

    ヘッダ行と列数の足りない行は `None` を返す。
    """
    parts = (line or "").rstrip("\n").split("\t")
    if len(parts) < STATUS_COLUMNS or parts[0] == "Accession":
        return None
    return parts[0], parts[5], parts[2], parts[7]
