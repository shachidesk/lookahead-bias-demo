"""テストの検出力そのものを試す。

通っている先読みテストは、**素通りしているのか、実際に検出しているのか**が
それだけでは分からない。同じ手続きを、わざと先読みを入れた対照実装に当てて、
そこで**差が出ること**を確認する。ここが落ちたら、先読みテストのほうを疑う。
"""

from __future__ import annotations

import pandas as pd

from lookahead_demo.naive import naive_run_backtest

from conftest import CUTOFF, build_data, tamper_future_disclosures


def _differs(a, b) -> bool:
    if a.trade_log != b.trade_log:
        return True
    return not a.equity_curve.equals(b.equity_curve)


def test_future_price_shock_changes_the_naive_pipeline():
    """全期間ボラティリティで重みを決めていると、未来の価格が過去に届く。"""
    dates, price_normal, kwargs = build_data(future_shock=False)
    _,     price_shocked, _     = build_data(future_shock=True)

    dates_before = [d for d in dates if d <= CUTOFF]

    normal  = naive_run_backtest(dates_before, price_normal,  **kwargs)
    shocked = naive_run_backtest(dates_before, price_shocked, **kwargs)

    assert normal.trade_log, "対照実装でも取引が起きていない。データ側の問題"
    assert _differs(normal, shocked), (
        "対照実装で差が出ない。先読みテストが検出力を持っているとは言えない"
    )


def test_future_disclosure_changes_the_naive_pipeline():
    """時点で絞っていないスコアには、未来の開示が届く。"""
    dates, price_df, kwargs = build_data()
    dates_before = [d for d in dates if d <= CUTOFF]

    broken = tamper_future_disclosures(kwargs["statements_df"])

    normal = naive_run_backtest(dates_before, price_df, **kwargs)
    tampered = naive_run_backtest(
        dates_before,
        price_df,
        statements_df=broken,
        listed_info_df=kwargs["listed_info_df"],
    )

    assert normal.trade_log
    assert _differs(normal, tampered), (
        "対照実装で差が出ない。先読みテストが検出力を持っているとは言えない"
    )


def test_naive_universe_hides_the_delisted_code_from_the_past():
    """生存者バイアスは取引履歴に現れる。対照実装には打ち切りが1件も無い。"""
    dates, price_df, kwargs = build_data()

    naive = naive_run_backtest(dates, price_df, **kwargs)

    assert not [row for row in naive.trade_log if row[5] == "delisted"]


def test_the_fixture_actually_moves_prices_after_the_cutoff():
    """壊し方が効いているかの確認。CUTOFF 以前は同一、以後は違うこと。"""
    _, normal, _ = build_data(future_shock=False)
    _, shocked, _ = build_data(future_shock=True)

    before_n = normal[normal["date"] <= CUTOFF].reset_index(drop=True)
    before_s = shocked[shocked["date"] <= CUTOFF].reset_index(drop=True)
    pd.testing.assert_frame_equal(before_n, before_s)

    after_n = normal[normal["date"] > CUTOFF].reset_index(drop=True)
    after_s = shocked[shocked["date"] > CUTOFF].reset_index(drop=True)
    assert not after_n.equals(after_s)
