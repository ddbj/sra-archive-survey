"""シェルスクリプトのテストで共有する実行環境。"""

import os
import shutil
import sys

SAFE_TOOLS = ("bash", "gzip", "awk", "sort", "comm", "date", "seq", "mkdir",
              "cat", "head", "tail", "wc", "grep", "xargs", "tee", "ls", "rm", "touch",
              "sleep", "dirname", "printf", "mv", "cut", "uniq", "tr")


def path_without_curl(directory):
    """curl だけを持たない PATH を作る。

    テスト中のスクリプトが想定外の経路へ入っても、外部へ要求を出さないようにする。
    """
    bin_dir = os.path.join(directory, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for tool in SAFE_TOOLS:
        found = shutil.which(tool)
        if found and not os.path.exists(os.path.join(bin_dir, tool)):
            os.symlink(found, os.path.join(bin_dir, tool))
    # pyenv の shim は pyenv 自体が PATH に無いと動かないので、実体を直接指す。
    python = os.path.join(bin_dir, "python3")
    if not os.path.exists(python):
        os.symlink(sys.executable, python)
    return bin_dir


FAKE_CURL = r'''
"""ENA Portal API の代わりに fixture を返す curl。count と search だけを真似る。

FAKE_CURL_COUNT を与えると count はその値を返す（本文が途中で切れた状況を作る）。
FAKE_CURL_FAIL_FIRST_COUNT にファイル名を与えると、最初の count だけエラー応答を返す。
"""
import json, os, sys
args = sys.argv[1:]
fixture = os.environ["FAKE_CURL_FIXTURE"]
with open(fixture) as handle:
    body = handle.read()
if any(arg.endswith("/count") for arg in args):
    fail_marker = os.environ.get("FAKE_CURL_FAIL_FIRST_COUNT")
    if fail_marker and not os.path.exists(fail_marker):
        open(fail_marker, "w").close()
        print('{"message":"gateway timeout"}')
        sys.exit(0)
    count = os.environ.get("FAKE_CURL_COUNT", str(body.count("\n") - 1))
    print(json.dumps({"count": count}))
elif any(arg.endswith("/search") for arg in args):
    with open(args[args.index("-o") + 1], "w") as handle:
        handle.write(body)
    print("200", end="")
else:
    sys.exit("fake curl: 想定外の呼び出し " + " ".join(args))
'''


def install_fake_curl(bin_dir, fixture_path, env):
    """path_without_curl の bin に偽の curl を置き、返す本文を env で渡す。"""
    path = os.path.join(bin_dir, "curl")
    with open(path, "w") as handle:
        handle.write("#!" + sys.executable + FAKE_CURL)
    os.chmod(path, 0o755)
    env["FAKE_CURL_FIXTURE"] = fixture_path
