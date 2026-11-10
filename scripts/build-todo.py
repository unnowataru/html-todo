#!/usr/bin/env python3
"""JSON の仕様から、チェックボックス付きの単一ファイル HTML を生成する。

使い方:
    python build-todo.py <spec.json> [-o <出力先.html>] [--no-open]

生成したファイルは既定で Chrome 本体 (普段使っているプロファイル) に開く。
依存なし (標準ライブラリのみ)。出力は BOM なし UTF-8 / LF。
"""
import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "assets", "template.html")

DEFAULT_OWNERS = {"me": "私", "you": "あなた", "other": "他"}

# 明示 ID に使える文字。状態は URL ハッシュに "." 区切りで載せるので、
# 区切り文字やクエリ記号を ID に許すと復元できなくなる。
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# 環境変数で上書きできる。CI や無人実行では HTML_TODO_NO_OPEN=1 を立てる。
ENV_CHROME = "HTML_TODO_CHROME"
ENV_NO_OPEN = "HTML_TODO_NO_OPEN"


class SpecError(Exception):
    """仕様 JSON が受け付けられない。利用者に見せるメッセージを持つ。"""


# --------------------------------------------------------------------------
# ブラウザ起動
# --------------------------------------------------------------------------

def find_chrome(explicit=None):
    """Chrome 本体の実行ファイルを探す。見つからなければ None。

    決め打ちパスだけに頼らない。配布形態 (ユーザー / システムインストール) で
    場所が変わり、片方だけ見て「未インストール」と誤判定するため。
    """
    explicit = explicit or os.environ.get(ENV_CHROME)
    if explicit:
        if not os.path.isfile(explicit):
            raise SpecError("指定された chrome.exe がない: %s" % explicit)
        return explicit

    if sys.platform == "win32":
        found = _chrome_from_registry()
        if found:
            return found
        for env_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if not base:
                continue
            path = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.isfile(path):
                return path

    return shutil.which("chrome") or shutil.which("google-chrome")


def _chrome_from_registry():
    """App Paths を引く。インストール先を問わず正を返すので最優先。"""
    try:
        import winreg
    except ImportError:
        return None

    key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    # ユーザーインストールがシステムインストールを覆うので HKCU を先に見る。
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as k:
                    path = winreg.QueryValueEx(k, "")[0].strip('"')
            except OSError:
                continue
            if path and os.path.isfile(path):
                return path
    return None


def open_in_browser(dest, chrome_path=None):
    """生成した HTML を開く。Chrome 本体 → 既定ブラウザ、の順。

    file:// URL は必ず as_uri() で組む。日本語や空白を含むパスでも壊れない。
    プロファイルは指定しない。--user-data-dir を渡すと普段のログイン状態とは
    別の Chrome になるため。

    起動要求を渡せたかどうかしか分からない。目的のタブが表示されたことの
    確認にはならない。
    """
    url = pathlib.Path(dest).resolve().as_uri()

    chrome = find_chrome(chrome_path)
    if chrome:
        try:
            subprocess.Popen([chrome, url], close_fds=True)
            print("opened in Chrome: %s" % chrome)
            return
        except OSError as e:
            print("Chrome を起動できなかった (%s)。既定ブラウザを試す。" % e, file=sys.stderr)

    if webbrowser.open(url, new=2, autoraise=True):
        print("Chrome が見つからないので既定ブラウザで開いた: %s" % url, file=sys.stderr)
        return

    # HTML は生成済み。開けなかっただけなので終了コードは変えない。
    print("HTML は生成済みだが、ブラウザを開けなかった。手で開くこと: %s" % url, file=sys.stderr)


# --------------------------------------------------------------------------
# 仕様の正規化
# --------------------------------------------------------------------------

def stable_id(text):
    """項目名から安定した ID を作る。

    再生成しても、項目名が変わらなければ ID は変わらない。
    = 既にチェック済みの項目は、リストを編集して作り直してもチェックが残る。
    """
    return "t" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "todo"


def _require_str(value, what):
    if not isinstance(value, str) or not value.strip():
        raise SpecError("%s は空でない文字列が要る: %r" % (what, value))
    return value


