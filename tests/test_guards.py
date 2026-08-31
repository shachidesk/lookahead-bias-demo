"""本体に置いた歯止めが、本当に歯止めとして働くことの検査。

Q02(2026-08-30)の指摘に対する回帰テスト。「歯止めがあること」と
「歯止めが作動すること」は別で、この資産では後者が4か所で崩れていた。

置いている検査は3種類ある。
  1. 壊れた入力を渡して、実際に落ちること(作動の確認)
  2. 落ちる条件が**構造上起こりうる**こと(空振りの確認)
  3. 同じ欠陥が別の場所に入らないよう、**形で横断的に止める**こと
1件直して終わりにしたのが Q02 指摘4の原因なので、3 を必ず添える。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from lookahead_demo.backtest import (
    assert_cash_is_fully_deployed,
    inverse_vol_weights,
    run_backtest,
)
from lookahead_demo.errors import AccountingError
from lookahead_demo.scoring import latest_close_as_of

from conftest import build_data

SRC = Path(__file__).resolve().parents[1] / "src" / "lookahead_demo"


# --------------------------------------------------------------------------
# 指摘1: 既存建玉が目標ウェイトに再調整されず、資本が二重計上される
# --------------------------------------------------------------------------


def test_capital_is_not_double_counted():
    """建て直しのあと、現金が残らないこと。

    重みの合計は 1 なので、純資産を全額配分すれば現金はちょうど 0 になる。
    残るなら配分し切れておらず、負なら純資産を超えて建てている。**同じ根本
    原因(既存建玉を予算から差し引かない)が両方向に振れる**ので、絶対値で見る。
    """
    dates, price_df, kwargs = build_data()
    result = run_backtest(trading_dates=dates, price_df=price_df, **kwargs)

    assert result.trade_log, "取引が1件も起きていない。この検査は空振りしている"

    curve = result.equity_curve
    invested = curve[curve["n_holdings"] > 0]
    assert not invested.empty, "建玉を持った日が1日も無い。この検査は空振りしている"

    ratio = (invested["cash"] / invested["equity"]).abs()
    assert ratio.max() < 1e-9, (
        f"建て直しのあとに現金が残っている(最大 {ratio.max():.4%})。"
        "既存建玉を予算から差し引いていない可能性がある"
    )


def test_the_cash_guard_fires_in_both_directions():
    """歯止めが**両側**を見ていること。片側だけの歯止めは歯止めではない。

    正しい実装では現金は 0 に収まるので、この歯止めはパイプライン経由では
    踏めない。**単体で叩く。** 叩けない歯止めは、片側に戻されても誰も気づかない。
    """
    equity = 1_041_000.0

    # 負に振れた場合(建玉が純資産を超える)
    with pytest.raises(AccountingError, match="現金が残った"):
        assert_cash_is_fully_deployed(-4_625_880.0, equity, "2025-02-01")

    # 🔴 正に振れた場合(純資産を配分し切れていない)。実測で出たのはこちら
    with pytest.raises(AccountingError, match="現金が残った"):
        assert_cash_is_fully_deployed(201_118.60, equity, "2025-07-01")

    # 丸め誤差では落ちないこと(誤検出が続く歯止めは無視されるようになる)
    assert_cash_is_fully_deployed(-2.037e-10, equity, "2025-07-01")
    assert_cash_is_fully_deployed(0.0, equity, "2025-07-01")


def _swap_dataset():
    """採用銘柄が入れ替わり、かつ既存建玉が値上がりするデータ。

    二重計上があると、この形で現金が大きく負に落ちる(Q02 の実測で
    cash -4,625,880 / 純資産の1.89倍)。
    """
    dates = [
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-02-01"),
        pd.Timestamp("2025-03-01"),
    ]
    prices = []
    for d in dates:
        # A は 2月に10倍。既存建玉の価値が跳ねる
        a_close = 1000.0 if d < pd.Timestamp("2025-02-01") else 10000.0
        prices.append({"date": d, "Code": "A", "close": a_close})
        prices.append({"date": d, "Code": "B", "close": 2000.0})
        prices.append({"date": d, "Code": "C", "close": 500.0})

    statements = []
    for code, (prev, latest) in {
        "A": (10.0, 90.0),
        "B": (10.0, 20.0),
        "C": (10.0, 15.0),
    }.items():
        statements.append(
            {
                "Code": code,
                "PeriodEnd": pd.Timestamp("2023-03-31"),
                "DiscDate": pd.Timestamp("2024-05-10"),
                "CurPerType": "1Q",
                "EPS": prev,
            }
        )
        statements.append(
            {
                "Code": code,
                "PeriodEnd": pd.Timestamp("2024-03-31"),
                "DiscDate": pd.Timestamp("2024-12-10"),
                "CurPerType": "1Q",
                "EPS": latest,
            }
        )
    # 2025-01-15 開示。2月の判断日から C が B を押しのける
    statements.append(
        {
            "Code": "C",
            "PeriodEnd": pd.Timestamp("2024-09-30"),
            "DiscDate": pd.Timestamp("2025-01-15"),
            "CurPerType": "1Q",
            "EPS": 500.0,
        }
    )

    listed = [
        {"snapshot_date": d, "Code": c} for d in dates for c in ("A", "B", "C")
    ]
    return (
        dates,
        pd.DataFrame(prices),
        pd.DataFrame(statements),
        pd.DataFrame(listed),
    )


def test_cash_never_goes_negative_when_targets_change():
    """採用銘柄が入れ替わっても、現金が負に落ちないこと。

    負になるということは、借入の記録も無いまま暗黙のレバレッジが立っている
    ということ。ここが落ちたら、まず建玉の再調整を疑う。
    """
    dates, price_df, statements_df, listed_info_df = _swap_dataset()
    result = run_backtest(
        trading_dates=dates,
        price_df=price_df,
        statements_df=statements_df,
        listed_info_df=listed_info_df,
    )

    curve = result.equity_curve
    # 空振り防止。入れ替えが実際に起きていること
    sells = [row for row in result.trade_log if row[1] == "SELL"]
    assert sells, "入れ替えが1件も起きていない。この検査は空振りしている"

    # 丸め誤差ぶんは許す。**素の >= 0.0 で書くと、たまたま丸い数字のデータでしか
    # 通らない。** 既定データでは実測で cash 最小値が -2.037e-10 になる。
    tol = 1e-9 * float(curve["equity"].abs().max())
    assert float(curve["cash"].min()) >= -tol, (
        f"現金が負になった({curve['cash'].min():,.2f})"
    )
    leverage = (curve["equity"] - curve["cash"]) / curve["equity"]
    assert float(leverage.max()) <= 1.0 + 1e-9, (
        f"建玉が純資産を超えている(最大 {leverage.max():.3f}倍)"
    )


# --------------------------------------------------------------------------
# 指摘2: 等ウェイトに倒すと書いてあるのに、等ウェイトにならない
# --------------------------------------------------------------------------


def test_equal_weight_fallback_is_actually_equal():
    """定義できない銘柄があれば、**全銘柄**が等ウェイトになること。

    定義できない銘柄にだけ生の 1.0 を入れると、他銘柄の 1/v と尺度が違う。
    修正前は片方が NaN のとき {A: 0.0099, B: 0.9901} になっていた。
    """
    vol = pd.Series({"B": 0.01})  # A は定義できない
    weights, undefined = inverse_vol_weights(vol, ["A", "B"])

    assert undefined == ["A"]
    assert weights == {"A": 0.5, "B": 0.5}


def test_inverse_vol_weights_uses_inverse_vol_when_all_defined():
    """空振り防止。全部定義できるときは、ちゃんと逆ボラで重み付けすること。

    常に等ウェイトを返す実装でも上のテストは通ってしまう。
    """
    vol = pd.Series({"A": 0.01, "B": 0.03})
    weights, undefined = inverse_vol_weights(vol, ["A", "B"])

    assert undefined == []
    assert weights["A"] == pytest.approx(0.75)
    assert weights["B"] == pytest.approx(0.25)


def test_zero_vol_is_treated_as_undefined():
    """ボラティリティ 0 は 1/v が発散する。NaN と同じく定義できない扱い。"""
    vol = pd.Series({"A": 0.0, "B": 0.02})
    weights, undefined = inverse_vol_weights(vol, ["A", "B"])

    assert undefined == ["A"]
    assert weights == {"A": 0.5, "B": 0.5}


# --------------------------------------------------------------------------
# 指摘3: 本体の歯止めが素の assert で、python -O では消える
# --------------------------------------------------------------------------

# 子プロセスで歯止めを2つ叩き、落ちたものの名前を出す。
_GUARD_PROBE = """
import pandas as pd
from lookahead_demo.backtest import run_backtest
from lookahead_demo.calibration import assert_no_unmatured

