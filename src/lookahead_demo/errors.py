"""本体に置く歯止め用の例外。

**素の `assert` は `python -O` / `PYTHONOPTIMIZE=1` で消える。** 先読みの歯止めを
assert で書くと、その設定でだけ静かに素通りする。しかもテストも変異チェックも
`-O` では回さないので、消えたことに気づく経路が無い。

「静かに通さない」と謳う歯止めは、最適化フラグの影響を受けない明示的な raise に
する。テストの中の assert は pytest が書き換えるので対象外で、ここで言っている
のは **src/ 側に置いた歯止め**のこと。

`AssertionError` を継承しているのは、`pytest.raises(AssertionError)` と
「assert が落ちる」という読み手の期待をそのまま活かすため。
"""

from __future__ import annotations


class GuardError(AssertionError):
    """本体に置いた歯止めが作動したことを表す基底。"""


class LookaheadError(GuardError):
    """判断日より後の情報が混入した(しうる)状態を検出したときに送出する。"""


class AccountingError(GuardError):
    """建玉・現金の勘定が壊れたときに送出する。

    先読みではないが、こちらも「静かに数字を良くする」種類の欠陥なので
    同じ扱いにする。
    """
