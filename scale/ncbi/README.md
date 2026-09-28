# scale/ncbi

このディレクトリには、NCBI の公式 Parquet カタログから SRA の規模を取り、前回との差分を出すスクリプトがある。NCBI の API には接続せず、AWS の匿名 S3 にある Parquet を DuckDB で読むだけである。

ふだんは [usecases/ncbi_scale.sh](../../usecases/README.md#1-ncbi-の規模を取る) から使う。ラッパーは次の順にスクリプトを呼ぶ。

1. [`survey_catalog.py`](#survey_catalogpy): カタログから snapshot を取り、規模を集計する
2. [`snapshot_diff.py`](#snapshot_diffpy): 前回の snapshot と突き合わせて差分を出す
3. [`scale_report.py`](#scale_reportpy): 規模と差分を 1 枚の規模レポートにする

[`filetype_stats.py`](#filetype_statspy) はラッパーから呼ばない。形式ごとの内訳が要るときに単独で使う。

必要なツールは、ルートの `README.md` の [動作環境](../../README.md#動作環境) にある。

---

## 目次

- [survey_catalog.py](#survey_catalogpy)
- [snapshot_diff.py](#snapshot_diffpy)
- [scale_report.py](#scale_reportpy)
- [filetype_stats.py](#filetype_statspy)
- [カタログの列の扱い](#カタログの列の扱い)

## survey_catalog.py

公式 Parquet カタログから、`--prefix` で選んだ public Run の一覧（snapshot）を取り、SRA Normalized と SRA Lite の Run 数・総容量・coverage を集計する。全件を読むので、三極合計で約 13 分、SRR だけで約 10 分かかる。

```text
$ python3 scale/ncbi/survey_catalog.py --help
usage: survey_catalog.py [-h] --prefix {DRR,ERR,SRR} [{DRR,ERR,SRR} ...]
                         --output-directory OUTPUT_DIRECTORY [--label LABEL]
                         [--parquet-glob PARQUET_GLOB] [--dry-run]

公式Parquetカタログから public Run の一覧と SRA Normalized の規模を出す。

規模調査の最初の段で、snapshot_diff.py と scale_report.py がこの出力を読む。
NCBI の API は叩かず、公式Parquetだけを1スキャンして次を得る。
対象は `--prefix` で選ぶ。NCBIのカタログには SRR だけでなく、EBI由来の ERR と
DDBJ由来の DRR も入っている。三極合計なら `--prefix SRR ERR DRR`、SRRだけなら
`--prefix SRR` とする。既定値は置かない。前回と違う対象でsnapshotを取ると、
差分で対象の差がそのまま増減に見えるので、毎回明示させる。

  - public Run の accession 一覧（snapshot。次回との差分の元になる）
  - SRA Normalized の Run 数・総容量・coverage

Parquetの `mbytes` 列は SRA Normalized のサイズであり、**単位はMiBである**。
代表6 Runで確認済み（SRR000001: 312,527,083 B / 2^20 = 298.05 → mbytes 298）。

SRA Lite の**有無**は `datastore_filetype` の `run.zq` で判定できる。1,834万件を
実測と照合し、不一致7件（0.00004%）であることを確認済み。したがって
coverage はこのscriptだけで出せる。ただし**容量の実測値は出せない**（mbytes は
SRA Normalized のサイズのみ）。Lite の容量は、Normalized の容量に係数を掛けた推定で出す。

再開について:
  出力は accession の辞書順で書く。checkpoint に最後に書いた accession を残し、
  再実行時は `acc > <last>` を足して続きから取得する。途中で落ちても最初からやり直さない。

使い方:
  python3 survey_catalog.py --prefix SRR ERR DRR --output-directory snapshots --dry-run
  python3 survey_catalog.py --prefix SRR --output-directory snapshots

options:
  -h, --help            show this help message and exit
  --prefix {DRR,ERR,SRR} [{DRR,ERR,SRR} ...]
                        対象のRun accession prefix。三極合計なら SRR ERR DRR
  --output-directory OUTPUT_DIRECTORY
  --label LABEL         出力ファイル名の接頭辞。既定は実行日
  --parquet-glob PARQUET_GLOB
  --dry-run             通信せず計画だけ表示する
```

`--prefix` に既定値は無い。三極合計なら `SRR ERR DRR`、一部だけなら `SRR` のように、毎回指定する。前回と違う対象で snapshot を取ると、対象の差がそのまま増減に見えるためである。

```bash
python3 scale/ncbi/survey_catalog.py --prefix SRR ERR DRR --output-directory out/ncbi/DRR-ERR-SRR
```

`--output-directory` に次のファイルができる。`<label>` は既定で実行日である。

- `<label>-runs.jsonl.gz`: snapshot。1 行 1 Run
- `<label>-report.json`: 規模の集計。合計と `by_prefix`（prefix ごとの内訳）を持つ
- `<label>-checkpoint.tsv`: 再開用。最後に書いた accession、行数、対象の prefix

snapshot の 1 行は次の形である。

```json
{"accession": "DRR000001", "sra_normalized_mbytes": 568, "has_sra_normalized": true, "has_sra_lite": true, "datastore_filetype": ["Illumina_native", "run.zq", "sra"], "releasedate": "2010-03-23", "run_file_version": 3}
```

- `sra_normalized_mbytes`: SRA Normalized のサイズ。単位は MiB
- `datastore_filetype`: その Run が持つ形式の配列。前後の空白を落とし、重複を除いて並べ替えてある
- `run_file_version`: ファイルが作り直された回数。上がった Run は、accession が同じままファイルが差し替わっている

### 途中で止まったとき

同じコマンドをもう一度実行すると、checkpoint の続きから取る。checkpoint は 100,000 行ごとに書く。次の場合は続きから取れないので、案内を出して止まる。

- checkpoint の対象 prefix と、今回の `--prefix` が違う
- 強制終了で snapshot の末尾が壊れている
- 最初の checkpoint を書く前に止まり、snapshot だけが残っている（Ctrl-C で止めた場合は、残さずに消す）

止まったときは、表示された snapshot と checkpoint を消して取り直す。

## snapshot_diff.py

2 つの snapshot を突き合わせ、前回からの差分を出す。通信はしない。SRR だけの snapshot 同士で約 4 分かかる。

```text
$ python3 scale/ncbi/snapshot_diff.py --help
usage: snapshot_diff.py [-h] --old OLD --new NEW --output-directory OUTPUT_DIRECTORY
                        [--label LABEL] [--lite-ratio LITE_RATIO] [--dry-run]

2つのsnapshotの差分を出し、逆向き差分を積む。

survey_catalog.py が取った snapshot 2つを突き合わせる。通信はしない。

snapshotは accession の辞書順で書かれているので、2つを同時に読み進めれば全体を
メモリへ載せずに突き合わせられる。public Run は3,000万件あり、2つ分を辞書へ載せる
実装は計算ノードでも現実的でない。

差分の3種:
  added     新しいsnapshotにだけあるRun。新規に公開されたもの
  removed   古いsnapshotにだけあるRun。カタログから消えたことだけを表し、
            取り下げか、一時的にカタログに載らなかっただけかは区別しない
  modified  両方にあるが内容が変わったRun。サイズ変化、Liteの後付け、実体の作り直し

**前回からの増加は新規Runだけでは測れない。** NCBIは既にあるRunのファイルを作り直す
ことがあり、公開Runの約9%（277万件）が `run_file_version` 2以上である。また SRA Lite は
NCBIが後から生成するため、既存Runにあとから付く。この2つを modified として拾う。

対象prefix:
  survey_catalog.py は `--prefix` で対象（SRRだけ、三極など）を選ぶ。対象の違う
  snapshot同士を比べると、対象の差がそのまま added・removed に出るので、
  両方に現れたprefixの集合が一致しないときは結果を残さずに止める。

逆向き差分:
  最新の完全な一覧を実体として持ち、過去へ遡る差分を積む。最新の参照が常にO(1)で済む。
  rdiff は「新しいsnapshotへ適用すると古いsnapshotになる」操作列である。

使い方:
  python3 snapshot_diff.py --old 2026-08-02-runs.jsonl.gz --new 2026-08-09-runs.jsonl.gz \
      --output-directory snapshots --dry-run

options:
  -h, --help            show this help message and exit
  --old OLD             前回のsnapshot（jsonl.gz）
  --new NEW             今回のsnapshot（jsonl.gz）
  --output-directory OUTPUT_DIRECTORY
  --label LABEL         出力ファイル名の接頭辞。既定は実行日
  --lite-ratio LITE_RATIO
                        SRA Lite 容量の推定に使う係数
  --dry-run             読み書きせず計画だけ表示する
```

```bash
python3 scale/ncbi/snapshot_diff.py \
    --old out/ncbi/SRR/2026-09-20-runs.jsonl.gz \
    --new out/ncbi/SRR/2026-09-27-runs.jsonl.gz \
    --output-directory out/ncbi/SRR \
    --label 2026-09-27
```

`--output-directory` に次のファイルができる。

- `<label>-diff-report.json`: 差分の集計
- `<label>-added-runs.txt`: 新しく現れた Run の accession
- `<label>-removed-runs.txt`: カタログから消えた Run の accession
- `<label>-changed-runs.tsv`: 内容が変わった Run と、変わった項目。比べる項目は `sra_normalized_mbytes`、`has_sra_normalized`、`has_sra_lite`、`datastore_filetype`、`run_file_version`、`releasedate` である
- `<label>-diff.jsonl.gz`: Run ごとの変化と、その前後の値
- `<label>.rdiff.gz`: 逆向き差分。新しい snapshot に適用すると古い snapshot に戻る操作の列

**重要:** 2 つの snapshot に現れた prefix の集合が違うと、結果を残さずに止まる。SRR だけの snapshot と三極の snapshot を比べると、ERR と DRR がすべて「新しく現れた」に数えられるためである。

snapshot は accession の辞書順に並んでいる前提で、2 つを同時に読み進める。全件をメモリに載せないので、3,000 万件を超える snapshot でも動く。並びが崩れた snapshot を渡すと止まる。

## scale_report.py

survey_catalog.py と snapshot_diff.py の集計を、1 枚の規模レポートにする。SRA Normalized の容量は実測、SRA Lite の容量は推定であることを、レポートの中で区別して書く。

```text
$ python3 scale/ncbi/scale_report.py --help
usage: scale_report.py [-h] --catalog-report CATALOG_REPORT [--diff-report DIFF_REPORT]
                       [--output OUTPUT]

規模レポートを1枚に組み立てる。

SRA Lite と SRA Normalized それぞれの Run数・総容量・coverage を、どの時点の
カタログを測ったか（snapshot境界）と前回からの差分とともに1枚にまとめる。
catalog の集計（survey_catalog.py）と差分の集計（snapshot_diff.py）を突き合わせて作る。

**実測と推定を混ぜない。** SRA Normalized の容量は Parquet の `mbytes` による実測、
SRA Lite の容量は係数を掛けた推定である。同じ表に並べる以上、どちらがどちらかを
レポート自身が明示する。SRA Lite の**件数**は `run.zq` による実測なので、同じ
「SRA Lite」の行でも件数と容量で根拠が違う。

使い方:
  python3 scale_report.py --catalog-report 2026-08-10-report.json \
      --diff-report 2026-08-10-diff-report.json --output 2026-08-10-summary.json

options:
  -h, --help            show this help message and exit
  --catalog-report CATALOG_REPORT
                        survey_catalog.py が出した {label}-report.json
  --diff-report DIFF_REPORT
                        snapshot_diff.py が出した {label}-diff-report.json。初回は省略する
  --output OUTPUT       機械可読な規模レポートの書き出し先
```

```bash
python3 scale/ncbi/scale_report.py \
    --catalog-report out/ncbi/SRR/2026-09-27-report.json \
    --diff-report out/ncbi/SRR/2026-09-27-diff-report.json \
    --output out/ncbi/SRR/2026-09-27-summary.json
```

人が読む形は標準出力に出る。出力例は usecases の [三極合計で取りたいとき](../../usecases/README.md#三極合計で取りたいとき) にある。

- 初回は `--diff-report` を省く。規模だけのレポートになる。
- catalog と diff の対象 prefix が違うと止まる。
- 新しく現れた Run と消えた Run の容量は、別々に出す。合算した純増は出さない。消えた Run は、取り下げられたのか一時的にカタログに載らなかっただけなのかを区別していないので、増えた分から引くと性質の違う数字が混ざるためである。

## filetype_stats.py

snapshot か差分から、形式（`datastore_filetype` の値）ごとの Run 数と容量を出す。規模レポートには含めない内訳で、必要なときに単独で使う。

```text
$ python3 scale/ncbi/filetype_stats.py --help
usage: filetype_stats.py [-h] (--snapshot SNAPSHOT | --diff DIFF)

snapshotと差分から、形式ごとの規模と増減を出す。

`datastore_filetype` はそのRunが持つ全形式の配列である。survey_catalog.py が
snapshotへ配列を保存しているので、後から形式単位で数え直せる。規模レポートの
定型集計には含めず、必要になったときにこのscriptで出す。

  形式の例: sra（SRA Normalized）、run.zq（SRA Lite）、fastq / bam / sff（投稿形式）、
            realign / activ_sars2_vcf / wgmlst_sig（NCBIが作る派生物）

使い方:
  python3 filetype_stats.py --snapshot 2026-08-22-runs.jsonl.gz
  python3 filetype_stats.py --diff 2026-08-22-diff.jsonl.gz

options:
  -h, --help           show this help message and exit
  --snapshot SNAPSHOT  {label}-runs.jsonl.gz
  --diff DIFF          {label}-diff.jsonl.gz
```

```bash
python3 scale/ncbi/filetype_stats.py --snapshot out/ncbi/SRR/2026-09-27-runs.jsonl.gz
python3 scale/ncbi/filetype_stats.py --diff out/ncbi/SRR/2026-09-27-diff.jsonl.gz
```

容量は「その形式を持つ Run の SRA Normalized のサイズ」であって、その形式のファイル自体のサイズではない。カタログは SRA Normalized のサイズしか持たないためである。

## カタログの列の扱い

- `mbytes` の単位は MiB である。byte に直すときは 1,048,576 を掛ける。`mbytes` は INTEGER なので、掛ける前に BIGINT へ寄せないと 2 GiB を超える Run で桁があふれる。
- 形式の有無は `datastore_filetype` で判定し、サイズでは判定しない。`mbytes` は MiB へ丸められるので、1 MiB 未満の Run は 0 になる。
    - `sra` は SRA Normalized を、`run.zq` は SRA Lite を表す。
    - 値の末尾に空白が付いた異体（`fastq` と `fastq ` など）が混ざるので、比べる前に空白を落とす。
- SRA Lite の容量はカタログに無い。Lite を持つ Run の SRA Normalized の容量に、係数 0.6379 を掛けて推定する。係数は SRR の実測から出した値で、ERR と DRR にも同じ値を当てる。差分の推定に使う係数だけは、`snapshot_diff.py` の `--lite-ratio` で変えられる。
- カタログは常に更新される。集計するたびに総数が変わるので、`<label>` の日付と一緒に扱う。

## ライセンス

MIT License で公開する。全文は [LICENSE](../../LICENSE) にある。

Copyright (c) 2026 fmaccha
