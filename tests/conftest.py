"""テスト用のダミーデータ一式。

ここが唯一「データを作る」場所。src/ 側はどの関数も、渡された DataFrame しか
見ない。読む経路が無いので、先読みはこのファイルの外からは入りようがない。

CUTOFF より後だけを壊せるように作ってあるのが要点。
「判断日より後を壊す → それ以前の出力が変わらないことを assert する」
という先読みテストの一般形が、この構造の上で書ける。
"""

from __future__ import annotations

import pandas as pd
import pytest

CODES = ["A", "B", "C"]

# 判断日。ここより後のデータを壊して、ここ以前の出力が動かないことを見る。
CUTOFF = pd.Timestamp("2025-04-01")

# C は 2025-06 を最後に母集団から消える(上場廃止に相当)
DELISTED_CODE = "C"
DELIST_AFTER = pd.Timestamp("2025-06-01")

_PRICE_START = pd.Timestamp("2024-01-01")
_PRICE_MONTHS = 24
_BASE_PRICE = {"A": 1000.0, "B": 2000.0, "C": 3000.0}
_DRIFT = {"A": 0.01, "B": 0.02, "C": 0.005}

SHOCK_CODE = "A"
SHOCK_MULTIPLIER = 3.0


def build_price_df(*, future_shock: bool = False) -> pd.DataFrame:
    """月次の終値。future_shock=True のとき CUTOFF より後だけ異常値になる。"""
    dates = pd.date_range(_PRICE_START, periods=_PRICE_MONTHS, freq="MS")
    rows = []
    for code in CODES:
        for i, d in enumerate(dates):
            if code == DELISTED_CODE and d > DELIST_AFTER:
                continue
            close = _BASE_PRICE[code] * (1.0 + _DRIFT[code] * i)
            if future_shock and code == SHOCK_CODE and d > CUTOFF:
                # 露骨な値を置く。混入したら桁で変わるので、誤差と区別がつく。
                close *= SHOCK_MULTIPLIER
            rows.append({"date": d, "Code": code, "close": close})
    return pd.DataFrame(rows)


def build_statements_df() -> pd.DataFrame:
    """決算データ。DiscDate(開示日)と PeriodEnd(期末日)を別々に持つ。

    期末日は開示日の約2か月前。期末日で絞ると2か月ぶん未来を知ることになる。
    """
    eps = {
        "A": {2023: 60.0, 2024: 80.0, 2025: 100.0},
        "B": {2023: 45.0, 2024: 50.0, 2025: 55.0},
        "C": {2023: 20.0, 2024: 30.0, 2025: 60.0},
    }
    rows = []
    for code, by_year in eps.items():
        for year, value in by_year.items():
            rows.append(
                {
                    "Code": code,
                    "PeriodEnd": pd.Timestamp(f"{year}-03-31"),
                    "DiscDate": pd.Timestamp(f"{year}-05-10"),
                    "CurPerType": "1Q",
                    "EPS": value,
                }
            )
    # テスト対象期間より後の開示。使われたら値が桁で変わる。
    rows.append(
        {
            "Code": "A",
            "PeriodEnd": pd.Timestamp("2026-03-31"),
            "DiscDate": pd.Timestamp("2026-05-10"),
            "CurPerType": "1Q",
            "EPS": 9999.0,
        }
    )
    return pd.DataFrame(rows)


def build_listed_info_df() -> pd.DataFrame:
    """上場一覧のスナップショット。

    2025-09 と 2025-10 のスナップショットを欠いている。母集団を定義できない日が
    実際に出るようにしてある(隣月で黙って埋めないことを確認するため)。
    """
    snapshots = list(pd.date_range("2024-12-01", periods=13, freq="MS"))
    missing = {pd.Timestamp("2025-09-01"), pd.Timestamp("2025-10-01")}
    rows = []
    for snap in snapshots:
        if snap in missing:
            continue
        for code in CODES:
            if code == DELISTED_CODE and snap > DELIST_AFTER:
                continue
            rows.append({"snapshot_date": snap, "Code": code})
    return pd.DataFrame(rows)


def build_data(
    *, future_shock: bool = False
) -> tuple[list[pd.Timestamp], pd.DataFrame, dict[str, pd.DataFrame]]:
    """判断日の一覧・価格・その他の入力をまとめて返す。

    Returns:
        (trading_dates, price_df, kwargs)
        kwargs は run_backtest にそのまま ** で渡せる形。
    """
    trading_dates = list(pd.date_range("2025-01-01", periods=12, freq="MS"))
    price_df = build_price_df(future_shock=future_shock)
    kwargs = {
        "statements_df": build_statements_df(),
        "listed_info_df": build_listed_info_df(),
    }
    return trading_dates, price_df, kwargs


@pytest.fixture
def statements_df() -> pd.DataFrame:
    return build_statements_df()


@pytest.fixture
def listed_info_df() -> pd.DataFrame:
    return build_listed_info_df()


@pytest.fixture
def price_df() -> pd.DataFrame:
    return build_price_df()


def tamper_future_disclosures(
    statements_df: pd.DataFrame, cutoff: pd.Timestamp = CUTOFF
) -> pd.DataFrame:
    """cutoff より後の開示だけを壊す。**順位が動く壊し方**にしてある。

    ここは設計が要る。全銘柄を同じ向きに壊すと、スコアは激変しても順位は
    保たれる。順位で選ぶパイプラインでは出力が1ビットも変わらず、先読みが
    あっても検出できない。「未来を壊す」だけでは足りず、壊した結果が
    出力に届く経路まで作って初めてテストになる。

    そこで最下位の銘柄だけを上向きに壊し、順位が入れ替わるようにする。
    """
    broken = statements_df.copy()
    future = broken["DiscDate"] > cutoff
    broken.loc[future & (broken["Code"] == "B"), "EPS"] = 999999.0
    broken.loc[future & (broken["Code"] != "B"), "EPS"] = -123456.0
    return broken
