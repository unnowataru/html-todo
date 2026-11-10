#!/usr/bin/env python3
"""html-todo のスモークテスト。ブラウザ不要。

    python tests/smoke.py

生成が壊れていないこと、状態が壊れる形の入力を弾くこと、
作り直してもチェックが残ることを見る。

ビルダーは直接 import して、どの検査で弾かれたかまで見る
(終了コードが非ゼロなだけだと、テンプレート不在のクラッシュと区別が付かない)。

ここで見られるのは生成物の中身まで。チェック操作やリロードの挙動は
実ブラウザでしか確かめられない。
"""
import copy
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "scripts", "build-todo.py")
EXAMPLE = os.path.join(ROOT, "examples", "spec-minimal.json")
GRILL = os.path.join(ROOT, "scripts", "grill-to-spec.py")
FIXTURE_ROUND = os.path.join(ROOT, "tests", "fixtures", "grilling-round.md")
# grilling スキル本体の所在。上流の書式例をここから読んで、変換が追随しているかを見る。
GRILLING_SKILL_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".claude", "skills", "grilling", "SKILL.md"),
]

_spec = importlib.util.spec_from_file_location("build_todo", BUILD)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(name)


def rejects(name, spec):
    """仕様エラーとして弾かれることを見る。他の例外は素通しさせない。"""
    try:
        bt.build(spec)
    except bt.SpecError:
        check(name, True)
        return
    except Exception as e:  # noqa: BLE001 - 想定外の落ち方は失敗として見せる
        check(name, False, "SpecError ではなく %s: %s" % (type(e).__name__, e))
        return
    check(name, False, "弾かれなかった")


def ids_by_title(spec):
    return {it["title"]: it["id"] for s in spec["sections"] for it in s["items"]}


def main():
    with tempfile.TemporaryDirectory() as d:
        run(d)
        run_detail()
        run_decision()
        run_grill(d)

    print()
    if fails:
        print("FAILED: " + ", ".join(fails))
        sys.exit(1)
    print("すべて通過")


