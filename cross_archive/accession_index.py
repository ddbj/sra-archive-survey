"""三極のアーカイブを突き合わせるための共通処理。

NCBI・EBI・DDBJ はいずれも SRA の Run を持つが、accession の体系も配布の形も違う。
このモジュールは、その差を吸収するために各scriptが共有する小さな関数だけを置く。
"""

import re

ARCHIVE_PREFIXES = ("SRR", "ERR", "DRR", "SRX", "ERX", "DRX")
ACCESSION_PATTERN = re.compile(r"^(?P<prefix>[SED]R[RX])(?P<digits>[0-9]+)$")
# Apache の autoindex が出すソートリンク（?C=N;O=D など）と親ディレクトリを除くため、
# accession の形に一致する href だけを拾う。
HREF_PATTERN = re.compile(r'href="([A-Z]{3}[0-9]+)/?"')
NORMALIZED_FILETYPE = "sra"


def normalize_accession(value):
    """前後の空白を落として accession として検証する。

    zero padding は有意なので触らない。`DRR100` と `DRR000100` は別の Run である。
    """
    if not isinstance(value, str):
        raise ValueError(f"accessionとして不正: {value!r}")
    accession = value.strip()
    if not ACCESSION_PATTERN.match(accession):
        raise ValueError(f"accessionとして不正: {value!r}")
    return accession


def numeric_part(accession):
    """accession の数値部を返す。

    **桁を跨ぐ比較を文字列で行ってはならない。** DRR は7桁帯へ入っており、
    `DRR1090836` は文字列順では `DRR999999` より小さい。max を取ると最新を落とす。
    """
    match = ACCESSION_PATTERN.match(normalize_accession(accession))
    return int(match.group("digits"))


def drx_prefix_dir(accession):
    """DDBJ の FTP 上で DRX が属する上位ディレクトリ名を返す。

    配置は `ByExp/sra/DRX/DRX976/DRX976617/DRR999990/DRR999990.sra` の形で、
    上位3桁でグループ化されている。
    """
    accession = normalize_accession(accession)
    if not accession.startswith("DRX"):
        raise ValueError(f"DRXではない: {accession}")
    return "DRX" + accession[3:].zfill(6)[:3]


def parse_listing(html, prefix):
    """Apache の autoindex から、指定した接頭辞の accession を拾う。"""
    return [a for a in HREF_PATTERN.findall(html or "") if a.startswith(prefix)]


def classify_missing(catalog_row, filetypes):
    """本家にあって NCBI に無い Run を、無い理由で分ける。

    `None` を返すのは「NCBI 側にも sra がある」= 欠けていない場合である。

    `filetypes` が `None` の区分を分けているのは、カタログに行はあるのに
    `datastore_filetype` も `mbytes` も NULL という Run が実在するためである。
    SQL で `list_contains(NULL, 'sra')` は `false` ではなく `NULL` を返すので、
    `NOT has_sra` だけで分類すると、この行が黙って漏れる。
    """
    if catalog_row is None:
        return "not_in_ncbi_catalog"
    if filetypes is None:
        return "in_catalog_no_filetype_info"
    if NORMALIZED_FILETYPE not in [str(f or "").strip() for f in filetypes]:
        return "in_catalog_no_sra"
    return None