fired = []

# 満了していない行を混ぜる
used = pd.DataFrame([{"date": pd.Timestamp("2025-06-01"), "outcome": 1.0}])
try:
    assert_no_unmatured(used, pd.Timestamp("2025-07-01"), 3)
except AssertionError:
    fired.append("calibration")

# 時系列を降順で渡す
try:
    run_backtest(
        trading_dates=[pd.Timestamp("2025-02-01"), pd.Timestamp("2025-01-01")],
        price_df=pd.DataFrame(columns=["date", "Code", "close"]),
        statements_df=pd.DataFrame(columns=["Code", "DiscDate", "CurPerType", "EPS"]),
        listed_info_df=pd.DataFrame(columns=["snapshot_date", "Code"]),
    )
except AssertionError:
    fired.append("ascending")

print(",".join(sorted(fired)))
"""


@pytest.mark.parametrize("flags", [[], ["-O"]], ids=["normal", "optimized"])
def test_guards_fire_even_under_optimization(flags: list[str]):
    """`python -O` でも歯止めが作動すること。

    素の assert は -O / PYTHONOPTIMIZE で**消える**。テストも変異チェックも
    -O では回さないので、消えたことに気づく経路が無い。フラグ無しの側も
    一緒に回して、この検査自体が空振りしていないことを見る。
    """
    proc = subprocess.run(
        [sys.executable, *flags, "-c", _GUARD_PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ascending,calibration", (
        f"-O 付き({flags})で歯止めが消えている: {proc.stdout!r}"
    )


def test_src_has_no_bare_assert():
    """本体(src/)に素の assert を1件も置かないこと。

    **これが横断チェック。** 指摘3を1件ずつ直しても、次に書いた歯止めがまた
    assert になれば同じ穴が空く。個別の箇所ではなく「形」で止める。
    テストの中の assert は pytest が書き換えるので対象外(ここは src/ だけ見る)。
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "本体に素の assert がある(python -O で消える)。"
        "errors.py の例外を raise すること: " + ", ".join(offenders)
    )


