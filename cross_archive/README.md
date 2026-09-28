# cross_archive

このディレクトリには、NCBI のカタログと EBI・DDBJ の保有を突き合わせるスクリプトと、その結果がある。NCBI のカタログには三極すべての Run が入っているが、EBI や DDBJ にだけある Run も、NCBI にだけある Run もある。ここではその差を Run 単位で出す。

ふだんは usecases のラッパーから使う。

| ラッパー | 呼ぶスクリプト |
|---|---|
| [`ncbi_vs_ebi.sh`](../usecases/README.md#4-ncbi-と-ebi-の保有を比べる) | scale/ebi の [`extract_livelist_runs.sh`](../scale/ebi/README.md#extract_livelist_runssh) → [`survey_ebi_sra_sizes.sh`](#survey_ebi_sra_sizessh) → [`compare_archives.py`](#compare_archivespy) |
| [`ncbi_vs_ddbj.sh`](../usecases/README.md#5-ncbi-と-ddbj-の保有を比べる) | [`scan_ddbj_experiments.py`](#scan_ddbj_experimentspy) → [`compare_archives.py`](#compare_archivespy) → [`fetch_ddbj_runs.py`](#fetch_ddbj_runspy) → [`fetch_ddbj_status.py`](#fetch_ddbj_statuspy) → [`label_ddbj_results.py`](#label_ddbj_resultspy) |

`accession_index.py` と `ddbj_client.py` は、上のスクリプトが共有する部品である。単独では実行しない。

必要なツールと接続先は、ルートの `README.md` の [動作環境](../README.md#動作環境) にある。

---

## 目次

- [結果（results/）](#結果results)
- [compare_archives.py](#compare_archivespy)
- [survey_ebi_sra_sizes.sh](#survey_ebi_sra_sizessh)
- [scan_ddbj_experiments.py](#scan_ddbj_experimentspy)
- [fetch_ddbj_runs.py](#fetch_ddbj_runspy)
- [fetch_ddbj_status.py](#fetch_ddbj_statuspy)
- [label_ddbj_results.py](#label_ddbj_resultspy)
- [DDBJ へのリクエスト](#ddbj-へのリクエスト)

## 結果（results/）

[`results/`](results/) には、このディレクトリのスクリプトで突き合わせた結果を置いている。ファイル名の日付は、突き合わせた日である。

- [`2026-08-27-err-ebi-only.tsv`](results/2026-08-27-err-ebi-only.tsv): EBI にあって NCBI に無い ERR。35,978 行
- [`2026-09-04-drr-ddbj-only.tsv`](results/2026-09-04-drr-ddbj-only.tsv): DDBJ にあって NCBI に無い DRR。19,045 行
- [`2026-09-04-drr-ncbi-only.tsv`](results/2026-09-04-drr-ncbi-only.tsv): NCBI にあって DDBJ の FTP に見つからない DRR。378 行

列の意味は、usecases の [4. NCBI と EBI の保有を比べる](../usecases/README.md#4-ncbi-と-ebi-の保有を比べる)（ERR）と [5. NCBI と DDBJ の保有を比べる](../usecases/README.md#5-ncbi-と-ddbj-の保有を比べる)（DRR）にある。ラッパーの出力と同じ列である。

NCBI のカタログも EBI・DDBJ の保有も日々変わるので、同じ日付の結果でなければ比べられない。新しい結果が要るときは、ラッパーで取り直す。

## compare_archives.py

NCBI のカタログと、EBI または DDBJ の一覧を突き合わせる。比べる単位は相手で変わる。

- EBI: ERR 単位。[`survey_ebi_sra_sizes.sh`](#survey_ebi_sra_sizessh) が作る accession とサイズの一覧と比べる
- DDBJ: DRX 単位。[`scan_ddbj_experiments.py`](#scan_ddbj_experimentspy) が作る DRX の一覧と比べる。DRR は後で [`fetch_ddbj_runs.py`](#fetch_ddbj_runspy) が引く

NCBI のカタログを全件読むので、EBI で約 20 分、DDBJ で約 25 分かかる。

```text
$ python3 cross_archive/compare_archives.py --help
usage: compare_archives.py [-h] --archive {ddbj,ebi} --peer PEER [PEER ...]
                           [--ncbi-only-output NCBI_ONLY_OUTPUT] --output OUTPUT
                           [--parquet-glob PARQUET_GLOB] [--dry-run]

NCBI のカタログと本家（EBI / DDBJ）の保有状況を突き合わせる。

同じ Run でも、どのアーカイブが実体を持つかは揃っていない。NCBI の公式Parquetは
三極すべての Run を収めるが、本家にあって NCBI に無いもの、その逆がどちらにもある。
このscriptは両者の差を出し、本家にしか無い accession を理由つきで一覧にする。

比較の単位は対象で変わる。

  EBI  : ERR 単位。EBIのFTP実測（accession と bytes のTSV）と突き合わせる
  DDBJ : DRX 単位。DDBJのFTPから得た DRX 一覧と突き合わせ、DRR は別途 API で引く

NCBI カタログの全件を読むため、EBI で約20分、DDBJ で約25分かかる。
本家側の一覧が部分集合のときは「NCBI にあって本家に無い」が意味を持たない
（渡さなかった分が全部そこへ数えられる）。

**NCBI 側で `datastore_filetype` が NULL の行がある。** `list_contains(NULL,'sra')`
は `false` ではなく `NULL` を返すため、`NOT has_sra` だけで分類すると黙って漏れる。
理由の区分はそこを分けてある（accession_index.classify_missing を参照）。

使い方:
  python3 compare_archives.py --archive ebi --peer ebi_sizes.tsv \
      --output ebi-only.tsv --dry-run
  python3 compare_archives.py --archive ddbj --peer ddbj_drx.txt --output ddbj-only.tsv

options:
  -h, --help            show this help message and exit
  --archive {ddbj,ebi}  突き合わせる本家
  --peer PEER [PEER ...]
                        本家側の一覧。複数可。ebi は accession<TAB>bytes、ddbj は DRX 一覧
  --ncbi-only-output NCBI_ONLY_OUTPUT
                        NCBIにしか無いものの書き出し先。ERRでは約870万行になる
  --output OUTPUT       差分の書き出し先（TSV）
  --parquet-glob PARQUET_GLOB
  --dry-run             通信せず計画だけ表示する
```

```bash
cd <repository root>
source .venv/bin/activate
python3 cross_archive/compare_archives.py \
    --archive ebi \
    --peer out/ebi-vs/ebi_sra/files.tsv \
    --output out/ebi-vs/ebi-only.tsv \
    --ncbi-only-output out/ebi-vs/ncbi-only.tsv
```

`--output` には、相手にあって NCBI に無いものが理由付きで出る。

| `--archive` | 列 |
|---|---|
| `ebi` | `accession`、`ebi_bytes`、`reason`、`ncbi_consent`、`ncbi_releasedate` |
| `ddbj` | `experiment`、`reason`、`ncbi_consent`、`ncbi_releasedate` |

`reason` の値は usecases の [4. NCBI と EBI の保有を比べる](../usecases/README.md#4-ncbi-と-ebi-の保有を比べる) にある。NCBI のカタログで `datastore_filetype` が空の行は、`in_catalog_no_filetype_info` として分けて出す。

`--ncbi-only-output` には、NCBI にあって相手に無いものが出る。`ebi` では `accession` の 1 列、`ddbj` では `drx` と `drr` の 2 列である。1 つの DRX に複数の DRR がつくことがあるので、DRR をすべて残す。

**重要:** `--ncbi-only-output` が意味を持つのは、`--peer` に相手の全件を渡したときだけである。一部しか渡さないと、渡さなかった分がすべて「NCBI にしか無い」に数えられる。

`--peer` は複数渡せる。同じ accession が違うサイズで現れると止まる。

## survey_ebi_sra_sizes.sh

EBI の FTP の `/vol1/err/` にある SRA 形式ファイル（拡張子の無い `ERR<数字>`）の件数とサイズを取る。HTTPS のディレクトリ一覧はサイズを丸めて表示するので、正確なバイト数を返す FTP の LIST を使う。

```text
$ bash cross_archive/survey_ebi_sra_sizes.sh --help
使い方: survey_ebi_sra_sizes.sh --dedup FILE [オプション] <出力先>
  EBI FTP の /vol1/err/ にある SRA 形式ファイルの件数と総サイズを取る。
  中断しても同じコマンドで続きから再開し、失敗したディレクトリも取り直す。
  --dedup FILE  extract_livelist_runs.sh が作る livelist_run_dedup.tsv.gz（必須）
  --batch N     1回の curl で引くディレクトリ数（既定 10）
  --sleep S     バッチ間の待ち秒数（既定 3）
  --limit N     動作確認用。対象ディレクトリを先頭 N 個に絞る
  -h, --help    この説明を出す
```

```bash
cd <repository root>
bash cross_archive/survey_ebi_sra_sizes.sh \
    --dedup out/ebi-vs/livelist/livelist_run_dedup.tsv.gz \
    out/ebi-vs/ebi_sra
```

対象のディレクトリは、livelist.gz で SRA 形式ファイルを持つ（`sra_file` が `Y`）Run から決める。約 4,200 ディレクトリになる。1 ファイルずつではなく 1 ディレクトリずつ LIST するので、リクエストはディレクトリの数で済む。

- `--batch` 個のディレクトリを 1 回の curl でまとめて引き、FTP の接続を使い回す。
- バッチの間に `--sleep` 秒空ける。既定では 1 秒あたり約 0.3 リクエストになる。
- 動作を確かめるときは、`--limit` で対象のディレクトリを絞る。

出力先に次のファイルができる。

- `files.tsv`: 1 ファイルの SRA 形式ファイルの accession とサイズ（byte）
- `vdb_dirs.txt`: ディレクトリで置かれた vdb 形式の ERR。サイズは取らない
- `dirs.txt`: 対象のディレクトリ
- `done.txt`: 取れたディレクトリ
- `failed.txt`: 取れなかったディレクトリと、その理由

途中で止まっても、同じコマンドで続きから取る。取れなかったディレクトリも取り直す。成功を返しながら本文が空になる転送があるので、SRA 形式ファイルが 1 件も取れなかったディレクトリは失敗として扱う。

## scan_ddbj_experiments.py

DDBJ の FTP を走査し、SRA 形式ファイルを持つ DRX の一覧を作る。DDBJ の FTP は DRX 起点で置かれていて、DRR 名を知るには末端まで降りる必要がある。1 DRX あたりの DRR は平均 1.02 件なので、末端まで降りると約 86 万リクエストになる。ここでは DRX の上位ディレクトリ（約 940 個）だけを LIST する。

```text
$ python3 cross_archive/scan_ddbj_experiments.py --help
usage: scan_ddbj_experiments.py [-h] --output OUTPUT [--delay DELAY] [--limit LIMIT] [--dry-run]

DDBJ の FTP を走査し、sra ファイルを持つ DRX の一覧を作る。

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

options:
  -h, --help       show this help message and exit
  --output OUTPUT  DRX一覧の書き出し先
  --delay DELAY    リクエスト間隔（秒）。既定 1.0
  --limit LIMIT    走査する接頭辞ディレクトリの上限。動作確認用
  --dry-run        通信せず計画だけ表示する
```

```bash
cd <repository root>
python3 cross_archive/scan_ddbj_experiments.py --output out/ddbj-vs/ddbj_experiments.tsv
```

既定の 1 秒間隔で約 16 分かかる。途中で止まっても、`--output` に書けた分は残り、取得済みの上位ディレクトリを飛ばして続きから取る。失敗したディレクトリは `<output>.failed` に書き、終了コード 2 で終わる。

## fetch_ddbj_runs.py

DDBJ Search API で、DRX に対応する DRR を引く。1 リクエストで 100 件の DRX を引けるので、19,000 件の DRX なら 190 リクエストで済む。

```text
$ python3 cross_archive/fetch_ddbj_runs.py --help
usage: fetch_ddbj_runs.py [-h] --input INPUT --output OUTPUT [--missing MISSING] [--delay DELAY]
                          [--limit LIMIT] [--dry-run]

DDBJ Search API で DRX に対応する DRR を引く。

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
  python3 fetch_ddbj_runs.py --input target_drx.txt --output drx_to_drr.tsv \
      --missing not_found.txt

options:
  -h, --help         show this help message and exit
  --input INPUT      DRX一覧（1行1accession）
  --output OUTPUT    DRX→DRR の書き出し先（TSV）
  --missing MISSING  APIが返さなかったDRXの書き出し先
  --delay DELAY      リクエスト間隔（秒）。既定 1.0
  --limit LIMIT      引く DRX の上限。動作確認用
  --dry-run          通信せず計画だけ表示する
```

```bash
cd <repository root>
python3 cross_archive/fetch_ddbj_runs.py \
    --input out/ddbj-vs/ddbj-only-drx.txt \
    --output out/ddbj-vs/ddbj-only-runs.tsv \
    --missing out/ddbj-vs/ddbj-only-not-found.txt
```

`--output` の列は `drx`、`drr`、`drr_count` である。DRR の無い DRX も、`drr` を空にして残す。

suppressed と withdrawn の DRX は、Search API に出てこない。FTP には実体が残っていても DRR を引けないので、`--missing` に書き出す。

途中で止まっても、`--output` にある DRX を飛ばして続きから引く。失敗したリクエストがあると、終了コード 2 で終わる。

## fetch_ddbj_status.py

DDBJ の status ファイルを 1 つ取る。status ファイルは DRA の全 accession の状態（public、suppressed、withdrawn など）を持ち、約 260 MB ある。日付ごとに `<YYYYMMDD>.dra.status.txt` として置かれるので、実行日から 1 日ずつさかのぼり、最初に見つかったものを取る。

```text
$ python3 cross_archive/fetch_ddbj_status.py --help
usage: fetch_ddbj_status.py [-h] --output-directory OUTPUT_DIRECTORY [--days DAYS] [--delay DELAY]
                            [--dry-run]

DDBJ の status ファイル（DRA の全 accession の状態、約260 MB）を1つ取る。

status ファイルは日付ごとに `<YYYYMMDD>.dra.status.txt` として置かれる。一覧ページを
解析せず、実行日から1日ずつさかのぼって最初に見つかったものを取る。当日分がまだ
出ていなくても、数リクエストで前日分に当たる。

出力先に `*.dra.status.txt` が既にあれば取り直さない。再実行で DDBJ へ同じ
260 MB を何度も要求しないためである。

使い方:
  python3 fetch_ddbj_status.py --output-directory out --dry-run
  python3 fetch_ddbj_status.py --output-directory out

options:
  -h, --help            show this help message and exit
  --output-directory OUTPUT_DIRECTORY
  --days DAYS           さかのぼる日数（既定 7）
  --delay DELAY         見つからなかったときの次のリクエストまでの秒数（既定 1.0）
  --dry-run             通信せず計画だけ表示する
```

```bash
cd <repository root>
python3 cross_archive/fetch_ddbj_status.py --output-directory out/ddbj-vs
```

出力先に `*.dra.status.txt` が既にあれば取り直さない。新しい status ファイルが要るときは、既存のファイルを消してから実行する。

## label_ddbj_results.py

compare_archives.py、fetch_ddbj_runs.py、fetch_ddbj_status.py の結果をまとめ、DRR 単位の一覧にする。通信はしない。

```text
$ python3 cross_archive/label_ddbj_results.py --help
usage: label_ddbj_results.py [-h] --status STATUS --ddbj-only DDBJ_ONLY --runs RUNS
                             --not-found NOT_FOUND --ncbi-only NCBI_ONLY
                             --output-ddbj-only OUTPUT_DDBJ_ONLY
                             --output-ncbi-only OUTPUT_NCBI_ONLY

NCBI と DDBJ の突き合わせ結果を、DDBJ 側の状態と DRR 付きの1枚にまとめる。

compare_archives.py は DRX 単位で比べ、fetch_ddbj_runs.py が DDBJ にしか無い DRX の
DRR を引く。ここではそれらを DRR 単位の行に広げ、status ファイルの状態を添える。

  ddbj_status  status ファイルの EXPERIMENT 行の Status（public / suppressed / withdrawn）。
               status ファイルに無ければ not_in_status
  source       DRR をどこから得たか
                 search_api        DDBJ Search API で引けた
                 status_file_only  API が返さなかった（suppressed / withdrawn は 404 になる）。
                                   DRR は分からないので空欄
                 ncbi_parquet      NCBI カタログにある DRX（NCBI 側の対応をそのまま使う）

使い方:
  python3 label_ddbj_results.py --status 20260927.dra.status.txt \
      --ddbj-only ddbj-only.tsv --runs ddbj-only-runs.tsv --not-found ddbj-only-not-found.txt \
      --ncbi-only ncbi-only.tsv \
      --output-ddbj-only ddbj-only-drr.tsv --output-ncbi-only ncbi-only-drr.tsv

options:
  -h, --help            show this help message and exit
  --status STATUS       fetch_ddbj_status.py が取った status ファイル
  --ddbj-only DDBJ_ONLY
                        compare_archives.py --archive ddbj の --output
  --runs RUNS           fetch_ddbj_runs.py の --output
  --not-found NOT_FOUND
                        fetch_ddbj_runs.py の --missing
  --ncbi-only NCBI_ONLY
                        compare_archives.py --archive ddbj の --ncbi-only-output
  --output-ddbj-only OUTPUT_DDBJ_ONLY
  --output-ncbi-only OUTPUT_NCBI_ONLY
```

```bash
cd <repository root>
python3 cross_archive/label_ddbj_results.py \
    --status out/ddbj-vs/20260927.dra.status.txt \
    --ddbj-only out/ddbj-vs/ddbj-only.tsv \
    --runs out/ddbj-vs/ddbj-only-runs.tsv \
    --not-found out/ddbj-vs/ddbj-only-not-found.txt \
    --ncbi-only out/ddbj-vs/ncbi-only.tsv \
    --output-ddbj-only out/ddbj-vs/ddbj-only-drr.tsv \
    --output-ncbi-only out/ddbj-vs/ncbi-only-drr.tsv
```

出力の列は usecases の [5. NCBI と DDBJ の保有を比べる](../usecases/README.md#5-ncbi-と-ddbj-の保有を比べる) にある。

**重要:** `--ddbj-only` にある DRX のうち、`--runs` にも `--not-found` にも無いものが残っていると、結果を書かずに止まる。DRR を引き終えていないまま書くと、DDBJ にしか無い Run を黙って落とすためである。fetch_ddbj_runs.py を再実行してから、まとめ直す。

## DDBJ へのリクエスト

DDBJ へのリクエストは、並列にせず、既定で 1 秒ずつ間を空けて送る。DDBJ は rate limit を公開していないので、`--delay` を 1 秒より短くしないことを勧める。

| スクリプト | リクエスト数 |
|---|---|
| scan_ddbj_experiments.py | 上位ディレクトリの一覧 1 回と、上位ディレクトリごとに 1 回（約 940 回） |
| fetch_ddbj_runs.py | DRX 100 件ごとに 1 回 |
| fetch_ddbj_status.py | 1〜7 回（見つかるまで 1 日ずつさかのぼる） |

どれも再実行で取得済みの分を飛ばすので、止まった後に同じコマンドを流しても、同じリクエストを送り直さない。

## ライセンス

MIT License で公開する。全文は [LICENSE](../LICENSE) にある。

Copyright (c) 2026 fmaccha
