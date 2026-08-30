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
37 passed
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
検出 OK  | 経路A: 開示日フィルタを外す
検出 OK  | 経路D: ボラティリティを全期間から計算する
検出 OK  | 経路C: 未来のスナップショットも母集団に使う
検出 OK  | 鮮度チェックを外す(古い母集団を黙って使う)
検出 OK  | 経路D: 満了していない期間も混ぜる
検出 OK  | 時系列の昇順チェックを外す

変異 6 件中 検出 6 件
```

1件でも素通りしたら、その経路は実質ノーガードになっている。CI で毎回回している。

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

## 構成

```
src/lookahead_demo/
  scoring.py       経路A・B。as_of_date を必須引数にし、開示日で絞る
  universe.py      経路C。日付ごとの母集団(PIT ユニバース)
  calibration.py   経路D。満了済みの過去だけから作る + 本体に置く歯止め
  backtest.py      上記を組んだ検証パイプライン
  naive.py         わざと先読みを入れた対照実装
tests/
  conftest.py                  ダミーデータ。ここだけがデータを作る
  test_scoring.py              経路A・B
  test_universe.py             経路C
  test_calibration.py          経路D
  test_backtest_lookahead.py   パイプライン全体
  test_detects_naive.py        テストの検出力そのもの
  test_no_data_io.py           ロジック側にI/Oが無いことの静的検査
tools/
  mutation_check.py            正しい実装を壊してテストが落ちるか確かめる
CLAUDE.md                      AI に守らせるためのハードルール
```

## AI に書かせるときは

会話で「気をつけて」と伝えても守られない。会話は流れるし、コンテキストからも落ちる。
仕組み側に置く。[`CLAUDE.md`](CLAUDE.md) がその実例。

## 解説記事

このリポジトリは記事「AIにバックテストを書かせると、たいてい先読みバイアスが入る」の付録です。

<記事URL>

## ライセンス

MIT