def normalize(spec, inherited_key=None):
    """仕様 JSON を検査し、テンプレートへ渡す形へ均す。

    ここで弾かないと、型違いが AttributeError や TypeError として出て、
    利用者には何が悪いのか分からないまま落ちる。

    inherited_key は、上書き先に既にある生成物から引き継ぐ保存キー。
    title を書き換えても保存キーが変わらないようにするために要る
    (変わると、そのリストのチェックが全部消える)。
    """
    if not isinstance(spec, dict):
        raise SpecError("仕様のトップレベルはオブジェクトが要る")

    title = _require_str(spec.get("title"), "title")

    owners = spec.get("owners")
    if owners is None:
        owners = DEFAULT_OWNERS
    if not isinstance(owners, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in owners.items()
    ):
        raise SpecError("owners は 文字列 → 文字列 のオブジェクトが要る")

    raw_sections = spec.get("sections")
    if not isinstance(raw_sections, list):
        raise SpecError("sections は配列が要る")

    sections = []
    seen = set()
    for sec in raw_sections:
        if not isinstance(sec, dict):
            raise SpecError("section はオブジェクトが要る: %r" % (sec,))
        raw_items = sec.get("items")
        if not isinstance(raw_items, list):
            raise SpecError("section '%s' の items は配列が要る" % sec.get("name"))

        items = []
        for it in raw_items:
            if isinstance(it, str):
                it = {"title": it}
            if not isinstance(it, dict):
                raise SpecError("項目は文字列かオブジェクトが要る: %r" % (it,))

            item_title = _require_str(it.get("title"), "項目の title")

            item_id = it.get("id")
            if item_id is None:
                item_id = stable_id(item_title)
            elif not isinstance(item_id, str) or not ID_RE.match(item_id):
                raise SpecError(
                    "id は英数字・ハイフン・アンダースコア 1〜80 文字が要る "
                    "(URL ハッシュの区切りと衝突するため): %r" % (item_id,)
                )
            if item_id in seen:
                raise SpecError("ID が重複している (title が同一か id 指定が衝突): %r" % item_title)
            seen.add(item_id)

            owner = it.get("owner")
            if owner is not None and owner not in owners:
                raise SpecError(
                    "owner '%s' が owners に定義されていない。定義済み: %s"
                    % (owner, ", ".join(sorted(owners)))
                )

            note = it.get("note", "")
            if not isinstance(note, str):
                raise SpecError("note は文字列が要る: %r" % (note,))

            detail = it.get("detail", "")
            if not isinstance(detail, str):
                raise SpecError("detail は文字列が要る: %r" % (detail,))

            items.append({
                "id": item_id,
                "owner": owner,
                "title": item_title,
                # note は 1 行として扱う。改行が入ると Markdown コピーが崩れる。
                "note": " ".join(note.split()),
                # detail は複数行の本文。改行は残す (質問の選択肢や前提を段落で書くため)。
                # Markdown コピーには載せない。貼り戻しは判定とコメントだけで足りる。
                "detail": detail.replace("\r\n", "\n").replace("\r", "\n").strip(),
            })

        if not items:
            continue

        highlight = sec.get("highlight", False)
        if not isinstance(highlight, bool):
            # bool("false") は True。文字列を黙って真扱いすると意図と逆になる。
            raise SpecError("highlight は true / false が要る: %r" % (highlight,))

        sections.append({
            "name": sec.get("name") or "",
            "highlight": highlight,
            "items": items,
        })

    if not sections:
        raise SpecError("項目のあるセクションが 1 つも無い")

    storage_key = spec.get("storageKey")
    if storage_key is not None:
        _require_str(storage_key, "storageKey")
    elif inherited_key:
        # 既にある生成物のキーを継ぐ。title を書き換えても状態が残る。
        storage_key = inherited_key
    else:
        # 初回だけ title から作る。以降は生成物に焼かれて動かない。
        # slugify だけだと日本語タイトルが全部同じ slug に潰れ、
        # 別のリスト同士が localStorage を共有して状態を上書きし合う。
        storage_key = (
            "todo-" + slugify(title) + "-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:6]
        )

    for key in ("subtitle", "footer"):
        if key in spec and not isinstance(spec[key], str):
            raise SpecError("%s は文字列が要る" % key)

    # 判定用のリスト。項目ごとに YES / NO を選び、進捗は判定済みの数で数える。
    decision = spec.get("decision", False)
    if not isinstance(decision, bool):
        raise SpecError("decision は true / false が要る: %r" % (decision,))

    # 戻り値は入力と同じ形にする。生成物へ埋め込んだものを、
    # そのまま次回の仕様として読み直せるようにするため (normalize は冪等)。
    doc = {
        "title": title,
        "subtitle": spec.get("subtitle", ""),
        "footer": spec.get("footer", ""),
        "owners": owners,
        "storageKey": storage_key,
        "sections": sections,
    }
    # 指定したときだけ載せる。通常のリストの生成物は、この機能の追加前と同じ中身になる。
    if decision:
        doc["decision"] = True
    return doc


# --------------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------------

