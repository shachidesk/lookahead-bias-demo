"""テストの検出力そのものを試す。

通っている先読みテストは、**素通りしているのか、実際に検出しているのか**が
それだけでは分からない。同じ手続きを、わざと先読みを入れた対照実装に当てて、
そこで**差が出ること**を確認する。ここが落ちたら、先読みテストのほうを疑う。
"""

from __future__ import annotations

import pandas as pd

from lookahead_demo.backtest import run_backtest
from lookahead_demo.naive import naive_run_backtest

from conftest import CUTOFF, DELISTED_CODE, build_data, tamper_future_disclosures


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
    """生存者バイアスは取引履歴に現れる。正しい実装との**差**で見る。

    「naive に delisted が1件も無いこと」だけを見る書き方は空振りする。
    naive_run_backtest は "delisted" という理由を**構造上1件も出さない**ので、
    母集団の作り方が正しかろうと間違っていようと必ず真になる。実測で
    naive の reason 集合は {'rebalance'} のみだった。

    見るべきは正しい実装との差で、順序も大事になる。**まず正しい側に打ち切りが
    実在することを確かめてから**、対照側にそれが無いことを言う。前半が無いと、
    データの都合で打ち切りが起きなくなった日に検査ごと空振りする。
    """
    dates, price_df, kwargs = build_data()

    correct = run_backtest(trading_dates=dates, price_df=price_df, **kwargs)
    naive = naive_run_backtest(dates, price_df, **kwargs)

    # 空振り防止(その1)。正しい側に打ち切りが実在すること。
    delisted = [row for row in correct.trade_log if row[5] == "delisted"]
    assert delisted, (
        "正しい実装に打ち切りが1件も無い。データ側の問題で、この検査は空振りしている"
    )
    assert {row[2] for row in delisted} == {DELISTED_CODE}

    # 空振り防止(その2)。対照側でも取引自体は起きていること。
    assert naive.trade_log, "対照実装で取引が起きていない。データ側の問題"

    # 本題。対照実装は最新スナップショット1枚を全期間に当てるので、途中で
    # 消えた銘柄は過去の母集団からも消える。打ち切りという事象自体が起きない。
    # naive_build_universe を直すとここが落ちる = 検出力がある。
    assert DELISTED_CODE not in {row[2] for row in naive.trade_log}, (
        "対照実装が、消えた銘柄を過去に扱っている。経路Cの再現になっていない"
    )


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
