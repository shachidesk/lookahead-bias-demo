"""対照用の「素直に書いた」実装。**わざと先読みが入っている。**

なぜこれを置くか。先読みテストは、書いただけでは**素通りしているのか、実際に
検出しているのかが分からない**。通っているテストが、そもそも何も見張っていない
可能性があるからだ。

そこで同じテストを、この壊れた実装にも当てる。ここで**落ちること**を確認して
初めて、テストが検出力を持っていると言える(tests/test_detects_naive.py)。

実運用に使わないこと。
"""

from __future__ import annotations

import pandas as pd

from .backtest import BacktestResult, TradeRecord, inverse_vol_weights, portfolio_value


def naive_score_growth(
    statements_df: pd.DataFrame, codes: list[str]
) -> pd.Series:
    """経路A: 時点が引数に無い。全期間の開示を見てしまう。"""
    df = statements_df[statements_df["Code"].isin(codes)]
    scores: dict[str, float] = {}
    for code, group in df.groupby("Code"):
        for _, per_type_group in group.groupby("CurPerType"):
            ordered = per_type_group.sort_values("DiscDate")
            if len(ordered) < 2:
                continue
            prev_eps = ordered.iloc[-2]["EPS"]
            latest_eps = ordered.iloc[-1]["EPS"]
            if prev_eps == 0:
                continue
            scores[code] = (latest_eps - prev_eps) / abs(prev_eps)
            break
    return pd.Series(scores, dtype="float64")


def naive_score_by_period_end(
    statements_df: pd.DataFrame, as_of_date: pd.Timestamp, codes: list[str]
) -> pd.Series:
    """経路B: 時点は受け取っているが、開示日ではなく**期末日**で絞っている。

    シグネチャだけ見ると対策済みに見える。ここが経路Bの厄介なところ。
    """
    df = statements_df[
        (statements_df["PeriodEnd"] <= as_of_date)
        & (statements_df["Code"].isin(codes))
    ]
    scores: dict[str, float] = {}
    for code, group in df.groupby("Code"):
        for _, per_type_group in group.groupby("CurPerType"):
            ordered = per_type_group.sort_values("PeriodEnd")
            if len(ordered) < 2:
                continue
            prev_eps = ordered.iloc[-2]["EPS"]
            latest_eps = ordered.iloc[-1]["EPS"]
            if prev_eps == 0:
                continue
            scores[code] = (latest_eps - prev_eps) / abs(prev_eps)
            break
    return pd.Series(scores, dtype="float64")


def naive_build_universe(
    listed_info_df: pd.DataFrame, dates: list[pd.Timestamp]
) -> dict[pd.Timestamp, list[str]]:
    """経路C: 最新のスナップショット1枚を、全期間に当てはめる。

    途中で消えた銘柄は、過去の日付の母集団からも消える。生存者バイアス。
    """
    latest = max(listed_info_df["snapshot_date"])
    codes = sorted(
        listed_info_df.loc[listed_info_df["snapshot_date"] == latest, "Code"]
    )
    return {d: codes for d in dates}


def naive_calibration(
    all_rebalance: list[pd.Timestamp], records: pd.DataFrame
) -> dict[str, float]:
    """経路D: 全期間のレコードから1つのキャリブレーションを作る。"""
    subset = records[records["date"].isin(set(all_rebalance))]
    if subset.empty:
        return {"n": 0, "mean": float("nan")}
    return {"n": float(len(subset)), "mean": float(subset["outcome"].mean())}


def naive_run_backtest(
    trading_dates: list[pd.Timestamp],
    price_df: pd.DataFrame,
    statements_df: pd.DataFrame,
    listed_info_df: pd.DataFrame,
    *,
    top_n: int = 2,
    initial_cash: float = 1_000_000.0,
) -> BacktestResult:
    """経路A・C・D をまとめて踏んだパイプライン。

    - 母集団は最新スナップショット1枚(経路C)
    - スコアは全期間の開示から(経路A)
    - 建玉の重みを**全期間の**ボラティリティから決める(経路D)

    3つ目が特に見つけにくい。「リスクで割る」のは正当な処理に見えるし、
    テストも通る。ただしそのボラティリティは、判断日にはまだ計算できない。
    """
    universe = naive_build_universe(listed_info_df, trading_dates)

    # 判断日には存在しない未来を含む、全期間のボラティリティ
    full_period_vol = price_df.groupby("Code")["close"].std()

    cash = initial_cash
    holdings: dict[str, float] = {}
    trade_log: list[TradeRecord] = []
    equity_rows: list[dict[str, object]] = []

    for d in trading_dates:
        day = d.strftime("%Y-%m-%d")
        listed = universe[d]
        as_of = price_df[
            (price_df["date"] <= d) & (price_df["Code"].isin(listed))
        ]
        if as_of.empty:
            continue
        prices = (
            as_of.sort_values("date").groupby("Code").tail(1).set_index("Code")["close"]
        )

        scores = naive_score_growth(statements_df, list(prices.index))
        targets = list(scores.sort_values(ascending=False).head(top_n).index)

        for code in [c for c in holdings if c not in targets]:
            if code in prices.index:
                cash += holdings[code] * float(prices[code])
                trade_log.append(
                    (day, "SELL", code, holdings[code], float(prices[code]), "rebalance")
                )
            del holdings[code]

        if targets:
            equity = portfolio_value(cash, holdings, prices)
            # 正しい実装との違いは、ここに渡す vol が全期間か d 時点までか、だけ
            weights = inverse_vol_weights(full_period_vol, targets)
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

        equity_rows.append({"date": d, "equity": portfolio_value(cash, holdings, prices)})

    return BacktestResult(
        trade_log=trade_log, equity_curve=pd.DataFrame(equity_rows)
    )
