"""ロジック側にデータ I/O を書けないことの静的検査。

検証したいロジックを「渡された DataFrame だけを見る純粋関数」に限定し、
データの読み込みは呼び出し側(tests/conftest.py)に固定する。ロジック側から
未知のファイルを読む経路そのものが無くなるので、先読みの入口が構造的に減る。

そしてその分離を、人の記憶ではなくテストで留める。
"""

from __future__ import annotations

from pathlib import Path

import pytest

FORBIDDEN = (
    "read_parquet",
    "read_csv",
    "read_json",
    "read_sql",
    "requests",
    "urllib",
    "open(",
    "Path(",
)

SRC = Path(__file__).resolve().parents[1] / "src" / "lookahead_demo"


def _source_files() -> list[Path]:
    return sorted(SRC.glob("*.py"))


def test_source_files_exist():
    """検査対象が0件のまま通る、という空振りを防ぐ。"""
    assert len(_source_files()) >= 5


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_logic_has_no_data_io(path: Path):
    src = path.read_text(encoding="utf-8")
    for token in FORBIDDEN:
        assert token not in src, f"ロジック側にデータI/Oが書かれている: {token}"


def test_the_check_itself_detects_a_violation(tmp_path: Path):
    """検査が実際に反応すること。反応しない検査は無いのと同じ。"""
    offender = tmp_path / "bad.py"
    offender.write_text("df = pd.read_csv('prices.csv')\n", encoding="utf-8")

    src = offender.read_text(encoding="utf-8")
    hits = [token for token in FORBIDDEN if token in src]
    assert "read_csv" in hits
