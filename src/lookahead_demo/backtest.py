"""検証パイプライン本体。

データを読む責務はここには無い。DataFrame は呼び出し側(テストやハーネス)が
用意して渡す。ロジック側から未知のファイルを読む経路そのものが無いので、
先読みの入口が構造的に減る。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .errors import AccountingError, LookaheadError
from .scoring import (
    MAX_PRICE_STALENESS_DAYS,
    latest_close_as_of,
    score_growth,
    trailing_vol_as_of,
)
from .universe import build_pit_universe, restrict_with_count

TradeRecord = tuple[str, str, str, float, float, str]

# 現金の残高チェックで許す誤差。純資産に対する比率で見る(絶対額で書くと、
# 元本の桁が変わったときに意味が変わってしまう)。
CASH_TOLERANCE_RATIO = 1e-9


@dataclass
class BacktestResult:
    """検証結果。trade_log は比較しやすいよう素の値のタプルで持つ。"""

    trade_log: list[TradeRecord] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    dropped_by_restriction: int = 0
    undefined_universe_days: int = 0
    # ボラティリティを定義できず等ウェイトに倒した日数。restrict_with_count と
    # 同じ理由で、倒した事実を呼び出し側から数えられるようにしておく。
    equal_weight_days: int = 0


def portfolio_value(
    cash: float, holdings: dict[str, float], prices: pd.Series
) -> float:
    total = cash
    for code, shares in holdings.items():
        if code in prices.index:
            total += shares * float(prices[code])
    return total


def assert_cash_is_fully_deployed(cash: float, equity: float, day: str) -> None:
    """建て直しのあと、純資産がちょうど配分し切られていることを確かめる。

    重みの合計は 1 なので、現金は**ちょうど 0** になる。

    🔴 **両側を見る。** 資本の二重計上は逆向きにも振れる。実測では既定データで
    「現金が純資産の 19.31% 残る」という**正の側**に出たので、`cash < 0` だけを
    見る歯止めは、その歯止めが存在する理由になった欠陥をまさに見逃す。

    関数として切り出してあるのは、**片側だけに戻す変異を検出できるようにする**ため。
    ループの中に埋めたままだと、正しい実装では条件を踏めないので単体で叩けない。
    """
    if abs(cash) > CASH_TOLERANCE_RATIO * max(abs(equity), 1.0):
        raise AccountingError(
            f"{day}: 建て直しのあとに現金が残った({cash:,.2f} / "
            f"純資産 {equity:,.2f})。負なら建玉が純資産を超えており、"
            f"正なら純資産を配分し切れていない。どちらも既存建玉を"
            f"予算から差し引かなかったときに起きる"
        )


def inverse_vol_weights(
    vol: pd.Series, codes: list[str]
) -> tuple[dict[str, float], list[str]]:
    """ボラティリティの逆数で重みを作る。定義できない銘柄があれば等ウェイトに倒す。

    **倒すのは全銘柄で、一部だけではない。** 定義できない銘柄にだけ生の 1.0 を
    入れて他銘柄の 1/v と足すと、尺度の違うものを混ぜることになる。判断日までの
    価格が2行未満だと std()(ddof=1)が NaN になるので、**データが薄い銘柄ほど
    極端な比重を持つ**という逆の挙動になる(片方が NaN のとき、実測で
    {A: 0.0099, B: 0.9901})。

    倒したことは戻り値の第2要素で見える。restrict_with_count と同じ理由で、
    黙って埋めずに呼び出し側から数えられるようにしておく。

    Args:
        vol: 銘柄コードを index に持つボラティリティ。欠けていてよい。
        codes: 重みを付けたい銘柄コード。

    Returns:
        (重み, ボラティリティを定義できなかった銘柄の一覧)。
        第2要素が空でないとき、重みは全銘柄が等ウェイトになっている。
    """
    if not codes:
        return {}, []

    inv: dict[str, float] = {}
    undefined: list[str] = []
    for code in codes:
        v = float(vol[code]) if code in vol.index else float("nan")
        if v == v and v > 0:  # NaN でも 0 でもない
            inv[code] = 1.0 / v
        else:
            undefined.append(code)

    if undefined:
        equal = 1.0 / len(codes)
        return {code: equal for code in codes}, undefined

    total = sum(inv.values())
    return {code: inv[code] / total for code in codes}, undefined


def run_backtest(
    trading_dates: list[pd.Timestamp],
    price_df: pd.DataFrame,
    statements_df: pd.DataFrame,
    listed_info_df: pd.DataFrame,
    *,  # 以降はキーワード専用
    top_n: int = 2,
    initial_cash: float = 1_000_000.0,
    max_staleness_days: int = MAX_PRICE_STALENESS_DAYS,
) -> BacktestResult:
    """各判断日で、その日までに入手可能だった情報だけを使って組み替える。

    約定は判断日の終値。判断と約定が同日という単純化はしているが、
    「判断日より後の価格・開示・上場情報は一切見ない」という性質は保たれる。

    Args:
        max_staleness_days: 許容する鮮度(日)。**母集団と価格の両方に掛かる。**
            片方にだけ掛けると、母集団側が明示的に拒んでいる古さの銘柄が
            価格側から素通りする。実測で、7日を指定しても価格側は既定の 45日で
            動いており、**31日前の終値がその日の約定値として使われていた**。
            引数が届いていないことは、結果が同じになるので気づけない。
    """
    # 並び替えのつもりが順序を壊していた、という事故を止める。
    # assert ではなく raise。-O で消える歯止めは歯止めではない(errors.py)。
    if trading_dates != sorted(trading_dates):
        raise LookaheadError("trading_dates が昇順でない")

    universe = build_pit_universe(
        listed_info_df, trading_dates, max_staleness_days=max_staleness_days
    )

    cash = initial_cash
    holdings: dict[str, float] = {}
    trade_log: list[TradeRecord] = []
    equity_rows: list[dict[str, object]] = []
    dropped_total = 0
    undefined_days = 0
    equal_weight_days = 0

    for d in trading_dates:
        day = d.strftime("%Y-%m-%d")
        listed = universe.get(d)

        if listed is None:
            # 母集団を定義できない日。埋めずに、持ち高を据え置いて記録だけ残す。
            # ここは評価であって判断ではないので、鮮度上限は掛けない。掛けると
            # 古い銘柄が equity から黙って消え、資産が減ったように見えてしまう。
            undefined_days += 1
            held_prices = latest_close_as_of(
                price_df, d, list(holdings), max_staleness_days=None
            )
            equity_rows.append(
                {
                    "date": d,
                    "equity": portfolio_value(cash, holdings, held_prices),
                    "cash": cash,
                    "n_holdings": len(holdings),
                }
            )
            continue

        # 保有中だが母集団から消えたものは、消えた時点の最終値で明示的に打ち切る。
        # 黙って集計から外すのが、最も検出しづらい先読みになる。
        # 打ち切りは「最後に分かっている値」を使うのが正しいので上限を外す。
        for code in [c for c in holdings if c not in listed]:
            last = latest_close_as_of(price_df, d, [code], max_staleness_days=None)
            if code in last.index:
                cash += holdings[code] * float(last[code])
                trade_log.append(
                    (day, "SELL", code, holdings[code], float(last[code]), "delisted")
                )
            del holdings[code]

        # 判断に使う価格には鮮度上限が掛かる。落ちた銘柄は tradable から外れ、
        # restrict_with_count が件数として返す(黙って埋めない)。
        prices = latest_close_as_of(
            price_df, d, listed, max_staleness_days=max_staleness_days
        )
        tradable, dropped = restrict_with_count(listed, set(prices.index))
        dropped_total += dropped

        scores = score_growth(statements_df, d, tradable)
        targets = list(scores.sort_values(ascending=False).head(top_n).index)

        for code in [c for c in holdings if c not in targets]:
            if code in prices.index:
                cash += holdings[code] * float(prices[code])
                trade_log.append(
                    (day, "SELL", code, holdings[code], float(prices[code]), "rebalance")
                )
            else:
                # 母集団には残っているが、価格が古すぎて売値を出せない。黙って
                # 消すと建玉が蒸発するので、最後に分かっている値で打ち切る。
                last = latest_close_as_of(price_df, d, [code], max_staleness_days=None)
                if code in last.index:
                    cash += holdings[code] * float(last[code])
                    trade_log.append(
                        (
                            day,
                            "SELL",
                            code,
                            holdings[code],
                            float(last[code]),
                            "stale_price",
                        )
                    )
            del holdings[code]

        if targets:
            equity = portfolio_value(cash, holdings, prices)
            # 重みも d 時点までの価格だけから作る。ここが naive との唯一の違い。
            vol = trailing_vol_as_of(price_df, d, targets)
            weights, undefined_vol = inverse_vol_weights(vol, targets)
            if undefined_vol:
                equal_weight_days += 1

            for code in targets:
                px = float(prices[code])
                # 目標は「今の純資産 × 重み」。**既存の建玉も含めて**この水準に
                # 揃える。新規ぶんだけを equity から建てて既存を素通りさせると、
                # 既に持っている建玉の価値が予算から差し引かれず、資本を二重に
                # 数えることになる。現金が負に落ち、借入の記録も歯止めも無いまま
                # 暗黙のレバレッジが立つ(実測で cash -4,625,880 / 純資産の1.89倍)。
                # 逆向きにも振れ、その場合は純資産の一部が現金のまま遊ぶ。
                target_shares = equity * weights[code] / px
                delta = target_shares - holdings.get(code, 0.0)
                holdings[code] = target_shares
                if delta == 0.0:
                    continue
                cash -= delta * px
                trade_log.append(
                    (
                        day,
                        "BUY" if delta > 0 else "SELL",
                        code,
                        abs(delta),
                        px,
                        "rebalance",
                    )
                )

            assert_cash_is_fully_deployed(cash, equity, day)

        # cash と n_holdings も残す。建て直しのあと現金がいくら残ったかが
        # 見えないと、「資本の二重計上が起きていないこと」を外から検査できない。
        equity_rows.append(
            {
                "date": d,
                "equity": portfolio_value(cash, holdings, prices),
                "cash": cash,
                "n_holdings": len(holdings),
            }
        )

    return BacktestResult(
        trade_log=trade_log,
        equity_curve=pd.DataFrame(equity_rows),
        dropped_by_restriction=dropped_total,
        undefined_universe_days=undefined_days,
        equal_weight_days=equal_weight_days,
    )
