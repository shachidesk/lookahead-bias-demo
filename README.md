# lookahead-bias-demo

過去データで仮説を検証するコードに**先読みバイアス**(look-ahead bias)が入る4つの経路と、
それを塞いだ実装、そして**塞げていることを確かめるテスト**の実例。

先読みバイアスは、その時点ではまだ知りようがなかった情報が、その時点の判断に
混ざってしまう欠陥のこと。厄介なのは症状の出方で、**壊れる方向ではなく良くなる方向**に出る。
例外は投げないし、「動くか」を見るテストは全部通る。そして良い数字は、悪い数字より疑われにくい。

株価データを題材にしているが、混入する経路そのものは「過去の記録で仮説を試す」あらゆる処理に共通する。
売上の予測、需要の見積もり、A/B の事後分析、解約の予測——どれも同じ穴を持つ。

> **注**: このリポジトリは検証コードの書き方についてのものであり、
> 特定の銘柄や投資判断について述べるものではありません。

## 動かす

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
.venv/bin/pytest
```

```
90 passed
```

## 4つの混入経路

| 経路 | 症状 | 塞いだ実装 | 検出するテスト |
|---|---|---|---|
| **A** 時点が引数に入っていない | 先読みしていないことを外から確かめる手段が無い | [`scoring.py`](src/lookahead_demo/scoring.py) `as_of_date` を必須引数にする | `test_scoring.py::test_as_of_date_has_no_default` |
| **B** データを「発生した日」で使う | 期末日で絞ると2か月ぶん未来を知る | [`scoring.py`](src/lookahead_demo/scoring.py) `DiscDate`(開示日)で絞る | `test_scoring.py::test_period_end_filter_sees_two_months_of_future` |
| **C** 母集団を「今の一覧」で作る | 途中で消えたものが丸ごと欠落する(生存者バイアス) | [`universe.py`](src/lookahead_demo/universe.py) 日付ごとに母集団を作り直す | `test_universe.py::test_delisted_code_is_present_before_it_disappears` |
| **D** しきい値を全期間から決める | 未来を見て決めたパラメータを過去の判断に使う | [`calibration.py`](src/lookahead_demo/calibration.py) 満了済みの過去だけから作る | `test_calibration.py::test_only_matured_dates_are_used` |

経路Bは特に見つけにくい。シグネチャに `as_of_date` があるので**対策済みに見える**のに、
絞る列を間違えているだけで漏れる。`naive.py` の `naive_score_by_period_end` がその形。

## 検出の一般形

先読みのテストには一般形がある。

> 1. 判断日 `d` を決める
> 2. `d` より**後**のデータを壊す
> 3. `d` 以前の出力が **1ビットも変わらない**ことを assert する

出力が変われば、未来のデータが判断に届いている。それが証拠になる。
この形の利点は**期待値を書かなくてよい**こと。「0.25 になるはず」ではなく「2つが一致するはず」
なので、ロジックを改良しても期待値の書き換えが発生しない。先読みテストだけが腐らずに残る。

[`tests/test_backtest_lookahead.py`](tests/test_backtest_lookahead.py) に、価格・開示・上場一覧の
3種類でこれをやっている。

## 通っているテストを、信じないための仕組み

ここがこのリポジトリの主眼。**通っている先読みテストは、それだけでは
「見張っている」のか「素通りしている」のか区別がつかない。**

### 1. わざと先読みを入れた対照実装に、同じテストを当てる

[`naive.py`](src/lookahead_demo/naive.py) は経路A・C・D をまとめて踏んだ実装。
[`test_detects_naive.py`](tests/test_detects_naive.py) が、同じ壊し方でこちらには
**差が出ること**を確認する。ここが落ちたら、疑うのはテストのほう。

### 2. 正しい実装をわざと壊して、テストが落ちることを確認する

```bash
.venv/bin/python tools/mutation_check.py
```

```
検出 OK     | 経路A: 開示日フィルタを外す -> tests/test_scoring.py が落ちた
検出 OK     | 経路D: ボラティリティを全期間から計算する -> tests/test_backtest_lookahead.py が落ちた
検出 OK     | 経路C: 未来のスナップショットも母集団に使う -> tests/test_universe.py が落ちた
検出 OK     | 鮮度チェックを外す(古い母集団を黙って使う) -> tests/test_universe.py が落ちた
検出 OK     | 経路D: 満了していない期間も混ぜる -> tests/test_calibration.py が落ちた
検出 OK     | 時系列の昇順チェックを外す -> tests/test_backtest_lookahead.py が落ちた
検出 OK     | 指摘1: 既存建玉を再調整せず、資本を二重計上する -> tests/test_guards.py が落ちた
検出 OK     | 指摘2: 定義できない銘柄にだけ生の 1.0 を混ぜる -> tests/test_guards.py が落ちた
検出 OK     | 指摘3: 歯止めを素の assert に戻す(-O で消える) -> tests/test_guards.py が落ちた
検出 OK     | 指摘9: 価格の鮮度上限を外す(古い終値を黙って使う) -> tests/test_guards.py が落ちた
検出 OK     | 指摘4の対偶: 消えた銘柄を打ち切らず黙って落とす -> tests/test_backtest_lookahead.py が落ちた
検出 OK     | 再検査1: 現金の歯止めを片側(負)だけに戻す -> tests/test_guards.py が落ちた
検出 OK     | 再検査2: max_staleness_days を価格側に渡さない -> tests/test_guards.py が落ちた
検出 OK     | 再検査3: 接頭辞に従わない読み込みの列挙を空にする -> tests/test_no_data_io.py が落ちた
検出 OK     | 実行ログ: 合否は出すが PASS 以外でも止めない -> tests/test_guard_log.py が落ちた
検出 OK     | 実行ログ: 空振り(評価回数0)でも PASS にする -> tests/test_guard_log.py が落ちた
検出 OK     | 実行ログ: 現金の歯止めを数えない(宣言だけ残って空振りする) -> tests/test_guard_log.py が落ちた

