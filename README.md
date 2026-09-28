# sra-archive-survey

sra-archive-survey は、SRA の Run データが NCBI・EBI・DDBJ の三極にどれだけあるかを調べるスクリプト集である。NCBI の公式 Parquet カタログ、ENA Portal API、EBI と DDBJ の FTP を読み、Run 数と総容量、前回からの増分、片方のアーカイブにしか無い Run を出す。

この `README.md` は概要である。使い方は [usecases/README.md](usecases/README.md) に、各スクリプトの詳細はサブディレクトリの `README.md` にある。

## 使い始める

やりたいことに合うコマンドを [usecases/README.md](usecases/README.md) で選ぶ。どのユースケースも `usecases/` のコマンド 1 つで最後まで流れる。

| # | やりたいこと | コマンド |
|---|---|---|
| 1 | NCBI にある SRA の規模を取り、前回との差分を出す（三極合計、または SRR などの一部） | [`ncbi_scale.sh`](usecases/README.md#1-ncbi-の規模を取る) |
| 2 | EBI にある ERR のファイル系統別の規模を取り、前回との差分を出す | [`ebi_scale.sh`](usecases/README.md#2-ebi-の規模を取る) |
| 3 | EBI にある CRAM の規模を取る | [`ebi_cram.sh`](usecases/README.md#3-ebi-の-cram-の規模を取る) |
| 4 | NCBI と EBI で ERR の SRA 形式ファイルの保有を比べる | [`ncbi_vs_ebi.sh`](usecases/README.md#4-ncbi-と-ebi-の保有を比べる) |
| 5 | NCBI と DDBJ で DRR の保有を比べる | [`ncbi_vs_ddbj.sh`](usecases/README.md#5-ncbi-と-ddbj-の保有を比べる) |

## リポジトリ構成

- [usecases/](usecases/)
    - ユースケースごとのラッパー。下の 3 ディレクトリのスクリプトを正しい順番でつなぐ。
- [scale/ncbi/](scale/ncbi/)
    - NCBI の公式 Parquet カタログから snapshot を取り、前回との差分とレポートを出すスクリプト。
- [scale/ebi/](scale/ebi/)
    - ENA Portal API から ERR のファイル系統別・CRAM の規模を取り、前回との差分を出すスクリプト。
- [cross_archive/](cross_archive/)
    - NCBI と EBI・DDBJ の保有を突き合わせるスクリプトと、その結果（[`results/`](cross_archive/results/)）。

## 動作環境

次のコマンドとライブラリを使う。

- Python 3 と DuckDB（NCBI の Parquet カタログを読む）
- bash、curl、gzip、awk、sort などの coreutils（EBI の取得）

DuckDB は venv に入れる。venv はリポジトリのルートに `.venv` として作る（`.gitignore` で除外してある）。

```bash
cd <repository root>
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install duckdb
```

venv は、シェルを開くたびに `source .venv/bin/activate` で有効にする。

外部へは次の宛先に接続する。どれも認証は要らない。

| 宛先 | 使うユースケース | 用途 |
|---|---|---|
| `s3://sra-pub-metadata-us-east-1/sra/metadata/`（AWS の匿名 S3） | 1, 4, 5 | NCBI の公式 Parquet カタログ |
| `extensions.duckdb.org` | 1, 4, 5 | DuckDB の `httpfs` 拡張（初回だけ取得する） |
| `www.ebi.ac.uk`（ENA Portal API） | 2, 3 | ERR のファイルサイズ |
| `ftp.ebi.ac.uk`、`ftp.sra.ebi.ac.uk` | 4 | livelist.gz と SRA 形式ファイルの一覧 |
| `ddbj.nig.ac.jp`（FTP の HTTPS 公開と Search API） | 5 | DRX の一覧、DRX と DRR の対応、status ファイル |

動作を確認した環境は次のとおりである。

| ツール | バージョン |
|---|---|
| Python | 3.14.4 |
| DuckDB | 1.5.2 |
| bash | 5.3.9 |
| curl | 8.21.0 |
| awk | mawk 1.3.4 |
| gzip | 1.14 |
| OS | Debian（Linux 7.1） |

## テストを走らせる

テストは外部へ接続しない。シェルスクリプトのテストは PATH から curl を外して走らせる。ディレクトリごとに実行する。

```bash
cd <repository root>
for d in scale/ncbi scale/ebi cross_archive usecases; do
  python3 -m unittest discover -s "$d" -p 'test_*.py' -t "$d"
done
```

## ライセンス

MIT License で公開する。全文は [LICENSE](LICENSE) にある。

Copyright (c) 2026 fmaccha
