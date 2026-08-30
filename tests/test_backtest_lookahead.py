"""パイプライン全体の先読みテスト。

関数単位のテストは、関数と関数の**あいだ**で起きる漏れを捕まえられない。
全体でも同じことをやる。
"""

from __future__ import annotations

import pandas as pd
import pytest

from lookahead_demo.backtest import run_backtest

from conftest import CUTOFF, DELISTED_CODE, build_data, tamper_future_disclosures


def test_backtest_is_not_affected_by_future_price_shock():
    """判断日より未来の価格を変えても、それ以前の取引結果は変わらない。"""
    dates, price_normal, kwargs = build_data(future_shock=False)
    _,     price_shocked, _     = build_data(future_shock=True)

    dates_before = [d for d in dates if d <= CUTOFF]

    normal  = run_backtest(trading_dates=dates_before, price_df=price_normal,  **kwargs)
    shocked = run_backtest(trading_dates=dates_before, price_df=price_shocked, **kwargs)

    # 空振り防止。取引が1件も起きていなければ、この比較は何も見ていない
    assert normal.trade_log, "判断日以前に取引が発生していない。テストが空振りしている"

    assert normal.trade_log == shocked.trade_log
    pd.testing.assert_frame_equal(normal.equity_curve, shocked.equity_curve)


def test_backtest_is_not_affected_by_future_disclosures():
    """判断日より後の開示を壊しても、それ以前の取引結果は変わらない。"""
    dates, price_df, kwargs = build_data()
    dates_before = [d for d in dates if d <= CUTOFF]

    broken = tamper_future_disclosures(kwargs["statements_df"])

    normal = run_backtest(
        trading_dates=dates_before, price_df=price_df, **kwargs
    )
    tampered = run_backtest(
        trading_dates=dates_before,
        price_df=price_df,
        statements_df=broken,
        listed_info_df=kwargs["listed_info_df"],
    )

    assert normal.trade_log
    assert normal.trade_log == tampered.trade_log
    pd.testing.assert_frame_equal(normal.equity_curve, tampered.equity_curve)


def test_backtest_is_not_affected_by_future_listing_snapshots():
    """判断日より後の上場一覧を壊しても、それ以前の母集団は変わらない。"""
    dates, price_df, kwargs = build_data()
    dates_before = [d for d in dates if d <= CUTOFF]

    broken = kwargs["listed_info_df"].copy()
    broken = broken[broken["snapshot_date"] <= CUTOFF]  # 未来のスナップショットを全部消す

    normal = run_backtest(
        trading_dates=dates_before, price_df=price_df, **kwargs
    )
    tampered = run_backtest(
        trading_dates=dates_before,
        price_df=price_df,
        statements_df=kwargs["statements_df"],
        listed_info_df=broken,
    )

    assert normal.trade_log
    assert normal.trade_log == tampered.trade_log


def test_trading_dates_must_be_ascending():
    """並び替えのつもりが順序を壊していた、という事故をその場で止める。"""
    dates, price_df, kwargs = build_data()
    with pytest.raises(AssertionError, match="昇順"):
        run_backtest(
            trading_dates=list(reversed(dates)), price_df=price_df, **kwargs
        )


def test_delisted_code_is_closed_out_not_silently_dropped():
    """消えた銘柄は、消えた時点の値で明示的に打ち切られること。

    黙って集計から外すのが、最も検出しづらい先読みになる。
    """
    dates, price_df, kwargs = build_data()
    result = run_backtest(trading_dates=dates, price_df=price_df, **kwargs)

    delisted_sales = [
        row for row in result.trade_log if row[5] == "delisted"
    ]
    assert delisted_sales, "上場廃止の打ち切りが記録されていない"
    assert all(row[2] == DELISTED_CODE for row in delisted_sales)


def test_undefined_universe_days_are_counted():
    """母集団を定義できない日が、埋められずに数えられていること。"""
    dates, price_df, kwargs = build_data()
    result = run_backtest(trading_dates=dates, price_df=price_df, **kwargs)

    assert result.undefined_universe_days == 1
    # その日も equity は記録される(行が消えない)
    assert len(result.equity_curve) == len(dates)