def js_literal(value):
    """インライン <script> の中に置いても安全な JSON を作る。

    項目名に `</script>` が入ると、そこで script 要素が閉じて HTML が壊れる。
    U+2028 / U+2029 も古い JS では行終端として扱われる。
    どれも JSON のエスケープ表現に置き換えるだけなので、値そのものは変わらない。
    """
    out = json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")
    # U+2028 / U+2029 はソースに直接書かない (行終端として扱う道具があり、割れる)
    for cp in (0x2028, 0x2029):
        out = out.replace(chr(cp), "\\u%04x" % cp)
    return out


PLACEHOLDER = re.compile(r"__([A-Z_]+)__")


def substitute(tpl, values):
    """ひな形のプレースホルダを 1 パスで埋める。

    連鎖 replace だと、先に埋めた値の中にプレースホルダ名が含まれていた場合、
    後段の replace がそれを拾って二重置換する (title に `__FOOTER__` と書くだけで起きる)。
    1 パスなら埋めた値は再走査されない。
    置換は関数で返す。文字列で渡すとバックスラッシュや \\1 が解釈される。
    """
    missing = sorted(set(PLACEHOLDER.findall(tpl)) - set(values))
    if missing:
        raise SpecError("ひな形に未知のプレースホルダがある: %s" % ", ".join(missing))
    unused = sorted(set(values) - set(PLACEHOLDER.findall(tpl)))
    if unused:
        raise SpecError("ひな形が使っていない値がある: %s" % ", ".join(unused))
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], tpl)


def build(spec, inherited_key=None):
    """仕様 JSON から HTML 文字列を作る。(html, 正規化した仕様) を返す。

    正規化した仕様は生成物にそのまま埋め込む。これで生成物が自己完結し、
    元の JSON を無くしても、生成物を仕様として読み直して作り直せる。
    """
    doc = normalize(spec, inherited_key)

    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()

    out = substitute(tpl, {
        "TITLE": html.escape(doc["title"]),
        # subtitle / footer は HTML をそのまま入れる。信頼できる仕様にだけ使う。
        "SUBTITLE": doc["subtitle"],
        "FOOTER": doc["footer"],
        # 描画に要るものは全部この 1 つから引く。二重に持たない。
        "SPEC_JSON": js_literal(doc),
    })
    return out, doc


SPEC_TAG = re.compile(
    r'<script type="application/json" id="todo-spec">(.*?)</script>', re.S
)