def run(d):
    with io.open(EXAMPLE, encoding="utf-8") as f:
        base = json.load(f)

    html_text, doc = bt.build(base)
    check("例の仕様がビルドできる",
          len(doc["sections"]) == 2 and sum(len(s["items"]) for s in doc["sections"]) == 4)

    # ---- 生成物の形 ----
    check("CRLF が混ざらない", "\r" not in html_text)
    check("BOM が付かない", not html_text.startswith("\ufeff"))
    check("外部リソースを読まない",
          not re.search(r'(src|href)\s*=\s*["\']https?://', html_text)
          and not re.search(r"@import|url\(\s*[\"']?https?:", html_text))

    # コメント欄。実際の入力・復元は headless Chrome で見る (SKILL.md の手順)。
    # ここでは、部品が抜け落ちていないことだけを見る。
    check("項目ごとのコメント欄がある", 'className = "cmt hidden"' in html_text)
    check("コメントは localStorage の別キーに置く", 'KEY + ":notes"' in html_text)
    check("コメントを URL ハッシュに載せない",
          "notes" not in html_text[html_text.index("function hashSave"):
                                   html_text.index("// 読み込みの優先順位")])
    check("Markdown にコメントを引用で出す", '"  > " + ln' in html_text)
    check("Markdown 生成が関数に切り出されている", "function buildMarkdown()" in html_text)

    embedded = bt.extract_spec(html_text)
    check("仕様が生成物に埋め込まれる", embedded is not None)
    check("埋め込んだ仕様が正規化済みと一致する", embedded == doc)
    ids = [it["id"] for s in embedded["sections"] for it in s["items"]]
    check("ID が一意", len(ids) == len(set(ids)))
    check("担当なしは null で入る",
          None in [it["owner"] for s in embedded["sections"] for it in s["items"]])

    # ---- 更新の耐性 ----
    check("正規化が冪等 (埋め込みを読み直しても変わらない)", bt.normalize(embedded) == doc)

    renamed = copy.deepcopy(embedded)
    renamed["title"] = "まったく違うリスト名"
    doc2 = bt.build(renamed, embedded["storageKey"])[1]
    check("リスト名を変えても保存キーが変わらない", doc2["storageKey"] == doc["storageKey"])
    check("リスト名を変えても項目 ID が変わらない", ids_by_title(doc2) == ids_by_title(doc))

    retitled = copy.deepcopy(embedded)
    retitled["sections"][0]["items"][0]["title"] = "項目名を書き換えた"
    doc3 = bt.build(retitled, embedded["storageKey"])[1]
    check("埋め込み仕様を土台に項目名を変えても ID が残る",
          doc3["sections"][0]["items"][0]["id"] == doc["sections"][0]["items"][0]["id"])

    grew = copy.deepcopy(embedded)
    grew["sections"][0]["items"].insert(0, {"owner": "me", "title": "あとから足した項目"})
    del grew["sections"][1]["items"][0]
    doc4 = bt.build(grew, embedded["storageKey"])[1]
    old_ids = ids_by_title(doc)
    new_ids = ids_by_title(doc4)
    check("項目を増減しても残った項目の ID は変わらない",
          all(new_ids[t] == old_ids[t] for t in new_ids if t in old_ids))

    lines = bt.diff_specs(doc, doc4)
    check("差分に追加が出る", any("追加" in x for x in lines), " / ".join(lines))
    check("差分に削除が出る", any("削除" in x for x in lines), " / ".join(lines))
    check("差分に改名が出る", any("改名" in x for x in bt.diff_specs(doc, doc3)))
    check("差分にリスト名変更が出る", any("リスト名" in x for x in bt.diff_specs(doc, doc2)))
    check("変化がなければ差分は空", bt.diff_specs(doc, doc) == [])

    # ---- 改名の警告 (id なしの入力で消える項目があるときだけ出す) ----
    def warns(raw, new_doc):
        return bt.rename_warning(raw, doc, new_doc) is not None

    hand_renamed = copy.deepcopy(base)
    hand_renamed["sections"][0]["items"][0]["title"] = "手書きで改名した"
    check("手書き仕様の改名で警告する", warns(hand_renamed, bt.build(hand_renamed)[1]))

    with_id = copy.deepcopy(base)
    with_id["sections"][0]["items"][0]["id"] = doc["sections"][0]["items"][0]["id"]
    with_id["sections"][0]["items"][0]["title"] = "id 付きで改名した"
    check("id を明示した改名では警告しない", not warns(with_id, bt.build(with_id)[1]))

    check("--extract 由来の仕様では警告しない",
          not warns(embedded, bt.build(embedded)[1]))

    only_add = copy.deepcopy(base)
    only_add["sections"][0]["items"].append({"owner": "me", "title": "足しただけ"})
    check("追加だけでは警告しない", not warns(only_add, bt.build(only_add)[1]))

    only_del = copy.deepcopy(base)
    del only_del["sections"][0]["items"][0]
    check("削除だけでは警告しない", not warns(only_del, bt.build(only_del)[1]))

    reordered = copy.deepcopy(base)
    reordered["sections"][0]["items"].reverse()
    check("並べ替えだけでは警告しない", not warns(reordered, bt.build(reordered)[1]))

    check("storageKey 明示は継承より優先する",
          bt.build(dict(base, storageKey="explicit-key"), "inherited")[1]["storageKey"]
          == "explicit-key")

    # ---- ID の安定性 (title 由来) ----
    edited = copy.deepcopy(base)
    edited["sections"][0]["items"].insert(0, {"owner": "me", "title": "あとから足した項目"})
    edited["sections"][1]["items"].reverse()
    m1 = ids_by_title(doc)
    m2 = ids_by_title(bt.build(edited)[1])
    check("仕様 JSON を編集しても既存項目の ID が変わらない", all(m1[t] == m2.get(t) for t in m1))

    keys = []
    for t in ("プロジェクト A の作業", "プロジェクト B の作業"):
        sp = copy.deepcopy(base)
        sp["title"] = t
        keys.append(bt.build(sp)[1]["storageKey"])
    check("初回はタイトルが違えば保存キーも違う", keys[0] != keys[1], " / ".join(keys))

    # ---- 埋め込みの安全性 ----
    inject = copy.deepcopy(base)
    inject["title"] = "終端 </script><script>alert(1)</script> 入り"
    inject["sections"][0]["items"][0]["note"] = "</SCRIPT >"
    out = bt.build(inject)[0]
    check("注入した script タグが生きた形で残らない", "<script>alert(1)</script>" not in out)
    check("</script> がエスケープされて入る", "\\u003c/script" in out)
    check("エスケープしても値は元に戻る",
          bt.extract_spec(out)["title"] == inject["title"])

    ph = copy.deepcopy(base)
    ph["title"] = "確認 __FOOTER__ と __SPEC_JSON__"
    ph["footer"] = "フッタ本文"
    out = bt.build(ph)[0]
    check("値の中のプレースホルダを二重置換しない",
          "__FOOTER__" in bt.extract_spec(out)["title"]
          and "<footer>フッタ本文</footer>" in out)

    sk = copy.deepcopy(base)
    sk["storageKey"] = 'quote"and\\backslash'
    check("storageKey の引用符で壊れない",
          bt.extract_spec(bt.build(sk)[0])["storageKey"] == sk["storageKey"])

    # ---- 正規化 ----
    nl = copy.deepcopy(base)
    nl["sections"][0]["items"][0]["note"] = "1 行目\n2 行目"
    note = bt.build(nl)[1]["sections"][0]["items"][0]["note"]
    check("note の改行は 1 行に潰れる", "\n" not in note and note == "1 行目 2 行目")

    same = copy.deepcopy(base)
    same["sections"][1]["name"] = same["sections"][0]["name"]
    check("同名セクションも両方残る", len(bt.build(same)[1]["sections"]) == 2)

    # ---- 壊れる入力 ----
    dup = copy.deepcopy(base)
    dup["sections"][1]["items"].append({"title": dup["sections"][1]["items"][0]["title"]})
    rejects("同名の項目を弾く", dup)

    bad = copy.deepcopy(base)
    bad["sections"][0]["items"][0]["owner"] = "nobody"
    rejects("未定義の担当を弾く", bad)

    rejects("空のリストを弾く", {"title": "空", "sections": []})

    notitle = copy.deepcopy(base)
    notitle["sections"][0]["items"][0] = {"owner": "me"}
    rejects("title なしの項目を弾く", notitle)

    no_doc_title = copy.deepcopy(base)
    del no_doc_title["title"]
    rejects("文書の title なしを弾く", no_doc_title)

    for bad_id in (".", "a&b", "a.b", 42):
        badid = copy.deepcopy(base)
        badid["sections"][0]["items"][0]["id"] = bad_id
        rejects("ハッシュを壊す id を弾く (%r)" % (bad_id,), badid)

    hl = copy.deepcopy(base)
    hl["sections"][0]["highlight"] = "false"
    rejects("highlight の文字列を弾く", hl)

    wrong = copy.deepcopy(base)
    wrong["sections"] = {"name": "オブジェクト"}
    rejects("sections が配列でないものを弾く", wrong)

    wrong = copy.deepcopy(base)
    wrong["owners"] = ["me", "you"]
    rejects("owners が配列のものを弾く", wrong)

    check("仕様の無い HTML を仕様として渡すと弾く", extract_fails(d))

    # ---- CLI ----
    sp = os.path.join(d, "spec.json")
    with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(base, ensure_ascii=False))
    out_path = os.path.join(d, "out.html")

    r = cli(sp, "-o", out_path, "--no-open")
    check("CLI がビルドできる", r.returncode == 0, r.stderr.strip()[:160])
    check("--no-open ではブラウザを開かない", "opened in" not in r.stdout, r.stdout.strip()[:160])
    check("CLI の出力先に書かれる", os.path.exists(out_path))

    r = cli(sp, "-o", out_path, "--no-open")
    check("同じ出力先への 2 回目は更新として出る",
          "更新:" in r.stdout and "変化なし" in r.stdout, r.stdout.strip()[:200])

    r = cli(out_path, "--extract")
    check("CLI が生成物から仕様を取り出せる",
          r.returncode == 0 and json.loads(r.stdout)["storageKey"] == doc["storageKey"],
          r.stderr.strip()[:160])

    r = cli(out_path, "-o", out_path, "--no-open")
    check("生成物そのものを仕様として渡せる", r.returncode == 0, r.stderr.strip()[:160])

    r = cli(sp, "-o", out_path, env_no_open=True)
    check("HTML_TODO_NO_OPEN=1 でもブラウザを開かない",
          r.returncode == 0 and "opened in" not in r.stdout, r.stdout.strip()[:160])

    r = cli(sp, "-o", out_path, "--no-open",
            "--chrome-path", os.path.join(d, "no-such-chrome.exe"))
    check("存在しない --chrome-path でも生成は成功する", r.returncode == 0, r.stderr.strip()[:160])
    # 起動そのものは実行しない (テストでブラウザを開かせない)。検出だけ単体で見る。
    try:
        bt.find_chrome(os.path.join(d, "no-such-chrome.exe"))
        check("存在しない chrome.exe の指定を弾く", False, "弾かれなかった")
    except bt.SpecError:
        check("存在しない chrome.exe の指定を弾く", True)

    bad_json = os.path.join(d, "broken.json")
    with io.open(bad_json, "w", encoding="utf-8", newline="\n") as f:
        f.write("{ not json")
    r = cli(bad_json, "--no-open")
    check("壊れた JSON を仕様エラーとして弾く",
          r.returncode != 0 and "JSON" in r.stderr, r.stderr.strip()[:160])

    # ---- ブラウザ探索 ----
    os.environ.pop("HTML_TODO_CHROME", None)
    found = bt.find_chrome()
    check("Chrome の検出が壊れていない", found is None or os.path.isfile(found), str(found))
    print("      検出した Chrome: %s" % found)


