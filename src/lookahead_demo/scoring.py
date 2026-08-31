"""スコア計算。経路A(時点が引数に無い)と経路B(発生日で使う)を塞いだ実装。

この module の関数は、渡された DataFrame しか見ない。ファイルも API も読まない
(データ I/O は呼び出し側の責務)。読む経路が無ければ、そこから先読みは入らない。
"""

from __future__ import annotations

import pandas as pd

# 決算データが「読めるようになった日」を表す列。期末日ではなくこちらを使う。
DISCLOSURE_DATE_COL = "DiscDate"

# 終値の許容鮮度(日)。母集団側(build_pit_universe)が古すぎる日を落として
# いるのに価格側に上限が無いと、「母集団が明示的に拒んでいる黙って埋める」を
# 価格側でやることになる。既定を universe.MAX_STALENESS_DAYS と同じ 45 に揃える。
MAX_PRICE_STALENESS_DAYS = 45


def score_growth(
    statements_df: pd.DataFrame,
    as_of_date: pd.Timestamp,  # デフォルト値を与えない(経路A)
    codes: list[str],
) -> pd.Series:
    """as_of_date 時点で実際に入手可能だった情報だけを使って EPS 成長率を返す。

    同じ会計期間区分(CurPerType)の直近2件を比べる。前年同期が見つからない銘柄は
    黙って 0 で埋めず、結果から落とす(欠けたことが呼び出し側から見える)。

    Args:
        statements_df: Code / DiscDate / CurPerType / EPS を持つ決算データ。
            全期間ぶんが入っていてよい。絞り込みはこの関数が行う。
        as_of_date: 判断を行う日。この日までに開示されたものだけが使われる。
        codes: 対象の銘柄コード。

    Returns:
        index が銘柄コード、値が EPS 成長率の Series。
    """
    # 入る前に落とす。計算してから捨てるのではない。
    df = statements_df[
        (statements_df[DISCLOSURE_DATE_COL] <= as_of_date)
        & (statements_df["Code"].isin(codes))
    ]

    scores: dict[str, float] = {}
    for code, group in df.groupby("Code"):
        for _, per_type_group in group.groupby("CurPerType"):
            ordered = per_type_group.sort_values(DISCLOSURE_DATE_COL)
            if len(ordered) < 2:
                continue
            prev_eps = ordered.iloc[-2]["EPS"]
            latest_eps = ordered.iloc[-1]["EPS"]
            if prev_eps == 0:
                continue
            scores[code] = (latest_eps - prev_eps) / abs(prev_eps)
            break  # 最初に2件そろった区分を採用する

    return pd.Series(scores, dtype="float64")


def latest_close_as_of(
    price_df: pd.DataFrame,
    as_of_date: pd.Timestamp,  # デフォルト値を与えない(経路A)
    codes: list[str],
    *,  # 以降はキーワード専用
    max_staleness_days: int | None = MAX_PRICE_STALENESS_DAYS,
) -> pd.Series:
    """as_of_date までの最新終値を返す。as_of_date より後の行は一切見ない。

    **鮮度の上限を持つ。** 上限を超えた銘柄は戻り値から落とす(0 や直近値で
    埋めない)。何か月前の終値でも「その日の価格」として返してしまうと、
    その銘柄はスコアリング・約定・評価にそのまま使われ、しかも
    restrict_with_count からは 0件落ちと報告される。

    max_staleness_days に None を渡すと上限を掛けない。**打ち切りや評価など、
    「最後に分かっている値」を使うことが正しい場面のために明示で開けてある。**
    既定を安全側に置き、緩めるほうを呼び出し側に書かせる。

    Args:
        price_df: date / Code / close を持つ価格データ。全期間ぶんでよい。
        as_of_date: 判断を行う日。この日までの行だけが使われる。
        codes: 対象の銘柄コード。
        max_staleness_days: 終値の許容鮮度(日)。None で無制限。

    Returns:
        index が銘柄コード、値が終値の Series。古すぎる銘柄は index に現れない。
    """
    df = price_df[
        (price_df["date"] <= as_of_date) & (price_df["Code"].isin(codes))
    ]
    if df.empty:
        return pd.Series(dtype="float64")
    latest = df.sort_values("date").groupby("Code").tail(1)
    if max_staleness_days is not None:
        staleness = (as_of_date - latest["date"]).dt.days
        latest = latest[staleness <= max_staleness_days]
        if latest.empty:
            return pd.Series(dtype="float64")
    return latest.set_index("Code")["close"]


def trailing_vol_as_of(
    price_df: pd.DataFrame,
    as_of_date: pd.Timestamp,  # デフォルト値を与えない(経路A)
    codes: list[str],
) -> pd.Series:
    """as_of_date までの終値だけからボラティリティを出す。

    「リスクで割る」処理は正当に見えるぶん、全期間から計算しても違和感が無い。
    だからこそ経路Dの温床になる。時点で切るのはこの1行だけだが、これが無いと
    判断日にはまだ計算できない数字を使うことになる。
    """
    df = price_df[
        (price_df["date"] <= as_of_date) & (price_df["Code"].isin(codes))
    ]
    return df.groupby("Code")["close"].std()
