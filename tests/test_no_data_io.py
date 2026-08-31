"""ロジック側にデータ I/O を書けないことの静的検査。

検証したいロジックを「渡された DataFrame だけを見る純粋関数」に限定し、
データの読み込みは呼び出し側(tests/conftest.py)に固定する。ロジック側から
未知のファイルを読む経路そのものが無くなるので、先読みの入口が構造的に減る。

そしてその分離を、人の記憶ではなくテストで留める。

**禁止トークンを列挙する方式はやめた。** `read_parquet` / `read_csv` /
`read_json` / `read_sql` の4つを並べても、pandas の読み込み関数は20種類ある。
実測で `read_excel` `read_pickle` `read_hdf` `read_html` `read_table`
`read_feather` ほか計14種が素通りしていた。列挙は必ず遅れる。

代わりに **AST を歩いて「形」で捕まえる**。`read_` で始まる呼び出しは名前を
知らなくても捕まるし、AST は docstring やコメントを見ないので「read_csv と
書いてある説明文」を誤検出することもない。文字列検索だと両方を外す。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# import された時点で「外を読む経路」ができる module(トップレベル名で見る)
FORBIDDEN_IMPORTS = frozenset(
    {
        # ネットワーク
        "requests", "urllib", "urllib3", "httpx", "aiohttp", "http", "socket",
        "ftplib", "telnetlib",
        # データベース
        "sqlite3", "sqlalchemy", "psycopg2", "pymysql", "pyodbc",
        # 直列化・ファイル
        "pickle", "shelve", "dbm", "marshal", "csv", "json", "configparser",
        "io", "os", "pathlib", "glob", "shutil", "tempfile", "zipfile", "tarfile",
        "gzip", "bz2", "lzma", "zlib", "netrc", "mmap", "ctypes",
        "yaml", "toml", "tomllib", "dotenv",
        # 動的読み込み(禁止 module を迂回できてしまう)
        "importlib",
        # ファイル形式のライブラリ
        "pyarrow", "openpyxl", "xlrd", "h5py", "tables",
        # 外部プロセス・リモートストレージ
        "subprocess", "fsspec", "s3fs", "gcsfs", "boto3",
    }
)

# 呼んだ時点で外部に触る組み込み
FORBIDDEN_BUILTINS = frozenset(
    {"open", "eval", "exec", "compile", "input", "__import__"}
)

# 書き出し側。読み込みは接頭辞で捕まるが、書き出しは to_dict / tolist のような
# 純粋な変換と名前が近いので、こちらは明示的に並べる。
FORBIDDEN_WRITERS = frozenset(
    {
        "to_csv", "to_parquet", "to_json", "to_sql", "to_pickle", "to_excel",
        "to_hdf", "to_feather", "to_orc", "to_stata", "to_xml", "to_html",
        "to_latex", "to_clipboard",
        "write_text", "write_bytes", "writelines",
    }
)

# 読み込みは接頭辞で捕まえる。read_parquet も read_excel も read_bytes も同じ形。
READER_PREFIX = "read_"

# 接頭辞に従わない読み込み(numpy / pandas)。**ここだけは列挙になる。**
# 列挙は必ず遅れるので、遅れたことを検出する検査を下に置く
# (test_the_reader_list_has_not_fallen_behind)。
FORBIDDEN_READERS = frozenset(
    {
        "load", "loadtxt", "genfromtxt", "fromfile", "memmap",  # numpy
        "HDFStore", "ExcelFile", "load_workbook",               # pandas / openpyxl
    }
)

# 読み込み系の名前を機械的に見つけるための手がかり。列挙の遅れの検出に使う。
READER_NAME_HINTS = ("load", "fromfile", "hdfstore", "excelfile", "memmap")

SRC = Path(__file__).resolve().parents[1] / "src" / "lookahead_demo"


def _source_files(root: Path = SRC) -> list[Path]:
    """検査対象の .py を集める。

    **rglob(再帰)であること。** `glob("*.py")` は直下しか見ないので、
    サブパッケージを1つ足した瞬間に、その中身が黙って検査対象から外れる。
    """
    return sorted(root.rglob("*.py"))


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def violations(source: str, label: str = "<source>") -> list[str]:
    """データ I/O にあたる記述を列挙する。空なら違反なし。"""
    tree = ast.parse(source, filename=label)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    found.append(f"{label}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            # 相対 import(level > 0)は自分のパッケージ内なので対象外
            top = (node.module or "").split(".")[0]
            if node.level == 0 and top in FORBIDDEN_IMPORTS:
                found.append(f"{label}:{node.lineno} from {node.module} import ...")
        elif isinstance(node, ast.Call):
            name = _called_name(node.func)
            if name is None:
                continue
            if (
                name in FORBIDDEN_BUILTINS
                or name in FORBIDDEN_WRITERS
                or name in FORBIDDEN_READERS
                or name.startswith(READER_PREFIX)
            ):
                found.append(f"{label}:{node.lineno} {name}()")

    return found


def test_source_files_exist():
    """検査対象が0件のまま通る、という空振りを防ぐ。"""
    assert len(_source_files()) >= 5


def test_the_scan_is_recursive(tmp_path: Path):
    """サブパッケージまで届くこと。届かない検査は、足した瞬間に穴になる。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sub" / "nested.py").write_text("y = 2\n", encoding="utf-8")

    names = {p.name for p in _source_files(tmp_path)}
    assert names == {"top.py", "nested.py"}


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_logic_has_no_data_io(path: Path):
    found = violations(path.read_text(encoding="utf-8"), path.name)
    assert not found, "ロジック側にデータI/Oが書かれている: " + " / ".join(found)