変異 17 件中 検出 17 件
```

1件でも素通りしたら、その経路は実質ノーガードになっている。CI で毎回回している。

**「落ちた」の判定は粗くしない。** `returncode != 0` を検出と数えると、pytest が
収集エラー(2)や使用法エラー(4)で終わっただけの変異が「検出 OK」になる。構文を
壊しただけの変異が、先読みテストを1件も落とさずに緑になってしまう。見るのは
「終了コードが**テスト失敗(1)**であること」と「**狙ったテストファイルが実際に
落ちたこと**」の両方。

### 3. 空振りしないことを、テスト自身に書く

比較対象が空なら、どんな比較も通る。

```python
assert normal.trade_log, "判断日以前に取引が発生していない。テストが空振りしている"
```

## 実装で効いた点

作りながら見つかったもので、書く前には分かっていなかったもの。

- **述語をもう一度書いた `assert` は、歯止めにならない。**
  絞り込みに使った条件をそのまま `assert` に書くと、構造上必ず真になる。歯止めのように見えて
  何も見張っていない。検査すべきは「どう絞ったか」ではなく「**結果に何が入ったか**」。
  [`calibration.py`](src/lookahead_demo/calibration.py) の `assert_no_unmatured` はそうしてある

- **壊し方が順位を変えないと、順位で選ぶ処理では差が出ない。**
  全銘柄を同じ向きに壊すと、スコアは激変しても順位は保たれる。出力は1ビットも変わらず、
  先読みがあっても検出できない。「未来を壊す」だけでは足りず、壊した結果が**出力に届く経路**まで
  作って初めてテストになる([`conftest.py`](tests/conftest.py) の `tamper_future_disclosures`)

- **消えたものを黙って落とすのが、最も検出しづらい先読み。**
  母集団から消えた対象は、消えた時点の値で明示的に打ち切る。絞り込みでは**何件落ちたか**を
  呼び出し側に返す。母集団を定義できない日は、隣の日で埋めずに落とす

- **データを読む責務をロジック側に置かない。**
  `src/` のどの関数もファイルも API も読まない。データを作るのは `tests/conftest.py` だけ。
  読む経路が無ければ、そこから先読みは入りようがない。
  [`test_no_data_io.py`](tests/test_no_data_io.py) が静的に検査している

- **禁止トークンを列挙する検査は、必ず遅れる。**
  `read_parquet` / `read_csv` / `read_json` / `read_sql` を並べても、pandas の
  読み込み関数は20種類ある。実測で `read_excel` `read_pickle` `read_hdf` ほか計14種が
  素通りしていた。列挙ではなく **AST を歩いて「形」で捕まえる**。`read_` で始まる
  呼び出しは名前を知らなくても捕まるし、AST は docstring やコメントを見ないので
  「`read_csv` と書いてある説明文」を誤検出することもない

- **本体に置く歯止めを、素の `assert` で書かない。**
  `python -O` / `PYTHONOPTIMIZE=1` で**消える**。テストも変異チェックも `-O` では
  回さないので、消えたことに気づく経路が無い。[`errors.py`](src/lookahead_demo/errors.py)
  の例外を `raise` する。同じ欠陥がまた入らないよう、`src/` に素の `assert` が
  1件も無いことを AST で横断的に検査している(`test_src_has_no_bare_assert`)

- **合否は、数字より先に出す。**
  検証を回すたびに `lookahead_guards: PASS (...)` の行を出し、**PASS でなければ
  結果を組み立てずに止める**([`guardlog.py`](src/lookahead_demo/guardlog.py))。
  良い数字を見てから検証すると、確かめる動機がそこで一番弱くなる。
  🔴 **ただし「PASS と出た」だけでは足りない。** 歯止めを一度も評価していない実行で
  PASS を出すのは、空振りする assert とまったく同じ形。宣言した歯止めの**評価回数**を
  数え、1つでも 0 なら判定は `VACUOUS` にして送出する。「作動しなかった」は正常だが、
  「評価されなかった」は異常

- **「対照実装に○○が無いこと」だけを見るテストは空振りする。**
  対照実装がその事象を**構造上起こさない**なら、母集団の作り方が正しかろうと
  間違っていようと必ず真になる。まず**正しい側にそれが実在すること**を確かめてから、
  対照側に無いことを言う。これは実測でしか分からない(reason 文字列の集合を出して判明した)

- **先読み以外の経路でも数字は壊れる。**
  既存の建玉を目標ウェイトに再調整せず、新規ぶんだけを純資産から建てると、
  資本を二重に数えることになる。現金が負に落ちて、借入の記録も無いまま暗黙の
  レバレッジが立つ。重みの合計は 1 なので、**建て直しのあと現金は 0 になる**はず ——
  これを不変条件として検査する(`test_capital_is_not_double_counted`)

## 構成

```
src/lookahead_demo/
  scoring.py       経路A・B。as_of_date を必須引数にし、開示日で絞る
  universe.py      経路C。日付ごとの母集団(PIT ユニバース)
  calibration.py   経路D。満了済みの過去だけから作る + 本体に置く歯止め
  backtest.py      上記を組んだ検証パイプライン
  naive.py         わざと先読みを入れた対照実装
  errors.py        本体に置く歯止め用の例外(-O で消えないように)
tests/
  conftest.py                  ダミーデータ。ここだけがデータを作る
  test_scoring.py              経路A・B
  test_universe.py             経路C
  test_calibration.py          経路D
  test_backtest_lookahead.py   パイプライン全体
  test_detects_naive.py        テストの検出力そのもの
  test_no_data_io.py           ロジック側にI/Oが無いことの静的検査(AST)
  test_guards.py               歯止めが作動することと、同じ欠陥が再発しないこと
tools/
  mutation_check.py            正しい実装を壊してテストが落ちるか確かめる
CLAUDE.md                      AI に守らせるためのハードルール
```

## AI に書かせるときは

会話で「気をつけて」と伝えても守られない。会話は流れるし、コンテキストからも落ちる。
仕組み側に置く。[`CLAUDE.md`](CLAUDE.md) がその実例。

## 解説記事

このリポジトリは記事「AIにバックテストを書かせると、構造的に先読みバイアスが入る」の付録です。

<記事URL>

## ライセンス

MIT
