"""実行ログの合否が、空振りしない形で出ていることの検査。

記事 §3「実行ログに合否を出させる」に対応する現物。見るのは3つ。

1. **数字と一緒に合否が返ってくる**(結果を見た人が必ず目にする)
2. **一度も評価されていない歯止めがあれば PASS にならない** —— これが無いと、
   歯止めを1つも踏まなかった実行で緑が出る。この資産が4回踏んだ形と同じ
3. **宣言に無い名前を数えない** —— 打ち間違いを黙って受け入れると、宣言側が
   0 のままなのに、どこかで数が増えていて気づけない
"""

from __future__ import annotations

import logging

import pytest

from conftest import build_data
from lookahead_demo.backtest import run_backtest
from lookahead_demo.guardlog import PASS, VACUOUS, GuardLog, GuardVacuousError


def test_backtest_returns_pass_line_with_counts():
    """正常な実行では PASS が出て、両方の歯止めが実際に評価されている。"""
    dates, price_df, kwargs = build_data()
    result = run_backtest(trading_dates=dates, price_df=price_df, **kwargs)

    assert result.guard_report.startswith("lookahead_guards: PASS")
    # 🔴 「PASS と書いてある」だけでは足りない。**回数が 0 でないこと**まで見る
    assert "ascending_dates=0" not in result.guard_report
    assert "cash_deployed=0" not in result.guard_report


def test_pass_line_is_logged_before_numbers(caplog):
    """合否はログにも出る。数字を返す前に出ていることが要点。"""
    dates, price_df, kwargs = build_data()
    with caplog.at_level(logging.INFO, logger="lookahead_demo.guardlog"):
        run_backtest(trading_dates=dates, price_df=price_df, **kwargs)

    lines = [r.getMessage() for r in caplog.records]
    assert any(ln.startswith("lookahead_guards: PASS") for ln in lines), lines


def test_unchecked_guard_is_not_pass():
    """評価されていない歯止めがあれば VACUOUS。PASS にはならない。"""
    log = GuardLog(expected=("a", "b"))
    log.checked("a")

    assert log.verdict() == VACUOUS
    assert log.never_checked() == ["b"]
    with pytest.raises(GuardVacuousError, match="探していない"):
        log.require_pass()


def test_all_checked_is_pass():
    log = GuardLog(expected=("a", "b"))
    log.checked("a")
    log.checked("b")

    assert log.verdict() == PASS
    assert log.require_pass() == "lookahead_guards: PASS (a=1, b=1)"


def test_unknown_guard_name_is_rejected():
    """宣言に無い名前は黙って受け入れない(打ち間違いを PASS にしない)。"""
    log = GuardLog(expected=("a",))
    with pytest.raises(ValueError, match="宣言に無い歯止め名"):
        log.checked("A")


def test_empty_declaration_is_rejected():
    """宣言が空なら、この仕組み自体が空振りする。"""
    with pytest.raises(ValueError, match="expected が空"):
        GuardLog(expected=())