# 空振り防止。検査が実際に反応することを、**列挙式では漏れていたものを含めて**
# 1件ずつ確かめる。反応しない検査は無いのと同じ。
VIOLATION_SAMPLES = [
    ("read_csv", "import pandas as pd\ndf = pd.read_csv('prices.csv')\n"),
    ("read_excel", "import pandas as pd\ndf = pd.read_excel('prices.xlsx')\n"),
    ("read_pickle", "import pandas as pd\ndf = pd.read_pickle('cache')\n"),
    ("read_hdf", "import pandas as pd\ndf = pd.read_hdf('store.h5')\n"),
    ("read_feather", "import pandas as pd\ndf = pd.read_feather('x.f')\n"),
    ("read_bytes", "raw = p.read_bytes()\n"),
    ("open", "f = open('prices.csv')\n"),
    ("to_csv", "df.to_csv('out.csv')\n"),
    ("requests", "import requests\n"),
    ("urllib", "import urllib.request\n"),
    ("sqlite3", "import sqlite3\n"),
    ("pickle", "from pickle import loads\n"),
    ("os", "import os\npath = os.environ['DATA']\n"),
    ("pathlib", "from pathlib import Path\n"),
    ("subprocess", "import subprocess\n"),
    # 接頭辞に従わない読み込み(2026-08-31 の再検査で追加)
    ("np.load", "import numpy as np\nd = np.load('cache.npy')\n"),
    ("np.loadtxt", "import numpy as np\nd = np.loadtxt('x.txt')\n"),
    ("np.genfromtxt", "import numpy as np\nd = np.genfromtxt('x.csv')\n"),
    ("np.fromfile", "import numpy as np\nd = np.fromfile('x.bin')\n"),
    ("np.memmap", "import numpy as np\nd = np.memmap('x.bin')\n"),
    ("pd.HDFStore", "import pandas as pd\ns = pd.HDFStore('store.h5')\n"),
    ("pd.ExcelFile", "import pandas as pd\ns = pd.ExcelFile('x.xlsx')\n"),
    ("gzip", "import gzip\n"),
    ("bz2", "import bz2\n"),
    ("lzma", "import lzma\n"),
    ("importlib", "import importlib\n"),
    ("pyarrow", "import pyarrow.parquet as pq\n"),
]


@pytest.mark.parametrize("label,src", VIOLATION_SAMPLES, ids=[s[0] for s in VIOLATION_SAMPLES])
def test_the_check_itself_detects_a_violation(label: str, src: str):
    assert violations(src, label), f"{label} を検出できていない"


def test_the_reader_list_has_not_fallen_behind():
    """**列挙が遅れていないことを、実物の API と突き合わせて確かめる。**

    `read_` 接頭辞に従わない読み込み(`np.load` / `pd.HDFStore` など)は、
    どうしても列挙になる。列挙は必ず遅れる —— なら、**遅れたことを検出する検査**を
    置く。ライブラリを上げて新しい読み込み関数が増えれば、ここが落ちる。

    2026-08-31 の再検査で、まさにこの形の6件が素通りしていた。
    """
    import numpy as np
    import pandas as pd

    missed: list[str] = []
    for mod in (pd, np):
        for name in dir(mod):
            if name.startswith("_") or name.startswith(READER_PREFIX):
                continue
            if name in FORBIDDEN_READERS:
                continue
            if any(hint in name.lower() for hint in READER_NAME_HINTS):
                missed.append(f"{mod.__name__}.{name}")

    assert not missed, (
        "読み込み系の API が FORBIDDEN_READERS から漏れている: "
        + ", ".join(sorted(missed))
    )


def test_the_check_does_not_fire_on_prose():
    """docstring やコメントに書かれた語では落ちないこと。

    文字列検索だとここが誤検出になる。誤検出が続く検査は無視されるようになり、
    結局は見張らなくなるので、ここも検査の一部として置いておく。
    """
    harmless = (
        '"""read_csv も open も、ここでは書かない、という説明の docstring。"""\n'
        "# requests を使わない理由をコメントで書いても落ちないこと\n"
        "import pandas as pd\n"
        "\n"
        "def f(df):\n"
        '    return df.groupby("Code")["close"].std()\n'
    )
    assert violations(harmless, "harmless") == []
