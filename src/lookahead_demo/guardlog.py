"""実行のたびに歯止めの合否を1行出し、PASS 以外なら数字を返さない。

なぜ要るか。**人間は良い数字を見てから検証すると甘くなる。** 先読みは静かに
数字を良くする種類の欠陥なので、結果を眺めてから「ところで検証は通ったっけ」と
振り返る順序だと、確かめる動機がそこで一番弱い。だから**数字より先に合否を出し、
PASS でなければ以降の処理を止める**。

ただし、ここには**この資産が4回踏んだ落とし穴**がそのまま口を開けている ——
**一度も評価されていない歯止めは、PASS ではない。** 歯止めを1つも踏まなかった
実行で `PASS` と出すのは、空振りする assert とまったく同じ形をしている。
「歯止めが作動しなかった」と「歯止めが存在しなかった」を、ログが区別できて
いなければ、その PASS は何も言っていない。

そこでこのモジュールは**回数を数える**。宣言した歯止めのうち1つでも評価回数が
0 なら、判定は PASS ではなく **VACUOUS**(空振り)で、`GuardVacuousError` を
送出して止める。

    lookahead_guards: PASS (ascending_dates=1, pit_universe=12, cash_deployed=10)
    lookahead_guards: VACUOUS (ascending_dates=1, pit_universe=12, cash_deployed=0)
                              ^^^^ 一度も踏んでいない歯止めがある

`logging` にも同じ行を流すが、**止める判断はログ設定に依存させない**
(ログを黙らせても歯止めは効く)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .errors import GuardError

logger = logging.getLogger(__name__)

PASS = "PASS"
VACUOUS = "VACUOUS"


class GuardVacuousError(GuardError):
    """宣言した歯止めのうち、一度も評価されなかったものがある。

    先読みが見つからなかったのではなく、**探していない**。
    """


@dataclass
class GuardLog:
    """1回の実行で、どの歯止めが何回評価されたかを数える。

    `expected` に挙げた名前が、この実行で最低1回は評価されていなければならない。
    **「作動しなかった」は正常だが、「評価されなかった」は異常。**
    """

    expected: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.expected:
            # 宣言が空なら、この仕組み自体が空振りする
            raise ValueError("expected が空。数えるべき歯止めを宣言していない")
        self.counts = {name: 0 for name in self.expected}

    def checked(self, name: str) -> None:
        """歯止めを1回評価したことを記録する(作動の有無ではない)。"""
        if name not in self.counts:
            # 名前の打ち間違いを黙って新しい歯止めとして受け入れない。
            # 受け入れると、宣言側の名前は0のまま PASS が出なくなる…のではなく、
            # **宣言に無い名前を数えるので、宣言側が空振りしていても気づけない**
            raise ValueError(f"宣言に無い歯止め名: {name!r}(expected={self.expected})")
        self.counts[name] += 1

    def verdict(self) -> str:
        return VACUOUS if self.never_checked() else PASS

    def never_checked(self) -> list[str]:
        return [name for name, n in self.counts.items() if n == 0]

    def line(self) -> str:
        detail = ", ".join(f"{name}={self.counts[name]}" for name in self.expected)
        return f"lookahead_guards: {self.verdict()} ({detail})"

    def require_pass(self) -> str:
        """合否を1行出し、PASS でなければ送出する。

        **返り値を組み立てる前に呼ぶこと。** 数字を返してからログを見るのでは、
        止めている意味が無い。
        """
        line = self.line()
        logger.info(line)
        if self.verdict() != PASS:
            raise GuardVacuousError(
                f"{line} —— 一度も評価されていない歯止めがある: "
                f"{self.never_checked()}。"
                "先読みが無かったのではなく、探していない"
            )
        return line
