# html-todo

会話で洗い出した ToDo を、依存のない単一ファイルの HTML チェックリストにして
ブラウザで開く Claude Code スキル。普段使っている Chrome (ログイン状態や拡張機能を含む) で開く。

項目ごとにチェックとコメントを付けられ、「Markdown でコピー」で両方をまとめて
チャットへ貼り戻せる。ToDo の消化だけでなく、提案や質問への回答をまとめて返す
レビューの往復にも使える。

仕様・オプション・判断基準の詳細は [SKILL.md](SKILL.md) に書いてある。
この README は、何ができるか・導入・基本の使い方を扱う。

## できること

| 機能 | 内容 |
|---|---|
| チェックリスト | セクション別の件数、全体の進捗バー、担当バッジ、「完了を隠す」フィルタ |
| コメント | 項目ごとに自由記述。入力のたびに保存される |
| 判定用のリスト | 仕様に `"decision": true` を書くと、チェックの代わりに YES / NO のボタンになる。NO も判定済みとして進捗に数える |
| Markdown でコピー | チェック状態とコメントをまとめてクリップボードへ。コメントは項目の下に `>` の引用で出る |
| 状態の保存 | チェックは `localStorage` と URL ハッシュ、コメントは `localStorage` に保存する |
| 作り直し | 項目を足す・消す・並べ替える、リスト名を変える、のいずれでもチェックとコメントは残る |
| 印刷 | A4 で、ボタン類と空のコメント欄を出さずに印刷できる |
| 表示 | ライト / ダークはブラウザの設定に合わせる |

生成した HTML には仕様が埋め込まれていて、元の仕様 JSON が無くても生成物から作り直せる。

## 前提

| 項目 | 条件 |
|---|---|
| OS | Windows (Chrome はレジストリと標準のインストール先から探す) |
| Python | 3.x。標準ライブラリのみで動き、追加パッケージは要らない |
| Chrome | 任意。見つからなければ既定のブラウザで開く |
| Claude Code | スキルとして使う場合のみ |

## 導入

リポジトリを個人スキルの配置先にクローンする。

```powershell
git clone https://github.com/unnowataru/html-todo.git "$env:USERPROFILE\.claude\skills\html-todo"
```

`%USERPROFILE%\.claude\skills\html-todo\SKILL.md` のように、`SKILL.md` がフォルダの直下に来ていれば正しい。Claude Code を起動し直すと、
スキル一覧に `html-todo` が出る。

スキルを編集しながら使う場合は、リポジトリを別の場所に置いて junction でつなぐ。
編集がそのまま反映される。

```powershell
git clone https://github.com/unnowataru/html-todo.git C:\src\html-todo
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\html-todo" -Target "C:\src\html-todo"
```

`C:\src\html-todo` は例。クローンした場所に読み替える。

## 使い方

Claude Code では「ToDo を HTML で」「チェックリストにして」のように頼むと、
スキルが仕様 JSON を書いて生成し、Chrome で開く。

仕様 JSON は、リスト名・セクション・項目 (題、補足、担当) を書いた JSON ファイル。
これを元に HTML を生成する。

手で動かす場合は、このリポジトリのフォルダで次のように実行する。

```bash
# 仕様 JSON から生成して Chrome で開く (仕様ファイルと同じフォルダに同名の .html を書く)
python scripts/build-todo.py examples/spec-minimal.json

# 生成物から仕様を取り出し、編集して作り直す
python scripts/build-todo.py todo.html --extract > todo.json
python scripts/build-todo.py todo.json -o todo.html
```

Chrome を開かせたくないときは `--no-open` か `HTML_TODO_NO_OPEN=1` を付ける。
仕様 JSON の書き方は `examples/spec-minimal.json` と SKILL.md を参照する。

## grilling スキルと組み合わせる

[grilling](https://github.com/mattpocock/skills) (Matt Pocock 作) も入れておくと、
html-todo をより効果的に使える。grilling は、計画や設計の判断を
推奨回答付きの質問としてまとめて出すスキル。1 回に複数の質問が並ぶので、
チャットで 1 問ずつ答えると手間がかかる。

html-todo はその質問の一覧をチェックリストに変換する。

- チェックを付けた質問は、推奨回答をそのまま採用したことになる
- 別の答えや条件があるときは、その質問のコメント欄に書く
- 書き終えたら「Markdown でコピー」で、全部の回答をまとめて貼り戻す

### grilling の導入

grilling の紹介ページ ([skills.sh](https://www.skills.sh/mattpocock/skills/grilling)) に
載っている方法で入れる。Node.js (`npx`) が要る。

```bash
npx skills add https://github.com/mattpocock/skills --skill grilling
```

手で入れる場合は、配布元リポジトリの `skills/productivity/grilling/SKILL.md` を
`%USERPROFILE%\.claude\skills\grilling\SKILL.md` に置く。

### 変換のしかた

grilling が出した質問の文面をファイルに保存し、`scripts/grill-to-spec.py` に渡す。

```bash
# 1 回目の質問から一覧を作って開く
python scripts/grill-to-spec.py round1.md --title "○○の設計" --html grill.html

# 2 回目以降は前の生成物に足す。前の質問のチェックとコメントは残る
python scripts/grill-to-spec.py round2.md --base grill.html --html grill.html
```

2 回目の質問の番号が Q1 から振り直されていると、前の質問と ID が重なって止まる。
そのときは `--id-prefix r2-` を付けて区別する。

grilling の質問の書式が変わって読み取れないときは、終了コード 2 で止まり、
空の一覧は作らない。その場合も、仕様 JSON を手で書けば同じ一覧を作れる。

## 動作確認

```bash
python tests/smoke.py
```

ブラウザは使わない。`scripts/` か `assets/` を変更したら実行する。grilling の
`SKILL.md` を更新したときも実行する (質問の書式例を実際に変換して確かめるため)。

チェック操作・コメント入力・再読み込み後の復元・クリップボードは、このテストでは
確かめられない。実際のブラウザで確認する。

## 構成

| パス | 内容 |
|---|---|
| `SKILL.md` | スキル本体。仕様と判断基準 |
| `scripts/build-todo.py` | 仕様 JSON から HTML を生成し、Chrome で開く |
| `scripts/grill-to-spec.py` | grilling の質問の文面を仕様 JSON に変換する |
| `assets/template.html` | 生成のひな形。画面の JavaScript はここにある |
| `examples/spec-minimal.json` | 最小の仕様の例 |
| `tests/smoke.py` | 回帰テスト |
| `tests/fixtures/grilling-round.md` | 変換テスト用の質問の文面 |

生成した `.html` は `.gitignore` で追跡しない。仕様から作り直せるうえ、
進捗の記録が 2 か所にあると古い方を見てしまうため。

## ライセンス

MIT License。全文は [LICENSE](LICENSE) にある。

