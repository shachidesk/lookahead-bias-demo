"""README が書いている数字を、実物と突き合わせる。

README は「clone して回せばこう出る」という形で数字を2つ載せている
(テスト件数と、変異の検出件数)。**この2つは実行結果の主張であって、
散文ではない。** それなのに、どの検査もここを見ていなかった。

実際にずれた: テストは 37 → 64 → 79 → 86 と増えたのに、README の
出力例は初版の `37 passed` のまま公開直前まで残っていた。読者が
最初に叩くコマンドの出力が、書いてある数字と合わない状態になる。

このリポジトリの主題は「通っていることは、見張っている証拠にならない」
だが、同じ話が README の側にもある —— **書いてある数字は、確かめた
証拠にならない。** 確かめる側をここに置く。

🔴 空振りさせない。README から数字を取り出せなかった場合は、
「一致した」ではなく**失敗**にする。取り出せないなら比較していない。
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _load_mutants() -> list:
    """tools/mutation_check.py の MUTANTS を読む。

    tools/ は pythonpath に入っていないのでファイル指定で読み込む。
    モジュールは __main__ ガードつきなので、import しても実行されない。
    """
    path = ROOT / "tools" / "mutation_check.py"
    spec = importlib.util.spec_from_file_location("_mutation_check", path)
    assert spec is not None and spec.loader is not None, f"読み込めない: {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MUTANTS


def test_readme_pytest_count_matches_collected() -> None:
    """README の `N passed` が、実際に収集されるテスト件数と一致すること。"""
    m = re.search(r"^(\d+) passed$", _readme_text(), re.MULTILINE)
    assert m is not None, (
        "README から `N passed` を取り出せなかった。"
        "出力例の書き方を変えたなら、この検査も一緒に直す"
    )
    claimed = int(m.group(1))

    # --collect-only は本体を実行しないので、この検査自身が再帰することはない。
    #
    # 🔴 `-o addopts=` で pyproject の addopts を打ち消してから呼ぶ。
    # addopts には既に `-q` が入っており、ここで `-q` を重ねると `-qq` に
    # なって「N tests collected」の行そのものが出力から消える(実測)。
    # 設定ファイル側の値に出力形式が左右される状態にしない。
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-o", "addopts="],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"収集に失敗した:\n{proc.stdout}\n{proc.stderr}"

    c = re.search(r"(\d+) tests? collected", proc.stdout)
    assert c is not None, f"収集件数を読み取れなかった:\n{proc.stdout[-2000:]}"
    actual = int(c.group(1))

    assert claimed == actual, (
        f"README は {claimed} passed と書いているが、実際に収集されるのは "
        f"{actual} 件。テストを増減したら README:「動かす」の出力例も直す"
    )


def test_readme_mutation_count_matches_mutants() -> None:
    """README の `変異 N 件中 検出 M 件` が、変異定義の件数と一致すること。

    N は MUTANTS の実数と一致していなければならない。M は「全件検出」を
    主張しているので N と等しくなければならない —— 検出できていない変異が
    あるなら、それは README に書くことではなく直すことである。
    """
    m = re.search(r"変異\s*(\d+)\s*件中\s*検出\s*(\d+)\s*件", _readme_text())
    assert m is not None, (
        "README から `変異 N 件中 検出 M 件` を取り出せなかった。"
        "書き方を変えたなら、この検査も一緒に直す"
    )
    claimed_total, claimed_detected = int(m.group(1)), int(m.group(2))
    actual_total = len(_load_mutants())

    assert claimed_total == actual_total, (
        f"README は変異 {claimed_total} 件と書いているが、"
        f"tools/mutation_check.py の MUTANTS は {actual_total} 件"
    )
    assert claimed_detected == claimed_total, (
        f"README が全件検出を主張していない({claimed_detected}/{claimed_total})。"
        "素通りする変異があるなら README を直すのではなく変異を潰す"
    )


@pytest.mark.parametrize(
    "pattern",
    [
        r"^(\d+) passed$",
        r"変異\s*(\d+)\s*件中\s*検出\s*(\d+)\s*件",
    ],
)
def test_patterns_actually_match_readme(pattern: str) -> None:
    """陽性対照 —— 上の2件が「取り出せたうえで一致した」ことを担保する。

    README の書き方が変わってパターンが当たらなくなると、上の2件は
    assert の文言つきで落ちる設計だが、その設計自体がいつか壊れうる。
    パターンが README に当たること自体を、独立した1件として数える。
    """
    assert re.search(pattern, _readme_text(), re.MULTILINE) is not None, (
        f"パターン {pattern!r} が README に当たらない"
    )
