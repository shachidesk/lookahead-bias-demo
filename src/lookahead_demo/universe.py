"""母集団の構築。経路C(今の一覧を過去に当てはめる = 生存者バイアス)を塞いだ実装。"""

from __future__ import annotations

import pandas as pd

MAX_STALENESS_DAYS = 45


def build_pit_universe(
    listed_info_df: pd.DataFrame,
    dates: list[pd.Timestamp],
    *,  # 以降はキーワード専用。位置引数の順番違いで日付が別の意味になる事故を防ぐ
    max_staleness_days: int = MAX_STALENESS_DAYS,
) -> dict[pd.Timestamp, list[str]]:
    """各日付 d について「d 時点で実際に存在していた銘柄の一覧」を返す。

    d 以前で最も新しいスナップショットを母集団とすれば、その時点で入手可能な
    情報だけを使うことになり、先読みは入らない。

    直近のスナップショットが max_staleness_days より古い日付は、「母集団を
    定義できない日」として**戻り値のキーごと落とす**。取りこぼした日を隣の日で
    黙って埋めると、埋めた事実が見えなくなる。

    Args:
        listed_info_df: snapshot_date / Code を持つ上場一覧のスナップショット。
        dates: 母集団を作りたい日付の一覧。
        max_staleness_days: スナップショットの許容鮮度(日)。

    Returns:
        日付 -> その日の銘柄コード一覧。定義できない日はキーごと存在しない。
    """
    snapshots = sorted(listed_info_df["snapshot_date"].unique())
    universe: dict[pd.Timestamp, list[str]] = {}

    for d in dates:
        available = [s for s in snapshots if s <= d]
        if not available:
            continue
        latest = available[-1]
        if (d - latest).days > max_staleness_days:
            continue  # 古すぎる。埋めずに落とす
        codes = listed_info_df.loc[
            listed_info_df["snapshot_date"] == latest, "Code"
        ].tolist()
        universe[d] = sorted(codes)

    return universe


def restrict_with_count(
    codes: list[str], allowed: set[str]
) -> tuple[list[str], int]:
    """母集団を絞り込み、**何件落ちたか**を一緒に返す。

    「価格データがある銘柄だけに限定」のような一見無害な絞り込みは、退場した
    ものを再び消して生存者バイアスを復活させる。件数を返り値に出しておくと、
    呼び出し側が黙って捨てられない。
    """
    kept = [c for c in codes if c in allowed]
    return kept, len(codes) - len(kept)
