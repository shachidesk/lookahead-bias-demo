"""キャリブレーション。経路D(しきい値を全期間から決める)を塞いだ実装。"""

from __future__ import annotations

import pandas as pd


def summarize(records: pd.DataFrame) -> dict[str, float]:
    """渡されたレコードだけを要約する。読み込みは一切しない。"""
    if records.empty:
        return {"n": 0.0, "mean": float("nan")}
    return {"n": float(len(records)), "mean": float(records["outcome"].mean())}


def matured_by(
    dates: list[pd.Timestamp], d: pd.Timestamp, max_horizon_months: int
) -> list[pd.Timestamp]:
    """d 時点で保有期間が満了している日だけを返す。"""
    return [
        rd
        for rd in dates
        if rd + pd.DateOffset(months=max_horizon_months) <= d
    ]


def assert_no_unmatured(
    used: pd.DataFrame, d: pd.Timestamp, max_horizon_months: int
) -> None:
    """先読み即死。**選ばれた行そのもの**に未満了が混ざっていないか見る。

    ここが要点で、絞り込みに使った述語をもう一度書いてはいけない。同じ式を
    二度書いた assert は構造上必ず真になり、歯止めのように見えて何も見張って
    いない。検査すべきは「どう絞ったか」ではなく「結果に何が入ったか」で、
    そうしておくと後から選択ロジックを差し替えたときにこの検査が生き残る。
    """
    if used.empty:
        return
    latest = used["date"].max() + pd.DateOffset(months=max_horizon_months)
    assert latest <= d, (
        f"calibration に未満了(将来)の日が混入している: "
        f"満了 {latest.date()} > 判断日 {d.date()}"
    )


def calibration_for(
    d: pd.Timestamp,
    all_rebalance: list[pd.Timestamp],
    records: pd.DataFrame,
    max_horizon_months: int,
) -> dict[str, float]:
    """d 時点のキャリブレーションを、満了済みの過去だけから作る。

    records は全期間ぶんの生レコード。読み込みは呼び出し側の責務で、
    この関数は渡されたものしか見ない(経路Aと同じ理由で引数に出す)。
    """
    eligible = set(matured_by(all_rebalance, d, max_horizon_months))
    used = records[records["date"].isin(eligible)]

    # テストではなく本体に置く。先読みは静かに数字を良くするので、静かに通さない。
    assert_no_unmatured(used, d, max_horizon_months)

    return summarize(used)


def calibration_series(
    dates: list[pd.Timestamp],
    all_rebalance: list[pd.Timestamp],
    records: pd.DataFrame,
    max_horizon_months: int,
) -> dict[pd.Timestamp, dict[str, float]]:
    """各 d のキャリブレーションをまとめて作る。

    素直に calibration_for を回すと、日が進むたびに過去を丸ごと作り直すため
    O(n^2) になりやすい。レコード単位の生成が日付に依存しないなら、
    「全期間ぶんを1回だけ作り、各 d では行フィルタで絞る」に置き換えられる。
    結果は行単位で同一のまま O(n) に落ちる。ここでは素直な形を残している
    (小さい入力しか扱わないため)。両者の一致は tests が確認している。
    """
    return {
        d: calibration_for(d, all_rebalance, records, max_horizon_months)
        for d in dates
    }