# --------------------------------------------------------------------------
# 指摘9: 母集団には鮮度の上限があるのに、価格には無い
# --------------------------------------------------------------------------


def test_stale_close_is_dropped_not_reused():
    """古すぎる終値は返さないこと。0 や直近値で埋めない。"""
    price_df = pd.DataFrame(
        [
            {"date": pd.Timestamp("2025-01-01"), "Code": "OLD", "close": 100.0},
            {"date": pd.Timestamp("2025-06-01"), "Code": "NEW", "close": 200.0},
        ]
    )
    d = pd.Timestamp("2025-07-01")  # OLD は181日前、NEW は30日前

    fresh = latest_close_as_of(price_df, d, ["OLD", "NEW"])
    assert list(fresh.index) == ["NEW"]

    # 空振り防止。上限を外せば OLD も返る = 落ちたのは鮮度が理由だと言える
    unlimited = latest_close_as_of(
        price_df, d, ["OLD", "NEW"], max_staleness_days=None
    )
    assert set(unlimited.index) == {"OLD", "NEW"}


def test_max_staleness_days_reaches_the_price_side():
    """`max_staleness_days` が母集団だけでなく**価格にも**掛かること。

    片方にだけ届くと、母集団側が明示的に拒んだ古さの銘柄が価格側から素通りする。
    しかも **引数が届いていないことは結果が同じになるので気づけない** ——
    実測では 7日を指定しても価格側は既定の45日で動いており、**31日前の終値が
    その日の約定値として使われていた**。
    """
    dates, price_df, statements_df, listed_info_df = _stale_price_dataset()
    args = dict(
        price_df=price_df,
        statements_df=statements_df,
        listed_info_df=listed_info_df,
    )

    strict = run_backtest(trading_dates=dates, max_staleness_days=7, **args)
    loose = run_backtest(trading_dates=dates, max_staleness_days=45, **args)

    # 空振り防止。緩いほうでは実際に古い終値が使われていること
    assert any(row[2] == "S" for row in loose.trade_log), (
        "45日ポリシーでも古い銘柄が約定していない。データ側の問題"
    )

    assert strict.dropped_by_restriction > loose.dropped_by_restriction, (
        "鮮度を厳しくしても落ちた件数が増えない。"
        f"引数が価格側に届いていない(7日={strict.dropped_by_restriction} / "
        f"45日={loose.dropped_by_restriction})"
    )


