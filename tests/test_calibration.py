"""経路D(全期間からしきい値を決める)のテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from lookahead_demo.calibration import (
    assert_no_unmatured,
    calibration_for,
    calibration_series,
    summarize,
)
from lookahead_demo.naive import naive_calibration

MAX_HORIZON_MONTHS = 3


def _records(rebalance_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """各リバランス日に結果が1件ずつ出る、という形の生レコード。"""
    return pd.DataFrame(
        [
            {"date": d, "outcome": float(i)}
            for i, d in enumerate(rebalance_dates)
        ]
    )


def _rebalance_dates() -> list[pd.Timestamp]:
    return list(pd.date_range("2025-01-01", periods=12, freq="MS"))


def test_only_matured_dates_are_used():
    """保有期間が満了していない日は、まだ結果が出ていないので使えない。"""
    dates = _rebalance_dates()
    records = _records(dates)
    d = pd.Timestamp("2025-07-01")

    result = calibration_for(d, dates, records, MAX_HORIZON_MONTHS)

    # 2025-01 〜 2025-04 の4件だけが 3か月後 <= 2025-07-01 を満たす
    assert result["n"] == 4
    assert result["mean"] == pytest.approx((0 + 1 + 2 + 3) / 4)


def test_naive_calibration_uses_the_whole_period():
    """対照実装は全期間を見る。n が全件になることで差が出る。"""
    dates = _rebalance_dates()
    records = _records(dates)

    result = naive_calibration(dates, records)

    assert result["n"] == 12
    assert result["n"] != calibration_for(
        pd.Timestamp("2025-07-01"), dates, records, MAX_HORIZON_MONTHS
    )["n"]


def test_calibration_is_unchanged_by_breaking_the_future():
    """一般形: d より後のレコードを壊しても、d のキャリブレーションは変わらない。"""
    dates = _rebalance_dates()
    records = _records(dates)
    d = pd.Timestamp("2025-07-01")

    before = calibration_for(d, dates, records, MAX_HORIZON_MONTHS)

    broken = records.copy()
    broken.loc[broken["date"] > d, "outcome"] = -999999.0

    after = calibration_for(d, dates, records=broken, max_horizon_months=MAX_HORIZON_MONTHS)
    assert before == after


def test_naive_calibration_is_detected_by_the_same_check():
    """同じ操作で対照実装は動く。検査が空振りしていないことの確認。"""
    dates = _rebalance_dates()
    records = _records(dates)
    d = pd.Timestamp("2025-07-01")

    before = naive_calibration(dates, records)
    broken = records.copy()
    broken.loc[broken["date"] > d, "outcome"] = -999999.0
    after = naive_calibration(dates, broken)

    assert before["mean"] != after["mean"]


def test_series_matches_the_linear_time_equivalent():
    """素直な O(n^2) と、行フィルタで済ませる O(n) が行単位で一致すること。

    正しさを犠牲にせず速くできる場所であることを、テストで担保しておく。
    """
    dates = _rebalance_dates()
    records = _records(dates)
    target_days = list(pd.date_range("2025-05-01", periods=6, freq="MS"))

    quadratic = calibration_series(
        target_days, dates, records, MAX_HORIZON_MONTHS
    )

    matured_at = records.assign(
        matured=records["date"] + pd.DateOffset(months=MAX_HORIZON_MONTHS)
    )
    linear = {
        d: summarize(matured_at[matured_at["matured"] <= d])
        for d in target_days
    }

    assert quadratic == linear


def test_guard_fires_when_an_unmatured_row_is_selected():
    """歯止めが実際に落ちること。落ちない assert は歯止めではない。"""
    d = pd.Timestamp("2025-07-01")
    used = pd.DataFrame(
        [
            {"date": pd.Timestamp("2025-03-01"), "outcome": 1.0},
            # 3か月後は 2025-09-01。判断日にはまだ結果が出ていない
            {"date": pd.Timestamp("2025-06-01"), "outcome": 2.0},
        ]
    )
    with pytest.raises(AssertionError, match="未満了"):
        assert_no_unmatured(used, d, MAX_HORIZON_MONTHS)


def test_guard_passes_on_matured_rows():
    d = pd.Timestamp("2025-07-01")
    used = pd.DataFrame([{"date": pd.Timestamp("2025-03-01"), "outcome": 1.0}])
    assert_no_unmatured(used, d, MAX_HORIZON_MONTHS)  # 落ちなければ合格


def test_guard_survives_a_broken_selection():
    """選択ロジックを壊した実装に同じ歯止めを当てると落ちること。

    述語をもう一度書いた assert では、この壊し方を検出できない。
    """
    d = pd.Timestamp("2025-07-01")
    dates = _rebalance_dates()
    records = _records(dates)

    # 「満了済み」ではなく「判断日以前」で選んでしまう、よくある取り違え
    broken_selection = records[records["date"] <= d]

    with pytest.raises(AssertionError, match="未満了"):
        assert_no_unmatured(broken_selection, d, MAX_HORIZON_MONTHS)
