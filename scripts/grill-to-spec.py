#!/usr/bin/env python3
"""grilling のラウンド文面を html-todo の仕様 JSON にする。

    python scripts/grill-to-spec.py round.md --title "○○の設計" -o round.json
    python scripts/grill-to-spec.py round.md --base prev.html --html prev.html

読むのは grilling スキル (https://github.com/mattpocock/skills 、MIT License、
作者 Matt Pocock) が 1 ラウンドで出す文面。下の書式例はその SKILL.md から引いた:

    ❓ **Q1** - **<question title>**: <question body ...>

    ➡️ <recommended answer>

    ---

    ❓ **Q2** - ...

各質問を 1 項目にする。

    title  = "Q1 <question title>"
    detail = <question body>            (複数行のまま)
    note   = "➡️ 推奨: <recommended answer>" (1 行に潰す)
    id     = "q1"                        (--id-prefix で前置きできる)

チェック = 推奨をそのまま採用。別の答え・条件・質問はコメント欄に書き、
「Markdown でコピー」で貼り戻す。

grilling の書式に依存するのはこのファイルだけ。上流の書式が変われば直すのはここで、
build-todo.py と生成物の形は変わらない。認識できなければ黙って空を出さず、
終了コード 2 で止まる。その場合は仕様 JSON を手で書けば同じ生成物が得られる。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build-todo.py")

# 見出し行。絵文字は無くてもよい。区切りはハイフン類かコロン。
#   ❓ **Q1** - **title**: body の 1 行目
HEAD_RE = re.compile(
    r"^\s*(?:❓\s*)?\*\*Q(?P<num>\d+)\*\*\s*[-–—:]\s*"
    r"\*\*(?P<title>.+?)\*\*\s*:?\s*(?P<rest>.*)$"
)
# 太字の中に番号と題を一緒に入れた変形。**Q1 - title**: body
HEAD_LOOSE_RE = re.compile(
    r"^\s*(?:❓\s*)?\*\*Q(?P<num>\d+)\s*[-–—:]\s*(?P<title>[^*]+?)\*\*\s*:?\s*(?P<rest>.*)$"
)
# 推奨回答の行。➡️ (異体字セレクタ有無) と → を受ける。
REC_RE = re.compile(r"^\s*(?:➡️?|→|➜|=>)\s*(?P<rest>.*)$")
SEP_RE = re.compile(r"^\s*-{3,}\s*$")
# 質問の見出しに見えるのに HEAD_RE で読めない行。黙って捨てると項目が欠ける
ORPHAN_RE = re.compile(r"^\s*(?:❓|\*\*Q\d)")

DEFAULT_SUBTITLE = (
    "<strong>チェック = 推奨案 (➡️) をそのまま採用</strong>。別の答え・条件付き・質問は"
    "右端の「コメント」に。終わったら「Markdown でコピー」でチャットに貼り戻してください。"
)


class ParseError(Exception):
    pass


def parse_round(text):
    """ラウンド文面を [{num, title, body, rec}] にする。認識できなければ空リスト。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    questions = []
    orphans = []
    cur = None
    mode = None  # "body" / "rec"

    def close():
        nonlocal cur, mode
        if cur is not None:
            cur["body"] = "\n".join(cur["body"]).strip()
            cur["rec"] = " ".join(" ".join(cur["rec"]).split())
            questions.append(cur)
        cur, mode = None, None

    for line in text.split("\n"):
        m = HEAD_RE.match(line) or HEAD_LOOSE_RE.match(line)
        if m:
            close()
            cur = {
                "num": int(m.group("num")),
                "title": " ".join(m.group("title").split()),
                "body": [],
                "rec": [],
            }
            mode = "body"
            rest = m.group("rest").strip()
            if rest:
                cur["body"].append(rest)
            continue
        if cur is None:
            if ORPHAN_RE.match(line):
                orphans.append(line.strip())
            continue  # 前置きは捨てる
        if SEP_RE.match(line):
            close()
            continue
        r = REC_RE.match(line)
        if r and mode == "body":
            mode = "rec"
            cur["rec"].append(r.group("rest"))
            continue
        cur[mode].append(line)
    close()
    if orphans:
        raise ParseError(
            "質問の見出しに見えるが読めない行がある (書式が変わったか崩れている):\n  "
            + "\n  ".join(orphans)
        )
    return questions


def to_items(questions, id_prefix="", owner=None):
    items = []
    for q in questions:
        it = {
            "id": "%sq%d" % (id_prefix, q["num"]),
            "title": "Q%d %s" % (q["num"], q["title"]),
        }
        if owner:
            it["owner"] = owner
        if q["body"]:
            it["detail"] = q["body"]
        if q["rec"]:
            it["note"] = "➡️ 推奨: " + q["rec"]
        items.append(it)
    return items


