"""検証パイプライン本体。

データを読む責務はここには無い。DataFrame は呼び出し側(テストやハーネス)が
用意して渡す。ロジック側から未知のファイルを読む経路そのものが無いので、
先読みの入口が構造的に減る。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .scoring import latest_close_as_of, score_growth, trailing_vol_as_of
from .universe import build_pit_universe, restrict_with_count

TradeRecord = tuple[str, str, str, float, float, str]


@dataclass
class BacktestResult:
    """検証結果。trade_log は比較しやすいよう素の値のタプルで持つ。"""

    trade_log: list[TradeRecord] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    dropped_by_restriction: int = 0
    undefined_universe_days: int = 0


def portfolio_value(
    cash: float, holdings: dict[str, float], prices: pd.Series
) -> float:
    total = cash
    for code, shares in holdings.items():
        if code in prices.index:
            total += shares * float(prices[code])
    return total


def inverse_vol_weights(vol: pd.Series, codes: list[str]) -> dict[str, float]:
    """ボラティリティの逆数で重みを作る。定義できない銘柄は等ウェイトに倒す。"""
    inv = {}
    for code in codes:
        v = float(vol[code]) if code in vol.index else float("nan")
        inv[code] = 1.0 / v if v == v and v > 0 else 1.0
    total = sum(inv.values())
    return {code: inv[code] / total for code in codes}


def run_backtest(
    trading_dates: list[pd.Timestamp],
    price_df: pd.DataFrame,
    statements_df: pd.DataFrame,
    listed_info_df: pd.DataFrame,
    *,  # 以降はキーワード専用
    top_n: int = 2,
    initial_cash: float = 1_000_000.0,
    max_staleness_days: int = 45,
) -> BacktestResult:
    """各判断日で、その日までに入手可能だった情報だけを使って組み替える。

    約定は判断日の終値。判断と約定が同日という単純化はしているが、
    「判断日より後の価格・開示・上場情報は一切見ない」という性質は保たれる。
    """
    # 並び替えのつもりが順序を壊していた、という事故を止める1行
    assert trading_dates == sorted(trading_dates), "trading_dates が昇順でない"

    universe = build_pit_universe(
        listed_info_df, trading_dates, max_staleness_days=max_staleness_days
    )

    cash = initial_cash
    holdings: dict[str, float] = {}
    trade_log: list[TradeRecord] = []
    equity_rows: list[dict[str, object]] = []
    dropped_total = 0
    undefined_days = 0

    for d in trading_dates:
        day = d.strftime("%Y-%m-%d")
        listed = universe.get(d)

        if listed is None:
            # 母集団を定義できない日。埋めずに、持ち高を据え置いて記録だけ残す。
            undefined_days += 1
            held_prices = latest_close_as_of(price_df, d, list(holdings))
            equity_rows.append(
                {"date": d, "equity": portfolio_value(cash, holdings, held_prices)}
            )
            continue

        # 保有中だが母集団から消えたものは、消えた時点の最終値で明示的に打ち切る。
        # 黙って集計から外すのが、最も検出しづらい先読みになる。
        for code in [c for c in holdings if c not in listed]:
            last = latest_close_as_of(price_df, d, [code])
            if code in last.index:
                cash += holdings[code] * float(last[code])
                trade_log.append(
                    (day, "SELL", code, holdings[code], float(last[code]), "delisted")
                )
            del holdings[code]

        prices = latest_close_as_of(price_df, d, listed)
        tradable, dropped = restrict_with_count(listed, set(prices.index))
        dropped_total += dropped

        scores = score_growth(statements_df, d, tradable)
        targets = list(scores.sort_values(ascending=False).head(top_n).index)

        for code in [c for c in holdings if c not in targets]:
            cash += holdings[code] * float(prices[code])
            trade_log.append(
                (day, "SELL", code, holdings[code], float(prices[code]), "rebalance")
            )
            del holdings[code]

        if targets:
            equity = portfolio_value(cash, holdings, prices)
            # 重みも d 時点までの価格だけから作る。ここが naive との唯一の違い。
            vol = trailing_vol_as_of(price_df, d, targets)
            weights = inverse_vol_weights(vol, targets)
            for code in targets:
                if code in holdings:
                    continue
                budget = equity * weights[code]
                shares = budget / float(prices[code])
                cash -= shares * float(prices[code])
                holdings[code] = shares
                trade_log.append(
                    (day, "BUY", code, shares, float(prices[code]), "rebalance")
                )

        equity_rows.append(
            {"date": d, "equity": portfolio_value(cash, holdings, prices)}
        )

    return BacktestResult(
        trade_log=trade_log,
        equity_curve=pd.DataFrame(equity_rows),
        dropped_by_restriction=dropped_total,
        undefined_universe_days=undefined_days,
    )