def _stale_price_dataset():
    """母集団には残り続けるのに、価格が初回で止まる銘柄 S を含むデータ。"""
    dates = list(pd.date_range("2025-01-01", periods=5, freq="MS"))
    prices = []
    for d in dates:
        prices.append({"date": d, "Code": "A", "close": 1000.0})
        prices.append({"date": d, "Code": "B", "close": 2000.0})
    # S の価格は初回で止まる。母集団には残り続ける
    prices.append({"date": dates[0], "Code": "S", "close": 500.0})

    statements = []
    for code, (prev, latest) in {
        "A": (10.0, 30.0),
        "B": (10.0, 20.0),
        "S": (10.0, 50.0),
    }.items():
        statements.append(
            {
                "Code": code,
                "PeriodEnd": pd.Timestamp("2023-03-31"),
                "DiscDate": pd.Timestamp("2024-05-10"),
                "CurPerType": "1Q",
                "EPS": prev,
            }
        )
        statements.append(
            {
                "Code": code,
                "PeriodEnd": pd.Timestamp("2024-03-31"),
                "DiscDate": pd.Timestamp("2024-12-10"),
                "CurPerType": "1Q",
                "EPS": latest,
            }
        )

    listed = [
        {"snapshot_date": d, "Code": c} for d in dates for c in ("A", "B", "S")
    ]
    return (
        dates,
        pd.DataFrame(prices),
        pd.DataFrame(statements),
        pd.DataFrame(listed),
    )


def test_stale_price_is_reported_as_dropped():
    """母集団に残っているのに価格が古い銘柄が、落ちた件数として数えられること。

    黙って直近値で埋めると、その銘柄は「価格がある」と見なされて約定にも
    評価にも使われ、restrict_with_count からは 0件落ちと報告される。
    """
    dates, price_df, statements_df, listed_info_df = _stale_price_dataset()

    result = run_backtest(
        trading_dates=dates,
        price_df=price_df,
        statements_df=statements_df,
        listed_info_df=listed_info_df,
    )

    assert result.dropped_by_restriction > 0, (
        "価格が5か月止まっている銘柄が、1件も落ちたと報告されていない"
    )