def load_build_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_todo", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_spec(questions, title=None, base=None, section=None, round_no=None,
              id_prefix="", owner=None):
    """質問一覧から仕様 JSON を組む。base があればそこへセクションを足す。"""
    if not questions:
        raise ParseError("質問を 1 つも認識できなかった")

    if base is not None:
        spec = json.loads(json.dumps(base, ensure_ascii=False))  # deep copy
        spec.setdefault("sections", [])
        if title:
            spec["title"] = title
    else:
        if not title:
            raise ParseError("--title が要る (--base が無いとき)")
        spec = {"title": title, "subtitle": DEFAULT_SUBTITLE, "sections": []}

    if round_no is None:
        round_no = len(spec["sections"]) + 1
    if not section:
        section = "ラウンド %d" % round_no

    items = to_items(questions, id_prefix, owner)

    existing_ids = {
        (it.get("id") if isinstance(it, dict) else None)
        for s in spec["sections"] for it in s.get("items", [])
    }
    dup = [it["id"] for it in items if it["id"] in existing_ids]
    if dup:
        raise ParseError(
            "既存の項目と id が重なる: %s。番号が続いていないなら --id-prefix r%d- のように前置きする"
            % (", ".join(dup), round_no)
        )

    spec["sections"].append({"name": section, "items": items})
    return spec


def main():
    # 入出力を UTF-8 に固定する。Windows の既定 (cp932) のままだと ➡️ を書けずに落ちる。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="grilling のラウンド文面を html-todo の仕様 JSON にする。",
    )
    ap.add_argument("round", help="ラウンド文面のファイル。- で標準入力")
    ap.add_argument("--title", help="リスト名。--base が無いときは必須")
    ap.add_argument("--base", help="足し先の仕様 JSON か生成済み HTML。前のラウンドに続ける")
    ap.add_argument("--section", help="セクション名 (既定: ラウンド N)")
    ap.add_argument("--round", type=int, dest="round_no", help="ラウンド番号 (既定: 既存セクション数 + 1)")
    ap.add_argument("--id-prefix", default="", help="項目 id の前置き。番号が振り直されたときに使う")
    ap.add_argument("--owner", help="全項目に付ける担当キー (owners に定義済みのもの)")
    ap.add_argument("-o", "--out", help="仕様 JSON の出力先 (既定: 標準出力)")
    ap.add_argument("--html", help="続けて build-todo.py で HTML まで作る。その出力先")
    ap.add_argument("--no-open", action="store_true", help="--html のとき Chrome を開かない")
    args = ap.parse_args()

    if args.round == "-":
        text = sys.stdin.read()
    else:
        with io.open(args.round, encoding="utf-8") as f:
            text = f.read()

    bt = load_build_module()
    base = None
    if args.base:
        try:
            base = bt.load_spec(args.base)
        except bt.SpecError as e:
            raise SystemExit(str(e))

    try:
        questions = parse_round(text)
    except ParseError as e:
        raise SystemExit(str(e))
    if not questions:
        head = "\n".join(text.strip().split("\n")[:6])
        raise SystemExit(
            "grilling の書式を認識できなかった (❓ **Q1** - **題**: ... の行が無い)。\n"
            "上流の書式が変わったなら scripts/grill-to-spec.py の HEAD_RE を直す。\n"
            "急ぐなら仕様 JSON を手で書いて build-todo.py に渡す。\n"
            "--- 先頭 ---\n" + head
        )

    try:
        spec = make_spec(questions, args.title, base, args.section, args.round_no,
                         args.id_prefix, args.owner)
        bt.normalize(spec)  # ここで弾けば、build 側で落ちる前に何が悪いか出る
    except (ParseError, bt.SpecError) as e:
        raise SystemExit(str(e))

    out_json = json.dumps(spec, ensure_ascii=False, indent=2)
    print("questions=%d section=%s" % (len(questions), spec["sections"][-1]["name"]),
          file=sys.stderr)

    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(out_json + "\n")
        print("wrote: %s" % os.path.abspath(args.out), file=sys.stderr)
    elif not args.html:
        print(out_json)

    if args.html:
        spec_path = args.out
        if not spec_path:
            spec_path = os.path.splitext(os.path.abspath(args.html))[0] + ".json"
            with io.open(spec_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(out_json + "\n")
            print("wrote: %s" % spec_path, file=sys.stderr)
        cmd = [sys.executable, BUILD, spec_path, "-o", args.html]
        if args.no_open:
            cmd.append("--no-open")
        r = subprocess.run(cmd)
        raise SystemExit(r.returncode)


if __name__ == "__main__":
    main()
