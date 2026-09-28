# usecases

このディレクトリには、ユースケースごとのラッパーがある。各ラッパーは [scale/ncbi/](../scale/ncbi/)、[scale/ebi/](../scale/ebi/)、[cross_archive/](../cross_archive/) のスクリプトを正しい順番で呼ぶ。前回の結果を探すこと、途中で止まったときに続きから再開すること、欠けがあるときに止まることも、ラッパーの中で行う。

| # | やりたいこと | コマンド | 所要時間の目安 | 繰り返す意味 |
|---|---|---|---|---|
| 1 | NCBI にある SRA の規模を取り、前回との差分を出す | [`ncbi_scale.sh`](#1-ncbi-の規模を取る) | 10〜15 分 | ある（差分が出る） |
| 2 | EBI にある ERR のファイル系統別の規模を取り、前回との差分を出す | [`ebi_scale.sh`](#2-ebi-の規模を取る) | 20 分 | ある（差分が出る） |
| 3 | EBI にある CRAM の規模を取る | [`ebi_cram.sh`](#3-ebi-の-cram-の規模を取る) | 10 分 | 無い（その時点の規模だけ） |
| 4 | NCBI と EBI で ERR の SRA 形式ファイルの保有を比べる | [`ncbi_vs_ebi.sh`](#4-ncbi-と-ebi-の保有を比べる) | 1 時間以上 | 無い（その時点の比較だけ） |
| 5 | NCBI と DDBJ で DRR の保有を比べる | [`ncbi_vs_ddbj.sh`](#5-ncbi-と-ddbj-の保有を比べる) | 1 時間弱 | 無い（その時点の比較だけ） |

所要時間は、動作を確認した環境での実測をもとにした目安である。

どのコマンドも、1 回実行するとその時点の規模や比較を 1 回分取って終わる。1 と 2 は、同じ出力先で繰り返し実行すると、前回の結果との差分も出す。繰り返す間隔は自由で、週 1 回でも 3 日おきでもよい。定期的に実行したいときは、cron などは各自で設定する。

---

## 目次

- [0. 環境用意](#0-環境用意)
- [ラッパーに共通する動き](#ラッパーに共通する動き)
- [1. NCBI の規模を取る](#1-ncbi-の規模を取る)
    - [三極合計で取りたいとき](#三極合計で取りたいとき)
    - [どれかだけ取りたいとき（例 SRR）](#どれかだけ取りたいとき例-srr)
    - [NCBI の規模の出力](#ncbi-の規模の出力)
    - [差分を prefix ごとに見る](#差分を-prefix-ごとに見る)
    - [NCBI の規模の数字の読み方](#ncbi-の規模の数字の読み方)
- [2. EBI の規模を取る](#2-ebi-の規模を取る)
- [3. EBI の CRAM の規模を取る](#3-ebi-の-cram-の規模を取る)
- [4. NCBI と EBI の保有を比べる](#4-ncbi-と-ebi-の保有を比べる)
- [5. NCBI と DDBJ の保有を比べる](#5-ncbi-と-ddbj-の保有を比べる)

## 0. 環境用意

必要なツールの入れ方と接続先は、ルートの `README.md` の [動作環境](../README.md#動作環境) にある。

## ラッパーに共通する動き

- 例では、出力先を `out/` の下にしている。`out/` はコマンドを実行した場所に作られる。
- 出力先は必ず指定する。既定の出力先は持たない。リポジトリの中を指定しても、大きな出力（`*.jsonl.gz`、`*.tsv.gz`、`*.rdiff.gz`）は `.gitignore` で除外される。
- `--dry-run` で、実行するコマンドを確かめられる。何も実行せず、外部へも接続しない。
- 止まったら同じコマンドをもう一度実行する。どのラッパーも、取得済みの分を飛ばして続きから進める。
- 欠けがあると、集計や比較の前に止まる。取得に失敗した年やディレクトリが残ったまま比べると、欠けた分が「消えた」「片方にしか無い」に数えられるためである。止まったときは、取り直すコマンドが表示される。
- 外部へのリクエストは逐次で、間隔を空けて送る。並列には送らない。

実行するコマンドは、次のように `+` 付きで表示される（`<repo>` はリポジトリの場所）。

```text
+ python3 <repo>/scale/ncbi/survey_catalog.py --prefix DRR ERR SRR --output-directory out/ncbi/DRR-ERR-SRR --label 2026-09-27
```

## 1. NCBI の規模を取る

NCBI の公式 Parquet カタログから public Run の一覧（snapshot）を取り、SRA Normalized と SRA Lite の Run 数・総容量・coverage を出す。前回の snapshot があれば、前回からの差分と、次に取得し直すべき Run の一覧も出す。NCBI の API には接続せず、AWS の匿名 S3 から Parquet を読むだけである。

NCBI のカタログには、NCBI に投稿された SRR だけでなく、EBI 由来の ERR と DDBJ 由来の DRR も入っている。どの prefix を対象にするかを、実行するたびに指定する。

```text
ncbi_scale.sh [オプション] <出力先> <PREFIX>...
```

```text
$ bash usecases/ncbi_scale.sh --help
使い方: ncbi_scale.sh [オプション] <出力先> <PREFIX>...
  NCBI SRA の公式Parquetカタログから、指定した PREFIX の public Run の snapshot を取り、
  前回の snapshot との差分と規模レポートを出す。
  PREFIX は SRR・ERR・DRR から1つ以上。三極合計なら SRR ERR DRR、SRRだけなら SRR。
  出力は <出力先>/<PREFIXを辞書順に-でつないだ名前>/ に日付付きで溜まる
  （例 <出力先>/DRR-ERR-SRR/2026-09-27-summary.txt）。対象ごとにディレクトリが分かれる。
  同じ日付で再実行すると、完了済みなら取り直さず、途中なら続きから取る。
  --previous FILE  比べる前回の snapshot（*-runs.jsonl.gz）。
                   既定は同じディレクトリで今回より前の、完了した最新の snapshot
  --label DATE     出力ファイル名の日付（YYYY-MM-DD。既定 実行日）
  --dry-run        実行するコマンドを表示するだけで、何も実行しない
  -h, --help       この説明を出す
```

### 三極合計で取りたいとき

PREFIX に `SRR ERR DRR` を並べる。

```bash
cd <repository root>
source .venv/bin/activate
```

```bash
bash usecases/ncbi_scale.sh out/ncbi SRR ERR DRR
```

この例では、結果は `out/ncbi/DRR-ERR-SRR/` に出る。

合計に加えて、prefix ごとの内訳が出る。初回は前回の snapshot が無いので、規模だけを出す。

```text
=== 規模レポート 2026-09-27 ===

  対象         : DRR, ERR, SRR
  snapshot境界 : s3://sra-pub-metadata-us-east-1/sra/metadata/*
  所要         : 759.6s

  public Run 総数            :     42,616,426

  --- SRA Normalized（実測） ---
  Run数                      :     42,477,154
  coverage                   :       99.6732%
  総容量                     : 41.967 PB / 37.274 PiB

  --- SRA Lite（件数は実測、容量は推定） ---
  Run数                      :     40,310,325
  coverage                   :       94.5887%
  総容量（推定）             : 25.400 PB / 22.560 PiB
    ※ 係数 0.6379 による推定である。Parquetは SRA Lite のサイズを持たない。
       根拠は両形式のサイズが確定したSRR 17,544,315 Run の実測。
       係数は SRR の実測から出した値で、他のprefixにも同じ係数を当てている。

  --- prefixごとの内訳 ---
  prefix          Run数  Normalized coverage / 総容量      Lite coverage / 総容量（推定）
  DRR            879,698   99.1899% /   1.470 PB   94.0863% /   0.887 PB
  ERR         10,773,101   98.9802% /   6.656 PB   94.9502% /   3.923 PB
  SRR         30,963,627   99.9280% /  33.842 PB   94.4772% /  20.590 PB

  前回のsnapshotが無いため、変化は出せない。
```

### どれかだけ取りたいとき（例 SRR）

PREFIX に対象だけを書く。SRR だけなら `SRR`、EBI 由来の ERR だけなら `ERR` とする。

```bash
cd <repository root>
source .venv/bin/activate
```

```bash
bash usecases/ncbi_scale.sh out/ncbi SRR
```

この例では、結果は対象ごとのディレクトリ `out/ncbi/SRR/` に出る。

同じ出力先で、三極合計と SRR だけの取得を並行して繰り返せる。前回の snapshot は同じ対象のディレクトリからだけ探すので、対象の違う snapshot 同士を比べることはない。`--previous` で対象の違う snapshot を指定しても、差分を出す前に止まる。

前回の snapshot があると、規模の後に差分が加わる。次は、出力先に `ncbi` を指定し、SRR の 2026-08-22 と 2026-09-27 の snapshot を比べたときの差分の部分である。

```text
  --- 前回からの変化（ncbi/SRR/2026-08-22-runs.jsonl.gz との比較） ---
  新規に現れた Run           :        430,598
    うち SRA Lite を持つ     :        396,988
    SRA Normalized の容量    : 0.692 PB / 0.615 PiB
    SRA Lite の容量（推定）  : 0.408 PB / 0.362 PiB
  消えた Run                 :          3,300
    うち SRA Lite を持つ     :          3,174
    SRA Normalized の容量    : 0.005 PB / 0.005 PiB
    SRA Lite の容量（推定）  : 0.003 PB / 0.003 PiB
  内容が変わった Run         :          4,031
    Lite が後から付いた      :             31（推定 0.000 PB）
    実体が作り直された       :            290
    既存Runの容量増減        : 0.001 PB / 0.001 PiB

  取得し直しが要る Run       :        434,629
    新規Runと内容が変わったRunの合計。増減の合算ではない。
```

### NCBI の規模の出力

`<出力先>/<対象>/` に、日付（`--label`）を頭に付けたファイルが溜まる。

レポート:

- `<label>-summary.txt`: 人が読む規模レポート。上の出力例と同じもの
- `<label>-summary.json`: 同じ内容の機械可読版
- `<label>-report.json`: 規模の集計。合計と `by_prefix`（prefix ごとの内訳）を持つ
- `<label>-diff-report.json`: 差分の集計

前回からの変化の一覧（前回の snapshot があるときだけ）:

- `<label>-added-runs.txt`: 新しく現れた Run の accession。1 行 1 件
- `<label>-changed-runs.tsv`: 内容が変わった Run と、変わった項目（`accession`、`changed_fields`）
- `<label>-removed-runs.txt`: カタログから消えた Run の accession

`added-runs.txt` と `changed-runs.tsv` に載っている Run を合わせたものが、取得し直しが要る Run である。規模レポートの「取得し直しが要る Run」は、その件数である。

snapshot と差分の本体:

- `<label>-runs.jsonl.gz`: snapshot。1 行 1 Run で、SRA Normalized のサイズ（MiB）、形式の配列、公開日、`run_file_version` を持つ。**次回の比較に使うので消さない**
- `<label>-diff.jsonl.gz`: Run ごとの変化。追加・消失・変化の前後の値を持つ
- `<label>.rdiff.gz`: 逆向き差分。今回の snapshot に適用すると前回の snapshot に戻る操作の列
- `<label>-checkpoint.tsv`: 途中で止まったときの再開用。取得が終わった後は使わない

snapshot は三極合計で 1 回あたり約 230 MB、SRR だけで約 170 MB になる。

### 差分を prefix ごとに見る

規模レポートの差分は、対象全体の合計だけを出す。prefix ごとの件数は、一覧ファイルを grep すれば出る。

```bash
cd <repository root>
```

```bash
grep -c '^ERR' out/ncbi/DRR-ERR-SRR/2026-09-27-added-runs.txt    # 新しく現れた ERR の件数
grep -c '^DRR' out/ncbi/DRR-ERR-SRR/2026-09-27-removed-runs.txt  # 消えた DRR の件数
grep '^ERR' out/ncbi/DRR-ERR-SRR/2026-09-27-changed-runs.tsv     # 内容が変わった ERR と変わった項目
```

容量は `<label>-diff.jsonl.gz` にしか無いので、DuckDB で集計する。新しく現れた Run の SRA Normalized の容量を prefix ごとに出す例を示す。

```sql
SELECT substr(accession, 1, 3) AS prefix,
       count(*) AS runs,
       sum(CAST(record.sra_normalized_mbytes AS BIGINT)) * 1048576 AS bytes
FROM read_json_auto('out/ncbi/DRR-ERR-SRR/2026-09-27-diff.jsonl.gz')
WHERE change = 'added'
GROUP BY prefix;
```

`sra_normalized_mbytes` の単位は MiB なので、1,048,576 を掛けて byte にする。`CAST` を外すと、2 GiB を超える Run で桁があふれる。

### NCBI の規模の数字の読み方

- SRA Normalized の容量は実測である。カタログの `mbytes`（単位は MiB）を合計している。
- SRA Lite の件数は実測、容量は推定である。件数は、カタログの `datastore_filetype` にある `run.zq` で数える。容量はカタログに無いので、Lite を持つ Run の SRA Normalized の容量に係数 0.6379 を掛けて出す。
    - 係数は、両形式のサイズが分かっている SRR の実測から出した値である。ERR と DRR にも同じ係数を当てているが、同じ比になるかは確かめていない。
    - 係数は古い accession 帯に偏った実測から出しているので、推定は小さめに出るおそれがある。
- 対象は public の Run だけである。controlled-access の Run は数えない。
- 「消えた Run」は、カタログから消えたことだけを表す。取り下げられたのか、一時的にカタログに載らなかっただけなのかは区別しない。
- 「内容が変わった Run」には、既存の Run のファイルの作り直しと、SRA Lite の後付けが入る。NCBI は既にある Run のファイルを作り直すことがあり、SRA Lite も後から生成する。そのため、前回からの増加は新規の Run だけでは測れない。

各スクリプトの詳細は [scale/ncbi/README.md](../scale/ncbi/README.md) にある。

## 2. EBI の規模を取る

ENA Portal API から、ERR のファイル系統別（fastq、submitted、sra、bam）の件数と総サイズを年単位で取る。前回の取得結果があれば、前回からの差分も出す。EBI は公開済みの Run へ後からファイルを足すため、増加は全件の差分でしか追えない。

```text
ebi_scale.sh [オプション] <出力先>
```

```text
$ bash usecases/ebi_scale.sh --help
使い方: ebi_scale.sh [オプション] <出力先>
  ENA Portal API から ERR のファイル系統別（fastq・submitted・sra・bam）の件数と
  総サイズを年単位で取り、前回の取得結果との差分を出す。
  出力は <出力先>/<日付>/ に溜まる。同じ日付で再実行すると、取得済みなら取り直さない。
  --previous DIR  比べる前回の取得結果（<出力先>/<日付>）。
                  既定は今回より前で、全年がそろった最新の取得結果
  --label DATE    取得結果のディレクトリ名（YYYY-MM-DD。既定 実行日）
  --sleep S       年スライス間の待ち秒数（survey_sizes.sh へ渡す。既定 5）
  --dry-run       実行するコマンドを表示するだけで、何も実行しない
  -h, --help      この説明を出す
```

```bash
cd <repository root>
```

```bash
bash usecases/ebi_scale.sh out/ebi
```

この例では、結果は `out/ebi/<実行日>/` に出る。

1 年につき、件数の問い合わせを取得の前後に 1 回ずつ、取得そのものを 1 回、計 3 リクエストを送る。2008 年から実行した年までで約 50 リクエストになり、約 20 分かかる。

規模は `total.txt` に出る。

```text
取得時刻(UTC): 2026-09-27T13:53:05Z
対象ファイル 17 個 / 対象Run 10,768,079
系統                 保有Run数  coverage         ファイル数                            バイト数        PB
fastq          10,643,359    98.84%    22,121,088           9,237,027,599,529,132     9.237
submitted      10,763,173    99.95%    19,819,018          11,439,930,222,462,133    11.440
sra                     0     0.00%             0                               0     0.000
bam             4,117,534    38.24%     4,117,534           4,209,307,074,682,309     4.209

合計バイト数 24,886,264,896,673,574 = 24.886 PB
```

`sra` は 0 になる。ENA Portal API が SRA 形式ファイルのサイズを返さないためである。EBI にある SRA 形式ファイルの規模は、[4. NCBI と EBI の保有を比べる](#4-ncbi-と-ebi-の保有を比べる)で FTP から取る。

前回の取得結果があると、差分が `scale_diff_<前回>--<今回>.txt` に出る。次は、2026-08-30 と 2026-09-27 の取得結果を比べた例である。

```text
前回 10,693,228 Run / 今回 10,768,079 Run
新規Run     : 91,162
消滅Run     : 16,311
内容変化Run : 1,809
系統                 ファイル増減                 バイト増減        TB
fastq             135,272    94,008,469,590,234    94.008
submitted         152,315   136,300,097,451,804   136.300
sra                     0                     0     0.000
bam                62,481    76,443,654,036,876    76.444

合計 350,068 ファイル / 306,752,221,078,914 bytes
```

`<出力先>/<日付>/` には次のファイルができる。

- `total.txt`: 規模の合計。全年を回し終えたときにだけ書かれる
- `per_year.tsv`: 年ごとの件数、状態、系統別の小計
- `raw/err_<年>.tsv.gz`: Portal API から取った年ごとの生データ。差分はこれを比べる
- `scale_diff_<前回>--<今回>.txt`: 前回からの差分

**重要:** 途中で本文が切れた年は、`per_year.tsv` の状態が `MISMATCH` になり、ラッパーは差分を出す前に止まる。欠けた年のまま比べると、その年の Run がすべて「消滅」に見えるためである。表示されたコマンドでその年だけ取り直してから、同じラッパーをもう一度実行する。

```bash
bash scale/ebi/survey_sizes.sh --from-year 2020 --to-year 2020 out/ebi/2026-09-27
bash usecases/ebi_scale.sh out/ebi
```

各スクリプトの詳細は [scale/ebi/README.md](../scale/ebi/README.md) にある。

## 3. EBI の CRAM の規模を取る

`submitted_format` が CRAM の ERR について、`.cram`、`.crai`（index）、その他のファイルの件数と総サイズを年単位で取る。Run 単位の `submitted_bytes` を合計すると index のサイズが混ざるので、ファイル名で分けて数える。

`ebi_cram.sh` は、引数をそのまま [scale/ebi/survey_cram_sizes.sh](../scale/ebi/README.md#survey_cram_sizessh) へ渡す。

```text
$ bash usecases/ebi_cram.sh --help
ユースケース3: EBI CRAM 規模。scale/ebi/survey_cram_sizes.sh をそのまま呼ぶ。

使い方: survey_cram_sizes.sh [オプション] <出力先>
  submitted_format=CRAM の ERR について、.cram・.crai・その他の件数と総サイズを年単位で取る。
  --from-year N  取得する最初の年（既定 2008）
  --to-year N    取得する最後の年（既定 実行した年）
  --sleep S      年スライス間の待ち秒数（既定 5。失敗したスライスの後はその2倍）
  -h, --help     この説明を出す
```

```bash
cd <repository root>
```

```bash
bash usecases/ebi_cram.sh out/cram
```

この例では、結果は `out/cram/` に出る。

リクエストの送り方は [2. EBI の規模を取る](#2-ebi-の規模を取る)と同じで、約 10 分かかる。規模は `total.txt` に出る。

```text
取得時刻(UTC): 2026-09-27T14:21:24Z
対象ファイル 14 個 / 対象Run 3,523,155 / 位置不一致 0
種別              ファイル数                            バイト数        PB
cram        3,523,155           1,611,888,259,918,955     1.612
crai        3,367,198                  52,754,042,953     0.000
other               0                               0     0.000

合計バイト数 1,611,941,013,961,908 = 1.612 PB
```

取得に失敗した年があると、合計は取れた年だけで出て、終了コード 1 で終わる。表示されたコマンドでその年だけ取り直すと、合計も作り直される。

```bash
bash usecases/ebi_cram.sh --from-year 2020 --to-year 2020 out/cram
```

## 4. NCBI と EBI の保有を比べる

EBI の FTP にある ERR の SRA 形式ファイルと、NCBI のカタログにある ERR を突き合わせる。EBI にしか無い Run を、NCBI に無い理由付きで一覧にする。

```text
$ bash usecases/ncbi_vs_ebi.sh --help
使い方: ncbi_vs_ebi.sh [オプション] <出力先>
  EBI にある ERR の SRA 形式ファイルと、NCBI カタログの ERR を突き合わせる。
    1. livelist.gz（2.16 GB）から RUN 一覧を作る          → <出力先>/livelist/
    2. EBI FTP を1ディレクトリ1リクエストで数える（約4,200ディレクトリ、1時間以上）
                                                          → <出力先>/ebi_sra/
    3. NCBI カタログと突き合わせる（約20分）              → <出力先>/ebi-only.tsv
                                                             <出力先>/ncbi-only.tsv
  比べるのは1ファイルの SRA 形式だけで、ディレクトリで置かれた vdb 形式の ERR は対象外。
  中断しても同じコマンドで続きから再開する。1 は作成済みなら飛ばす。
  --livelist FILE  手元の livelist.gz を使う（1 へ渡す）
  --batch N        1回の curl で引くディレクトリ数（2 へ渡す。既定 10）
  --sleep S        バッチ間の待ち秒数（2 へ渡す。既定 3）
  --dry-run        実行するコマンドを表示するだけで、何も実行しない
  -h, --help       この説明を出す
```

```bash
cd <repository root>
source .venv/bin/activate
```

```bash
bash usecases/ncbi_vs_ebi.sh out/ebi-vs
```

この例では、結果は `out/ebi-vs/` に出る。

手順 2 は、1 回の curl で 10 ディレクトリずつ引き、3 秒ずつ間を空ける。約 4,200 ディレクトリで約 420 回の接続になる。livelist.gz を別に取ってあれば、`--livelist` で渡すと手順 1 のダウンロードを省ける。

**重要:** 手順 2 で取れなかったディレクトリが残っていると、手順 3 の前に止まる。取りこぼしたまま比べると、EBI にあるのに NCBI に無い Run を見落とすためである。同じコマンドをもう一度実行すると、残りだけを取り直す。

結果は次の 2 つである。

- `ebi-only.tsv`: EBI にあって NCBI に無い Run
- `ncbi-only.tsv`: NCBI では SRA 形式を持つのに、EBI の FTP に SRA 形式ファイルが見つからない ERR の accession。800 万行を超える。EBI が SRA 形式ファイルを置いていない Run も、fastq などの形では持っていることが多いので、EBI に Run そのものが無いことは意味しない

`ebi-only.tsv` の列は次のとおりである。

| 列 | 内容 |
|---|---|
| `accession` | ERR accession |
| `ebi_bytes` | EBI の FTP にある SRA 形式ファイルのサイズ（byte） |
| `reason` | NCBI に無い理由 |
| `ncbi_consent` | NCBI のカタログの `consent`。カタログに行が無ければ空 |
| `ncbi_releasedate` | NCBI のカタログの公開日。カタログに行が無ければ空 |

`reason` は次の 3 つである。

| 値 | 意味 |
|---|---|
| `not_in_ncbi_catalog` | NCBI のカタログに行が無い |
| `in_catalog_no_sra` | カタログに行はあるが、SRA 形式（`sra`）を持たない |
| `in_catalog_no_filetype_info` | カタログに行はあるが、形式の情報（`datastore_filetype`）が空 |

- 比べるのは 1 ファイルの SRA 形式だけである。ディレクトリで置かれた vdb 形式の ERR は対象外で、実行時に件数だけを表示する。
- 同じ Run の SRA 形式ファイルでも、EBI と NCBI はそれぞれ別に作っていて、サイズは一致しないことが多い。どちらから取るかで、得られるファイルが変わる。

途中の生成物:

- `livelist/livelist_run.tsv.gz`、`livelist/livelist_run_dedup.tsv.gz`: livelist.gz から抜き出した RUN 行と、重複を除いた一覧
- `ebi_sra/files.tsv`: SRA 形式ファイルの accession とサイズ
- `ebi_sra/dirs.txt`、`done.txt`、`failed.txt`: 対象ディレクトリ、取得済み、失敗の一覧。再開に使う
- `ebi_sra/vdb_dirs.txt`: vdb 形式の ERR

各スクリプトの詳細は [cross_archive/README.md](../cross_archive/README.md) にある。

## 5. NCBI と DDBJ の保有を比べる

DDBJ の FTP にある DRX と、NCBI のカタログにある DRX を突き合わせる。DDBJ にしか無い DRX については、DDBJ Search API で DRR を引き、DDBJ 側の状態（public、suppressed、withdrawn）を添えて DRR 単位の一覧にする。

DDBJ の FTP は `ByExp/sra/DRX/DRX976/DRX976617/DRR999990/DRR999990.sra` のように DRX 起点で置かれていて、DRR 名を知るには末端まで降りる必要がある。そこで、FTP では DRX の一覧だけを取り、DRR は Search API でまとめて引く。

```text
$ bash usecases/ncbi_vs_ddbj.sh --help
使い方: ncbi_vs_ddbj.sh [オプション] <出力先>
  DDBJ にある DRX を NCBI カタログと突き合わせ、DDBJ にしか無い DRX の DRR を引く。
    1. DDBJ FTP から DRX 一覧を取る（936ディレクトリ、約16分）→ <出力先>/ddbj_experiments.tsv
    2. NCBI カタログと突き合わせる（約10〜30分）             → <出力先>/ddbj-only.tsv
                                                                <出力先>/ncbi-only.tsv
    3. DDBJ にしか無い DRX の DRR を DDBJ Search API で引く（100件で1リクエスト）
                                                             → <出力先>/ddbj-only-runs.tsv
                                                                <出力先>/ddbj-only-not-found.txt
    4. DDBJ の status ファイル（約260 MB）を1つ取る         → <出力先>/<YYYYMMDD>.dra.status.txt
    5. DRR 単位にまとめ、DDBJ 側の状態を添える              → <出力先>/ddbj-only-drr.tsv
                                                                <出力先>/ncbi-only-drr.tsv
       列は drx・drr・ddbj_status・source。
  中断しても同じコマンドで続きから再開する。
  --delay S   DDBJ へのリクエスト間隔の秒数（1・3・4 へ渡す。既定 1.0）
  --dry-run   実行するコマンドを表示するだけで、何も実行しない
  -h, --help  この説明を出す
```

```bash
cd <repository root>
source .venv/bin/activate
```

```bash
bash usecases/ncbi_vs_ddbj.sh out/ddbj-vs
```

この例では、結果は `out/ddbj-vs/` に出る。

DDBJ へのリクエストは 1 秒ずつ間を空けて送る。手順 1 で約 940 回、手順 3 で DDBJ にしか無い DRX 100 件につき 1 回、手順 4 で 1〜7 回になる。DDBJ は rate limit を公開していないので、`--delay` を 1 秒より短くしないことを勧める。

**重要:** 手順 1 または 3 で失敗したリクエストがあると、その場で止まる。同じコマンドをもう一度実行すると、取得済みの分を飛ばして残りだけを取る。手順 4 の status ファイルも、出力先に既にあれば取り直さない。

結果は次の 2 つである。列はどちらも同じである。

- `ddbj-only-drr.tsv`: DDBJ にあって NCBI に無いもの
- `ncbi-only-drr.tsv`: NCBI にあって DDBJ の FTP に見つからないもの

| 列 | 内容 |
|---|---|
| `drx` | DRX accession |
| `drr` | DRR accession。DRR を特定できなかった行は空 |
| `ddbj_status` | DDBJ の status ファイルにある DRX の状態（`public`、`suppressed`、`withdrawn`）。status ファイルに無ければ `not_in_status` |
| `source` | DRR をどこから得たか |

`source` は次の 3 つである。

| 値 | 意味 |
|---|---|
| `search_api` | DDBJ Search API で DRR を引けた |
| `status_file_only` | Search API が返さなかった。suppressed と withdrawn の DRX は Search API に出てこないので、DRR は分からず空になる |
| `ncbi_parquet` | NCBI のカタログにある DRX。DRR は NCBI のカタログの対応をそのまま使う |

- 1 つの DRX に複数の DRR がつくことがあるので、行は DRR 単位になる。
- `ncbi-only-drr.tsv` が意味を持つのは、手順 1 で DDBJ の DRX を全件取ったときだけである。一部しか取っていないと、取らなかった分がすべて「NCBI にしか無い」に数えられる。

途中の生成物:

- `ddbj_experiments.tsv`: DDBJ の FTP にある DRX の一覧。再開にも使う
- `ddbj-only.tsv`、`ncbi-only.tsv`: DRX 単位の突き合わせ結果（`ddbj-only.tsv` は NCBI に無い理由付き）
- `ddbj-only-drx.txt`: 手順 3 へ渡す DRX の一覧
- `ddbj-only-runs.tsv`、`ddbj-only-not-found.txt`: Search API で引いた DRX と DRR の対応と、引けなかった DRX
- `<YYYYMMDD>.dra.status.txt`: DDBJ の status ファイル

各スクリプトの詳細は [cross_archive/README.md](../cross_archive/README.md) にある。

## ライセンス

MIT License で公開する。全文は [LICENSE](../LICENSE) にある。

Copyright (c) 2026 fmaccha
