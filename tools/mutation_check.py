"""先読みテストの検出力を確かめる。

通っているテストは、それだけでは**素通りしているのか、実際に見張っているのか**
が分からない。そこで正しい実装をわざと壊し(変異)、テストが**落ちること**を
確認する。1件でも素通りしたら、その経路は実質ノーガードになっている。

    python tools/mutation_check.py

終了コード 0 = 全変異を検出。1 = 素通り(または壊せなかったもの)あり。

**「落ちた」の判定は粗くしない。** returncode != 0 を検出と数えると、pytest が
収集エラー(2)や使用法エラー(4)で終わっただけの変異が「検出 OK」になる。
構文を壊しただけの変異が、先読みテストを1件も落とさずに緑になってしまい、
変異を足すほど検出力ゼロのまま件数だけが伸びる。ここでは
  - 終了コードが「テスト失敗(1)」であること
  - かつ、**狙ったテストファイルが実際に落ちていること**
の両方を見る。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# pytest の終了コード。0/1 以外は「テストが失敗した」ではない。
PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1
PYTEST_EXIT_MEANING = {
    2: "実行中断(収集エラーなど)",
    3: "内部エラー",
    4: "使用法エラー",
    5: "テストが1件も収集されなかった",
}

# (対象ファイル, 置換前, 置換後, 何を壊しているか, 落ちるべきテストファイル)
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "src/lookahead_demo/scoring.py",
        "        (statements_df[DISCLOSURE_DATE_COL] <= as_of_date)\n",
        '        (statements_df[DISCLOSURE_DATE_COL] <= pd.Timestamp("2099-01-01"))\n',
        "経路A: 開示日フィルタを外す",
        "tests/test_scoring.py",
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
        "tests/test_backtest_lookahead.py",
    ),
    (
        "src/lookahead_demo/universe.py",
        "        available = [s for s in snapshots if s <= d]\n",
        "        available = list(snapshots)\n",
        "経路C: 未来のスナップショットも母集団に使う",
        "tests/test_universe.py",
    ),
    (
        "src/lookahead_demo/universe.py",
        "        if (d - latest).days > max_staleness_days:\n            continue",
        "        if False:\n            continue",
        "鮮度チェックを外す(古い母集団を黙って使う)",
        "tests/test_universe.py",
    ),
    (
        "src/lookahead_demo/calibration.py",
        "    eligible = set(matured_by(all_rebalance, d, max_horizon_months))\n"
        '    used = records[records["date"].isin(eligible)]',
        '    used = records[records["date"] <= d]',
        "経路D: 満了していない期間も混ぜる",
        "tests/test_calibration.py",
    ),
    (
        "src/lookahead_demo/backtest.py",
        "    if trading_dates != sorted(trading_dates):\n"
        '        raise LookaheadError("trading_dates が昇順でない")\n',
        "",
        "時系列の昇順チェックを外す",
        "tests/test_backtest_lookahead.py",
    ),
    # --- ここから Q02(2026-08-30)の指摘に対して足した変異 ---
    (
        "src/lookahead_demo/backtest.py",
        "                target_shares = equity * weights[code] / px\n"
        "                delta = target_shares - holdings.get(code, 0.0)\n"
        "                holdings[code] = target_shares\n",
        "                if code in holdings:\n"
        "                    continue\n"
        "                target_shares = equity * weights[code] / px\n"
        "                delta = target_shares\n"
        "                holdings[code] = target_shares\n",
        "指摘1: 既存建玉を再調整せず、資本を二重計上する",
        "tests/test_guards.py",
    ),
    (
        "src/lookahead_demo/backtest.py",
        "    if undefined:\n"
        "        equal = 1.0 / len(codes)\n"
        "        return {code: equal for code in codes}, undefined\n",
        "    for code in undefined:\n        inv[code] = 1.0\n",
        "指摘2: 定義できない銘柄にだけ生の 1.0 を混ぜる",
        "tests/test_guards.py",
    ),
    (
        "src/lookahead_demo/calibration.py",
        "    if latest > d:\n"
        "        raise LookaheadError(\n"
        '            f"calibration に未満了(将来)の日が混入している: "\n'
        '            f"満了 {latest.date()} > 判断日 {d.date()}"\n'
        "        )\n",
        "    assert latest <= d, (\n"
        '        f"calibration に未満了(将来)の日が混入している: "\n'
        '        f"満了 {latest.date()} > 判断日 {d.date()}"\n'
        "    )\n",
        "指摘3: 歯止めを素の assert に戻す(-O で消える)",
        "tests/test_guards.py",
    ),
    (
        "src/lookahead_demo/scoring.py",
        "    if max_staleness_days is not None:\n"
        '        staleness = (as_of_date - latest["date"]).dt.days\n'
        "        latest = latest[staleness <= max_staleness_days]\n"
        "        if latest.empty:\n"
        '            return pd.Series(dtype="float64")\n',
        "    if False:\n        pass\n",
        "指摘9: 価格の鮮度上限を外す(古い終値を黙って使う)",
        "tests/test_guards.py",
    ),
    (
        "src/lookahead_demo/backtest.py",
        "            if code in last.index:\n"
        "                cash += holdings[code] * float(last[code])\n"
        "                trade_log.append(\n"
        '                    (day, "SELL", code, holdings[code], float(last[code]), "delisted")\n'
        "                )\n"
        "            del holdings[code]\n",
        "            del holdings[code]\n",
        "指摘4の対偶: 消えた銘柄を打ち切らず黙って落とす",
        "tests/test_backtest_lookahead.py",
    ),
    # --- ここから 2026-08-31 の再検査(/code-review high)の指摘に対して足した変異 ---
    (
        'src/lookahead_demo/backtest.py',
        '    if abs(cash) > CASH_TOLERANCE_RATIO * max(abs(equity), 1.0):\n',
        '    if cash < -CASH_TOLERANCE_RATIO * max(abs(equity), 1.0):\n',
        '再検査1: 現金の歯止めを片側(負)だけに戻す',
        'tests/test_guards.py',
    ),
    (
        'src/lookahead_demo/backtest.py',
        '        prices = latest_close_as_of(\n            price_df, d, listed, max_staleness_days=max_staleness_days\n        )\n',
        '        prices = latest_close_as_of(price_df, d, listed)\n',
        '再検査2: max_staleness_days を価格側に渡さない',
        'tests/test_guards.py',
    ),
    (
        'tests/test_no_data_io.py',
        '        "load", "loadtxt", "genfromtxt", "fromfile", "memmap",  # numpy\n        "HDFStore", "ExcelFile", "load_workbook",               # pandas / openpyxl\n',
        '',
        '再検査3: 接頭辞に従わない読み込みの列挙を空にする',
        'tests/test_no_data_io.py',
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


def run_tests(python: str) -> tuple[int, str, str]:
    """テストを走らせ、(終了コード, 最終行, 標準出力の全文) を返す。

    「落ちたか」の bool には**しない**。pytest の終了コードは 0/1 以外にも
    2〜5 があり、それらは「テストが失敗した」ではないため、呼び出し側で
    種別まで見られるようにしておく。
    """
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
    return proc.returncode, (lines[-1] if lines else "(出力なし)"), proc.stdout


def failed_test_files(stdout: str) -> set[str]:
    """pytest の短いサマリから、落ちたテストファイルを取り出す。"""
    files: set[str] = set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith(("FAILED ", "ERROR ")):
            continue
        nodeid = line.split(None, 1)[1]
        files.add(nodeid.split("::")[0].replace("\\", "/"))
    return files


def main() -> int:
    python = sys.executable

    # 変異を入れる前に、素の状態で通ることを確かめる。
    code, summary, _ = run_tests(python)
    if code != PYTEST_OK:
        print(f"変異を入れる前からテストが通っていない(exit {code}): {summary}")
        return 1

    survivors: list[str] = []
    for rel, old, new, label, expected in MUTANTS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = mutate_bytes(original, old, new)

        if mutated is None:
            # 置換できないのは「壊せていない」ということ。素通りと同じ扱いにする。
            # ここを SKIP として見逃すと、検出力ゼロのまま緑になる。
            print(f"壊せず NG   | {label}(置換パターンが一致しない)")
            survivors.append(f"{label}(置換パターンが一致しない)")
            continue

        path.write_bytes(mutated)
        try:
            code, summary, stdout = run_tests(python)
        finally:
            path.write_bytes(original)  # 必ずバイト単位で戻す

        if code == PYTEST_OK:
            print(f"素通り NG   | {label} -> {summary}")
            survivors.append(label)
            continue

        if code != PYTEST_TESTS_FAILED:
            # 「落ちた」ように見えるが、テストは1件も失敗していない。
            meaning = PYTEST_EXIT_MEANING.get(code, "不明")
            print(f"壊れ方が違う NG | {label} -> pytest exit {code}({meaning})")
            survivors.append(f"{label}(pytest exit {code}: {meaning})")
            continue

        failed = failed_test_files(stdout)
        if expected not in failed:
            # 何かは落ちたが、見張っているはずのテストではない。
            print(
                f"別のが落ちた NG | {label} -> {expected} は通ったまま "
                f"(落ちたのは {', '.join(sorted(failed)) or '不明'})"
            )
            survivors.append(f"{label}({expected} が落ちていない)")
            continue

        print(f"検出 OK     | {label} -> {expected} が落ちた")

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