def run_detail():
    """items[].detail (複数行の本文)。改行を残し、CRLF は LF に揃える。"""
    spec = {"title": "detail", "sections": [{"items": [
        {"title": "A", "detail": "1 行目\r\n\r\n選択肢:\r\n- x\r\n- y\r\n"},
        {"title": "B"},
    ]}]}
    html_text, doc = bt.build(spec)
    det = doc["sections"][0]["items"][0]["detail"]
    check("detail の改行が残る", det == "1 行目\n\n選択肢:\n- x\n- y", repr(det))
    check("detail の無い項目は空文字で入る", doc["sections"][0]["items"][1]["detail"] == "")
    check("detail が描画される", 'className = "detail"' in html_text)
    check("detail は Markdown コピーに載せない",
          "detail" not in html_text[html_text.index("function buildMarkdown"):
                                    html_text.index('getElementById("copyMd")')])
    rejects("detail に文字列以外を弾く",
            {"title": "x", "sections": [{"items": [{"title": "A", "detail": ["a"]}]}]})


def run_decision():
    """判定用リスト (decision)。画面の挙動は headless Chrome で別に見る。"""
    base = {"title": "判定", "sections": [{"name": "s", "items": ["a", "b"]}]}
    _, plain = bt.build(base)
    check("decision を書かないリストに decision キーが入らない", "decision" not in plain)

    dec = dict(base, decision=True)
    html_text, doc = bt.build(dec)
    check("decision: true が正規化後に残る", doc.get("decision") is True)
    check("decision が生成物の埋め込みに残る",
          bt.extract_spec(html_text).get("decision") is True)
    check("decision 付きでも正規化が冪等", bt.normalize(doc) == doc)
    check("NO は localStorage の別キーに置く", 'KEY + ":no"' in html_text)
    check("Markdown に YES / NO / 未判定 を出す",
          all(s in html_text for s in ('"YES: "', '"NO: "', '"未判定: "')))

    rejects("decision の文字列を弾く", dict(base, decision="true"))

    on = bt.diff_specs(plain, doc)
    off = bt.diff_specs(doc, plain)
    check("差分に判定用への切り替えが出る", any("判定用にする" in x for x in on), " / ".join(on))
    check("差分に判定用の解除が出る", any("判定用をやめる" in x for x in off), " / ".join(off))


