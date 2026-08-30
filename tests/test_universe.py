"""経路C(生存者バイアス)のテスト。"""

from __future__ import annotations

import pandas as pd

from lookahead_demo.naive import naive_build_universe
from lookahead_demo.universe import build_pit_universe, restrict_with_count

from conftest import DELIST_AFTER, DELISTED_CODE


def test_delisted_code_is_present_before_it_disappears(listed_info_df):
    """消えた銘柄が、消える前の日付の母集団には入っていること。"""
    dates = list(pd.date_range("2025-01-01", periods=12, freq="MS"))
    universe = build_pit_universe(listed_info_df, dates)

    assert DELISTED_CODE in universe[pd.Timestamp("2025-03-01")]
    assert DELISTED_CODE in universe[DELIST_AFTER]
    assert DELISTED_CODE not in universe[pd.Timestamp("2025-08-01")]


def test_naive_universe_erases_the_delisted_code(listed_info_df):
    """対照実装では、消えた銘柄が過去からも消える。これが生存者バイアス。"""
    dates = list(pd.date_range("2025-01-01", periods=12, freq="MS"))
    universe = naive_build_universe(listed_info_df, dates)

    assert DELISTED_CODE not in universe[pd.Timestamp("2025-03-01")]


def test_stale_snapshot_days_are_dropped_not_filled(listed_info_df):
    """スナップショットが古すぎる日は、埋めずにキーごと落とす。"""
    dates = list(pd.date_range("2025-01-01", periods=12, freq="MS"))
    universe = build_pit_universe(listed_info_df, dates, max_staleness_days=45)

    # 2025-09 / 2025-10 のスナップショットが無い
    assert pd.Timestamp("2025-09-01") in universe  # 直近は 08-01(31日前)→ 使える
    assert pd.Timestamp("2025-10-01") not in universe  # 61日前 → 定義できない
    assert pd.Timestamp("2025-11-01") in universe


def test_universe_is_unchanged_by_breaking_future_snapshots(listed_info_df):
    """一般形: 判断日より後のスナップショットを壊しても、それ以前は変わらない。"""
    dates = list(pd.date_range("2025-01-01", periods=4, freq="MS"))
    before = build_pit_universe(listed_info_df, dates)

    broken = listed_info_df.copy()
    future = broken["snapshot_date"] > pd.Timestamp("2025-04-01")
    broken.loc[future, "Code"] = "ZZZZ"

    after = build_pit_universe(broken, dates)
    assert before == after


def test_restriction_reports_how_many_were_dropped():
    """絞り込みで生存者バイアスを復活させないため、落ちた件数を返り値に出す。"""
    kept, dropped = restrict_with_count(["A", "B", "C"], {"A", "B"})
    assert kept == ["A", "B"]
    assert dropped == 1
