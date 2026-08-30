"""先読みテストの検出力を確かめる。

通っているテストは、それだけでは**素通りしているのか、実際に見張っているのか**
が分からない。そこで正しい実装をわざと壊し(変異)、テストが**落ちること**を
確認する。1件でも素通りしたら、その経路は実質ノーガードになっている。

    python tools/mutation_check.py

終了コード 0 = 全変異を検出。1 = 素通り(または壊せなかったもの)あり。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (対象ファイル, 置換前, 置換後, 何を壊しているか)
MUTANTS: list[tuple[str, str, str, str]] = [
    (
        "src/lookahead_demo/scoring.py",
        "        (statements_df[DISCLOSURE_DATE_COL] <= as_of_date)\n",
        '        (statements_df[DISCLOSURE_DATE_COL] <= pd.Timestamp("2099-01-01"))\n',
        "経路A: 開示日フィルタを外す",
    ),
    (
        "src/lookahead_demo/scoring.py",
        "    df = price_df[\n"
        '        (price_df["date"] <= as_of_date) & (price_df["Code"].isin(codes))\n'
        "    ]\n"
        '    return df.groupby("Code")["close"].std()',
        '    df = price_df[price_df["Code"].isin(codes)]\n'
        '    return df.groupby("Code")["close"].std()',
        "経路D: ボラティリティを全期間から計算する",
    ),
    (
        "src/lookahead_demo/universe.py",
        "        available = [s for s in snapshots if s <= d]\n",
        "        available = list(snapshots)\n",
        "経路C: 未来のスナップショットも母集団に使う",
    ),
    (
        "src/lookahead_demo/universe.py",
        "        if (d - latest).days > max_staleness_days:\n            continue",
        "        if False:\n            continue",
        "鮮度チェックを外す(古い母集団を黙って使う)",
    ),
    (
        "src/lookahead_demo/calibration.py",
        "    eligible = set(matured_by(all_rebalance, d, max_horizon_months))\n"
        '    used = records[records["date"].isin(eligible)]',
        '    used = records[records["date"] <= d]',
        "経路D: 満了していない期間も混ぜる",
    ),
    (
        "src/lookahead_demo/backtest.py",
        '    assert trading_dates == sorted(trading_dates), "trading_dates が昇順でない"\n',
        "",
        "時系列の昇順チェックを外す",
    ),
]

CRLF = "\r\n"
LF = "\n"


def mutate_bytes(raw: bytes, old: str, new: str) -> bytes | None:
    """改行コードに依存せず置換する。元の改行スタイルは保つ。

    ここをテキストモードの read_text / write_text でやると、Windows では
    書き戻しの時点で改行が CRLF に変わる。復元したつもりが全行変更になるうえ、
    置換パターン(改行を含む)が次から一致しなくなり、変異が黙って素通りする。
    実際に一度そうなった。だからバイト列で扱い、比較の時だけ正規化する。
    """
    text = raw.decode("utf-8")
    had_crlf = CRLF in text
    normalized = text.replace(CRLF, LF)
    if old not in normalized:
        return None
    mutated = normalized.replace(old, new)
    if had_crlf:
        mutated = mutated.replace(LF, CRLF)
    return mutated.encode("utf-8")


def run_tests(python: str) -> tuple[bool, str]:
    """テストを走らせ、(落ちたか, 最終行) を返す。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [python, "-m", "pytest", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    return proc.returncode != 0, (lines[-1] if lines else "(出力なし)")


def main() -> int:
    python = sys.executable

    # 変異を入れる前に、素の状態で通ることを確かめる。
    failed, summary = run_tests(python)
    if failed:
        print(f"変異を入れる前からテストが落ちている: {summary}")
        return 1

    survivors: list[str] = []
    for rel, old, new, label in MUTANTS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = mutate_bytes(original, old, new)

        if mutated is None:
            # 置換できないのは「壊せていない」ということ。素通りと同じ扱いにする。
            # ここを SKIP として見逃すと、検出力ゼロのまま緑になる。
            print(f"壊せず NG | {label}(置換パターンが一致しない)")
            survivors.append(label)
            continue

        path.write_bytes(mutated)
        try:
            detected, summary = run_tests(python)
        finally:
            path.write_bytes(original)  # 必ずバイト単位で戻す

        print(f"{'検出 OK  ' if detected else '素通り NG'} | {label} -> {summary}")
        if not detected:
            survivors.append(label)

    print()
    print(f"変異 {len(MUTANTS)} 件中 検出 {len(MUTANTS) - len(survivors)} 件")
    if survivors:
        print("検出できなかった変異:")
        for label in survivors:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