def run_grill(d):
    """grilling のラウンド文面 → 仕様 JSON の変換。"""
    _s = importlib.util.spec_from_file_location("grill_to_spec", GRILL)
    g = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(g)

    with io.open(FIXTURE_ROUND, encoding="utf-8") as f:
        text = f.read()
    qs = g.parse_round(text)
    check("見本のラウンドから質問を 3 つ読む", len(qs) == 3, str([q["num"] for q in qs]))
    check("前置き・後置きは項目にならない", all(q["num"] in (1, 2, 3) for q in qs))
    check("題は太字の中身だけ", qs[0]["title"] == "保存先をどこにするか", qs[0]["title"])
    check("本文の段落と改行が残る", "\n\n1. localStorage" in qs[0]["body"], repr(qs[0]["body"][:60]))
    check("推奨は 1 行に潰す", "\n" not in qs[1]["rec"] and qs[1]["rec"].startswith("残さない"))
    check("推奨なしは空", qs[2]["rec"] == "")

    spec = g.make_spec(qs, title="変換")
    _, doc = bt.build(spec)
    items = doc["sections"][0]["items"]
    check("id は q<番号>", [it["id"] for it in items] == ["q1", "q2", "q3"])
    check("title は Q<番号> + 題", items[0]["title"] == "Q1 保存先をどこにするか")
    check("推奨は note に「推奨:」で入る", items[1]["note"].startswith("➡️ 推奨: "))
    check("本文は detail に入る", items[0]["detail"].startswith("チェック状態を残す場所"))
    check("subtitle に判定の意味が書かれる", "チェック = 推奨案" in doc["subtitle"])

    # 次のラウンドを足す。前の項目の id と保存キーは動かない。
    round2 = g.parse_round("❓ **Q4** - **次の問い**: 本文\n\n➡️ 推奨\n")
    spec2 = g.make_spec(round2, base=doc)
    _, doc2 = bt.build(spec2, doc["storageKey"])
    check("--base で 2 つ目のセクションが増える",
          len(doc2["sections"]) == 2 and doc2["sections"][1]["name"] == "ラウンド 2")
    check("--base でも前の項目 ID は変わらない",
          ids_by_title(doc2).items() >= ids_by_title(doc).items())
    check("--base で保存キーが変わらない", doc2["storageKey"] == doc["storageKey"])
    try:
        g.make_spec(g.parse_round("❓ **Q1** - **重複**: x\n"), base=doc)
        check("番号が重なると止まる", False, "止まらなかった")
    except g.ParseError as e:
        check("番号が重なると止まる", "--id-prefix" in str(e))
    spec3 = g.make_spec(g.parse_round("❓ **Q1** - **重複**: x\n"), base=doc, id_prefix="r2-")
    check("--id-prefix で重なりを避けられる", spec3["sections"][1]["items"][0]["id"] == "r2-q1")

    check("絵文字なしの見出しも読む", len(g.parse_round("**Q7** - **題**: 本文\n")) == 1)
    check("太字を分けない見出しも読む", g.parse_round("❓ **Q8 - 題**: 本文\n")[0]["title"] == "題")
    check("認識できなければ空", g.parse_round("ただの文章\n- 箇条書き\n") == [])
    try:
        g.parse_round("❓ **Q1** - **良い**: x\n\n---\n\n❓ **Q2** 区切りの無い見出し\n")
        check("読めない見出しが混ざると止まる (黙って欠けさせない)", False, "止まらなかった")
    except g.ParseError as e:
        check("読めない見出しが混ざると止まる (黙って欠けさせない)", "Q2" in str(e))

    # 上流 grilling の書式例をそのまま読めるか。ここが落ちたら上流の書式が変わった。
    skill = next((p for p in GRILLING_SKILL_CANDIDATES if os.path.isfile(p)), None)
    if skill is None:
        print("SKIP  grilling SKILL.md が見つからない (書式の追随確認を飛ばす)")
    else:
        with io.open(skill, encoding="utf-8") as f:
            body = f.read()
        blocks = [b for b in re.findall(r"```[a-z]*\n(.*?)```", body, re.S) if "Q1" in b]
        check("grilling SKILL.md に書式例がある", bool(blocks), skill)
        if blocks:
            ex = g.parse_round(blocks[0])
            check("grilling の書式例を読める (上流の書式に追随している)",
                  len(ex) >= 2 and all(q["rec"] for q in ex), repr(ex)[:200])

    # CLI: 文面 → JSON → HTML まで一気通貫
    out_html = os.path.join(d, "grill.html")
    r = subprocess.run(
        [sys.executable, GRILL, FIXTURE_ROUND, "--title", "CLI", "--html", out_html, "--no-open"],
        capture_output=True, text=True, encoding="utf-8",
    )
    check("grill-to-spec の CLI が HTML まで作る", r.returncode == 0 and os.path.isfile(out_html),
          (r.stderr or r.stdout).strip()[:200])
    check("CLI は仕様 JSON も隣に残す", os.path.isfile(os.path.join(d, "grill.json")))
    r = subprocess.run(
        [sys.executable, GRILL, EXAMPLE, "--title", "x"],
        capture_output=True, text=True, encoding="utf-8",
    )
    check("認識できない入力は終了コード非ゼロで止まる",
          r.returncode != 0 and "認識できなかった" in r.stderr, r.stderr[:120])


def cli(*args, env_no_open=False):
    env = dict(os.environ)
    env["HTML_TODO_NO_OPEN"] = "1" if env_no_open else "0"
    return subprocess.run([sys.executable, BUILD] + list(args),
                          capture_output=True, text=True, encoding="utf-8", env=env)


def extract_fails(d):
    plain = os.path.join(d, "plain.html")
    with io.open(plain, "w", encoding="utf-8", newline="\n") as f:
        f.write("<!DOCTYPE html><title>ただの HTML</title>")
    try:
        bt.load_spec(plain)
    except bt.SpecError:
        return True
    return False


if __name__ == "__main__":
    main()
