"""経路A・経路Bのテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from lookahead_demo.naive import naive_score_by_period_end, naive_score_growth
from lookahead_demo.scoring import latest_close_as_of, score_growth


def test_score_growth_excludes_future_disclosures():
    """as_of_date 以前の開示だけが使われることを確認する。"""
    statements_df = pd.DataFrame(
        [
            {"Code": "A", "DiscDate": pd.Timestamp("2024-05-10"), "CurPerType": "1Q", "EPS": 80.0},
            {"Code": "A", "DiscDate": pd.Timestamp("2025-05-10"), "CurPerType": "1Q", "EPS": 100.0},
            # 未来の開示。使われると値が変わってしまう
            {"Code": "A", "DiscDate": pd.Timestamp("2026-05-10"), "CurPerType": "1Q", "EPS": 9999.0},
        ]
    )
    result = score_growth(statements_df, pd.Timestamp("2025-06-01"), ["A"])
    # 2025-06-01 時点で 2026-05-10 の開示はまだ存在しない → (100-80)/80
    assert result["A"] == pytest.approx(0.25)


def test_as_of_date_has_no_default():
    """渡し忘れたら TypeError で落ちること。黙って全期間を使わせない。"""
    with pytest.raises(TypeError):
        score_growth(pd.DataFrame(), codes=["A"])  # type: ignore[call-arg]


def test_score_growth_is_unchanged_by_breaking_the_future(statements_df):
    """一般形: 判断日より後の行を壊しても、それ以前の出力は変わらない。"""
    as_of = pd.Timestamp("2025-06-01")
    before = score_growth(statements_df, as_of, ["A", "B", "C"])

    broken = statements_df.copy()
    future = broken["DiscDate"] > as_of
    broken.loc[future, "EPS"] = -123456.0

    after = score_growth(broken, as_of, ["A", "B", "C"])
    pd.testing.assert_series_equal(before, after)


def test_naive_score_growth_is_detected(statements_df):
    """対照実装は同じ操作で値が変わる。テストが検出力を持っている証拠。"""
    as_of = pd.Timestamp("2025-06-01")
    before = naive_score_growth(statements_df, ["A"])

    broken = statements_df.copy()
    future = broken["DiscDate"] > as_of
    broken.loc[future, "EPS"] = -123456.0

    after = naive_score_growth(broken, ["A"])
    assert before["A"] != after["A"]


def test_period_end_filter_sees_two_months_of_future(statements_df):
    """経路B: 期末日で絞ると、まだ開示されていない決算が見えてしまう。

    2025-04-01 時点で 2025-03-31 期末の決算は存在しない(開示は 2025-05-10)。
    それでも期末日フィルタは通してしまう。
    """
    as_of = pd.Timestamp("2025-04-01")
    correct = score_growth(statements_df, as_of, ["A"])
    leaked = naive_score_by_period_end(statements_df, as_of, ["A"])

    # 正しい側は 2024-05-10 までの2件 → (80-60)/60
    assert correct["A"] == pytest.approx(20.0 / 60.0)
    # 漏れた側は 2025-03-31 期末を拾う → (100-80)/80
    assert leaked["A"] == pytest.approx(0.25)
    assert correct["A"] != leaked["A"]


def test_latest_close_ignores_future_prices(price_df):
    as_of = pd.Timestamp("2025-04-01")
    before = latest_close_as_of(price_df, as_of, ["A", "B"])

    broken = price_df.copy()
    broken.loc[broken["date"] > as_of, "close"] = 999999.0

    after = latest_close_as_of(broken, as_of, ["A", "B"])
    pd.testing.assert_series_equal(before, after)
