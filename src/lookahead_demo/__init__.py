"""先読みバイアスの混入経路を塞いだ実装と、それを検出するテストの実例。

記事「AIにバックテストを書かせると、構造的に先読みバイアスが入る」の付録。

- scoring     経路A(時点が引数に無い) / 経路B(発生日で使う)
- universe    経路C(今の母集団を過去に当てはめる)
- calibration 経路D(全期間からしきい値を決める)
- backtest    上記を組んだパイプライン
- naive       わざと先読みを入れた対照実装(テストの検出力を確かめるため)
"""

from .backtest import BacktestResult, run_backtest
from .calibration import calibration_for, calibration_series, summarize
from .scoring import latest_close_as_of, score_growth
from .universe import build_pit_universe, restrict_with_count

__all__ = [
    "BacktestResult",
    "build_pit_universe",
    "calibration_for",
    "calibration_series",
    "latest_close_as_of",
    "restrict_with_count",
    "run_backtest",
    "score_growth",
    "summarize",
]