def extract_spec(html_text):
    """生成物に埋め込まれた仕様を取り出す。無ければ None。"""
    m = SPEC_TAG.search(html_text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise SpecError("生成物に埋め込まれた仕様が読めない: %s" % e)


def load_spec(path):
    """仕様を読む。`.json` でも、生成済みの `.html` でも受ける。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise SpecError("仕様を読めない: %s" % e)

    if path.lower().endswith((".html", ".htm")):
        spec = extract_spec(text)
        if spec is None:
            raise SpecError(
                "この HTML には仕様が埋め込まれていない "
                "(このスクリプトが作ったものか確認すること): %s" % path
            )
        return spec

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SpecError("仕様の JSON が読めない: %s" % e)


def read_existing(dest):
    """上書き先に既にある生成物の仕様を読む。無ければ None。"""
    if not os.path.exists(dest):
        return None
    try:
        with open(dest, encoding="utf-8") as f:
            return extract_spec(f.read())
    except (OSError, SpecError):
        # 読めない上書き先は「無い」と同じに扱う。ここで落とす理由がない。
        return None


def item_index(spec):
    """正規化済みの仕様から {ID: 項目名} を作る。"""
    return {
        it["id"]: it["title"]
        for sec in spec.get("sections", [])
        for it in sec.get("items", [])
    }


def implicit_ids(spec):
    """入力仕様のうち、id を書かずに title から作られる ID の集合。

    ここに入る項目は、title を書き換えると別物になる。
    """
    out = set()
    for sec in spec.get("sections", []) or []:
        for it in sec.get("items", []) or []:
            if isinstance(it, str):
                out.add(stable_id(it))
            elif isinstance(it, dict) and it.get("id") is None:
                title = it.get("title")
                if isinstance(title, str):
                    out.add(stable_id(title))
    return out


def rename_warning(raw_spec, old, new):
    """改名でチェックを落としそうなときだけ警告文を返す。無ければ None。

    条件は 2 つ揃ったときだけ。単なる追加・削除・並べ替えでは黙る。
      - 既存の ID が消える
      - id を書いていない入力項目から、新しい ID が生まれている
    削除と追加を同時にやっただけでも出るが、安全側の空振りとして許す。

    ツールには、改名なのか「削除して追加」なのかを区別する手がかりが無い。
    位置や名前の似かたで推測すると、別の項目へチェックを移す事故になる。
    だから継がずに、気付ける形にだけする。
    """
    old_ids, new_ids = set(item_index(old)), set(item_index(new))
    if not (old_ids - new_ids):
        return None
    if not ((new_ids - old_ids) & implicit_ids(raw_spec)):
        return None
    return (
        "警告: 消える項目と、id を書いていない新しい項目が同時にあります。\n"
        "      項目名を書き換えたのなら、チェックは引き継げません。\n"
        "      開くと古い ID は保存から消え、名前を戻しても復旧しません。\n"
        "      維持したいなら、この生成物から --extract した仕様を土台にするか、\n"
        "      仕様に元の id を書いてください。"
    )


def diff_specs(old, new):
    """更新で何が起きるかを行の一覧にする。空なら中身に変化なし。

    チェックが外れるのは「消えた ID」だけ。ID さえ残っていれば、
    項目名を書き換えても並べ替えてもチェックは残る。
    """
    old_items, new_items = item_index(old), item_index(new)
    lines = []
    for iid, title in new_items.items():
        if iid not in old_items:
            lines.append("  + 追加: %s" % title)
        elif old_items[iid] != title:
            lines.append("  ~ 改名: %s → %s (チェックは残る)" % (old_items[iid], title))
    for iid, title in old_items.items():
        if iid not in new_items:
            lines.append("  - 削除: %s (チェック状態も消える)" % title)
    if old.get("title") != new.get("title"):
        lines.append("  ~ リスト名: %s → %s (保存キーは据え置き)"
                     % (old.get("title"), new.get("title")))
    old_dec, new_dec = bool(old.get("decision")), bool(new.get("decision"))
    if old_dec and not new_dec:
        lines.append("  ~ 判定用をやめる (YES はチェックとして残り、NO の判定は消える)")
    elif new_dec and not old_dec:
        lines.append("  ~ 判定用にする (チェック済みは YES として残る)")
    return lines


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    # 入出力を UTF-8 に固定する。Windows の既定 (cp932) のままだと、--extract を
    # リダイレクトした JSON を読み直せず、cp932 に無い文字 (絵文字など) で落ちる。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="仕様 JSON (または生成済み HTML) から ToDo の HTML を作る。",
    )
    ap.add_argument("spec", help="仕様の JSON、または生成済みの HTML")
    ap.add_argument("-o", "--out", help="出力先 HTML (既定: 仕様ファイルと同名の .html)")
    ap.add_argument(
        "--extract",
        action="store_true",
        help="生成物から仕様 JSON を取り出して標準出力に書く (編集の土台にする)",
    )
    ap.add_argument(
        "--no-open",
        action="store_true",
        help="生成後にブラウザを開かない (既定は Chrome 本体で開く)",
    )
    ap.add_argument("--chrome-path", help="使う chrome.exe を明示する (既定: 自動検出)")
    args = ap.parse_args()

    try:
        run(args)
    except SpecError as e:
        raise SystemExit(str(e))


def run(args):
    spec = load_spec(args.spec)

    if args.extract:
        # 検査を通してから出す。id と保存キーが埋まった形になるので、
        # これを編集して渡し直せば、名前を変えてもチェックが残る。
        print(json.dumps(normalize(spec), ensure_ascii=False, indent=2))
        return

    dest = args.out
    if not dest:
        # 仕様ファイルと同名の .html。日本語タイトルでも名前が潰れず、上書き先が読める。
        dest = os.path.splitext(os.path.abspath(args.spec))[0] + ".html"
    dest = os.path.abspath(dest)

    # 上書き先に既に生成物があれば、保存キーを継いで、何が変わるかを先に出す。
    existing = read_existing(dest)
    out_html, doc = build(spec, existing.get("storageKey") if existing else None)

    warning = None
    if existing:
        changes = diff_specs(existing, doc)
        print("更新: %s" % dest)
        if changes:
            for line in changes:
                print(line)
        else:
            print("  (項目に変化なし)")
        warning = rename_warning(spec, existing, doc)
    else:
        print("wrote: %s" % dest)

    if warning:
        # 差分を出し切ってから書く。混ざると、どの行に対する警告か読めない。
        sys.stdout.flush()
        print(warning, file=sys.stderr)

    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(out_html)
    print("sections=%d items=%d key=%s"
          % (len(doc["sections"]),
             sum(len(s["items"]) for s in doc["sections"]),
             doc["storageKey"]))

    if args.no_open or os.environ.get(ENV_NO_OPEN) in ("1", "true", "True"):
        return
    try:
        open_in_browser(dest, args.chrome_path)
    except SpecError as e:
        # 生成は済んでいる。開けなかったことを終了コードに混ぜない。
        print("HTML は生成済みだが、開けなかった: %s" % e, file=sys.stderr)


if __name__ == "__main__":
    main()
