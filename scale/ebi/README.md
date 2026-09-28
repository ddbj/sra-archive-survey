# scale/ebi

このディレクトリには、ENA Portal API から EBI にある ERR の規模を取るスクリプトと、EBI の livelist.gz から RUN の一覧を作るスクリプトがある。

ふだんは usecases のラッパーから使う。

| ラッパー | 呼ぶスクリプト |
|---|---|
| [`ebi_scale.sh`](../../usecases/README.md#2-ebi-の規模を取る) | [`survey_sizes.sh`](#survey_sizessh)（集計に [`aggregate_sizes.py`](#aggregate_sizespy) を使う）→ [`scale_diff.py`](#scale_diffpy) |
| [`ebi_cram.sh`](../../usecases/README.md#3-ebi-の-cram-の規模を取る) | [`survey_cram_sizes.sh`](#survey_cram_sizessh)（集計に [`aggregate_cram_sizes.py`](#aggregate_cram_sizespy) を使う） |
| [`ncbi_vs_ebi.sh`](../../usecases/README.md#4-ncbi-と-ebi-の保有を比べる) | [`extract_livelist_runs.sh`](#extract_livelist_runssh) → cross_archive のスクリプト |

必要なツールは、ルートの `README.md` の [動作環境](../../README.md#動作環境) にある。

---

## 目次

- [ENA Portal API からの取り方](#ena-portal-api-からの取り方)
- [survey_sizes.sh](#survey_sizessh)
- [aggregate_sizes.py](#aggregate_sizespy)
- [scale_diff.py](#scale_diffpy)
- [survey_cram_sizes.sh](#survey_cram_sizessh)
- [aggregate_cram_sizes.py](#aggregate_cram_sizespy)
- [extract_livelist_runs.sh](#extract_livelist_runssh)

## ENA Portal API からの取り方

survey_sizes.sh と survey_cram_sizes.sh は、同じ方法で取る。

- 公開年（`first_public`）ごとに分けて取る。1 年分を `search` に `limit=0` を付けて全件取る。`search` は結果を流しながら返すので、ゲートウェイの 60 秒の制限に当たらない。
- 取れた行数を件数と突き合わせる。HTTP 200 でも本文が途中で切れることがあるので、取得の前後に `count` で件数を問い合わせ、行数がその間に収まっているかを確かめる。
    - 行数が取得前の件数と一致すれば `OK`
    - 取得中に件数が増えていて、行数がその間に収まれば `OK_MOVING`（公開が進んでいる年で起きる）
    - どちらでもなければ `MISMATCH` とし、その年を集計から外す
    - 件数が 0 の年は `EMPTY`
- 1 年あたり 3 リクエストを送る。件数が 0 の年は 1 リクエストで済む。年と年の間に `--sleep` 秒（既定 5 秒）空け、`MISMATCH` の後はその 2 倍空ける。
- サイズは Python の整数で足す。総バイト数は 2^53 を超えるので、倍精度で足すと合計がずれる。

**重要:** `MISMATCH` の年があると、合計は取れた年だけで出て、終了コード 1 で終わる。表示されたコマンドで、その年だけ取り直す。`per_year.tsv` へは追記するので、取り直した年は最後の行が使われ、合計も作り直される。

```bash
cd <repository root>
bash scale/ebi/survey_sizes.sh --from-year 2020 --to-year 2020 out/ebi/2026-09-27
```

## survey_sizes.sh

ERR のファイル系統別（fastq、submitted、sra、bam）に、ファイル数と総サイズを年単位で取る。1 列に `;` 区切りで複数ファイルのサイズが入るので、ファイル単位に分けて数える。

```text
$ bash scale/ebi/survey_sizes.sh --help
使い方: survey_sizes.sh [オプション] <出力先>
  ERR のファイル系統別（fastq・submitted・sra・bam）の件数と総サイズを年単位で取る。
  --from-year N  取得する最初の年（既定 2008）
  --to-year N    取得する最後の年（既定 実行した年）
  --sleep S      年スライス間の待ち秒数（既定 5。失敗したスライスの後はその2倍）
  -h, --help     この説明を出す
```

```bash
cd <repository root>
bash scale/ebi/survey_sizes.sh out/ebi/2026-09-27
```

出力先に次のファイルができる。

- `raw/err_<年>.tsv.gz`: Portal API から取った年ごとの生データ。列は `run_accession`、`fastq_bytes`、`submitted_bytes`、`sra_bytes`、`bam_bytes`
- `per_year.tsv`: 年ごとの件数、行数、状態、系統別のファイル数とバイト数。取り直すと追記される
- `total.txt`: 全年の合計。全年を回し終えたときにだけ書かれる

`sra` は 0 になる。Portal API が SRA 形式ファイルのサイズを返さないためである。EBI にある SRA 形式ファイルの規模は、cross_archive の [`survey_ebi_sra_sizes.sh`](../../cross_archive/README.md#survey_ebi_sra_sizessh) で FTP から取る。

## aggregate_sizes.py

survey_sizes.sh の `raw/` から、系統別の保有 Run 数・coverage・ファイル数・総バイト数を集計する。survey_sizes.sh が内部で呼ぶので、ふだん単独では使わない。通信せずに集計し直したいときに使う。

```bash
cd <repository root>
python3 scale/ebi/aggregate_sizes.py out/ebi/2026-09-27/raw/err_*.tsv.gz
```

`--per-year` を付けると、年ごとの小計を `per_year.tsv` と同じ列で出す。`per_year.tsv` を `raw/` から作り直すときに使う。

```bash
cd <repository root>
python3 scale/ebi/aggregate_sizes.py --per-year out/ebi/2026-09-27/raw/err_*.tsv.gz
```

## scale_diff.py

2 回分の `raw/` を比べ、系統別にファイル数とバイト数がどれだけ増えたかを出す。EBI は公開済みの Run へ後からファイルを足す。そのとき `first_public` は変わらず、`last_updated` も更新されないことがある。そのため、増加は全件の差分でしか追えない。

```text
scale_diff.py <前回の raw/err_*.tsv.gz...> -- <今回の raw/err_*.tsv.gz...>
```

```bash
cd <repository root>
python3 scale/ebi/scale_diff.py out/ebi/2026-09-20/raw/err_*.tsv.gz -- out/ebi/2026-09-27/raw/err_*.tsv.gz
```

出力例は usecases の [2. EBI の規模を取る](../../usecases/README.md#2-ebi-の規模を取る) にある。

## survey_cram_sizes.sh

`submitted_format` が CRAM の ERR について、`.cram`、`.crai`、その他のファイル数と総サイズを年単位で取る。Run 単位の `submitted_bytes` を合計すると `.crai`（index）のサイズが混ざるので、`submitted_ftp` のファイル名と位置で対応させて分ける。

```text
$ bash scale/ebi/survey_cram_sizes.sh --help
使い方: survey_cram_sizes.sh [オプション] <出力先>
  submitted_format=CRAM の ERR について、.cram・.crai・その他の件数と総サイズを年単位で取る。
  --from-year N  取得する最初の年（既定 2008）
  --to-year N    取得する最後の年（既定 実行した年）
  --sleep S      年スライス間の待ち秒数（既定 5。失敗したスライスの後はその2倍）
  -h, --help     この説明を出す
```

```bash
cd <repository root>
bash scale/ebi/survey_cram_sizes.sh out/cram
```

出力先には、survey_sizes.sh と同じ形で `raw/cram_<年>.tsv.gz`、`per_year.tsv`、`total.txt` ができる。`raw/` の列は `run_accession`、`submitted_bytes`、`submitted_ftp` である。

## aggregate_cram_sizes.py

survey_cram_sizes.sh の `raw/` から、`.cram`、`.crai`、その他のファイル数と総バイト数を集計する。survey_cram_sizes.sh が内部で呼ぶ。`--per-year` の使い方は [aggregate_sizes.py](#aggregate_sizespy) と同じである。

```bash
cd <repository root>
python3 scale/ebi/aggregate_cram_sizes.py out/cram/raw/cram_*.tsv.gz
```

出力の「位置不一致」は、`submitted_bytes` と `submitted_ftp` でファイルの数が違った Run の数である。ファイル名とサイズを対応させられないので、その Run は集計から外す。0 でなければ、合計はその分だけ小さい。

## extract_livelist_runs.sh

EBI の livelist.gz（約 2.16 GB）から RUN 行の必要な列だけを抜き出し、重複を除いた一覧を作る。cross_archive の [`survey_ebi_sra_sizes.sh`](../../cross_archive/README.md#survey_ebi_sra_sizessh) がこの一覧を入力に取る。

```text
$ bash scale/ebi/extract_livelist_runs.sh --help
使い方: extract_livelist_runs.sh [オプション] <出力先>
  EBI の livelist.gz から RUN 行を抜き出し、重複を除いた一覧も作る。
  --livelist FILE  手元の livelist.gz を読む（既定は EBI から 2.16 GB を取得する）
  -h, --help       この説明を出す
```

```bash
cd <repository root>
bash scale/ebi/extract_livelist_runs.sh out/ebi-vs/livelist
```

出力先に次のファイルができる。

- `livelist_run.tsv.gz`: RUN 行。列は `accession`、`status`、`updated`、`sra_file`、`fastq_file`
- `livelist_run_dedup.tsv.gz`: 上から重複を除いたもの

livelist.gz の RUN 行は accession 順に並んでおらず、同じ内容の行が離れた位置に重複して現れる。そのため、全体を並べ替えて重複を除く。並び順がロケールで変わらないよう、`LC_ALL=C` で並べる。

livelist.gz をダウンロードし直さずに試すときは、手元のファイルを `--livelist` で渡す。途中で切れた livelist.gz を渡すと、一覧を残さずに終了コード 1 で終わる。

## ライセンス

MIT License で公開する。全文は [LICENSE](../../LICENSE) にある。

Copyright (c) 2026 fmaccha
