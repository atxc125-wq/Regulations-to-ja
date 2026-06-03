#!/usr/bin/env python3
"""
build_html.py  --  翻訳済みJSONとGlossaryからStaticなHTMLを生成する。

Usage:
    python build_html.py --reg R13 --version rev2
    python build_html.py --all
"""

import json
from pathlib import Path

import click

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    raise SystemExit("jinja2 not installed. Run: pip install jinja2")

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
SITE_DIR = BASE_DIR / "docs"


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_nav_noise(p: dict) -> bool:
    """ナビゲーションツリーに表示すべきでないノイズ段落かどうかを判定する。
    - PDFページヘッダー（文書番号行、"E/ECE/..."）
    - 目次ドット行（"......"）
    - PDFページ走りヘッダー（3桁以上の純整数番号 = ページ番号）
      → 章番号は最大でも2桁（22章以下）なので3桁以上は全てページヘッダー
    """
    import re as _re
    text = (p.get("text") or "").strip()
    title = (p.get("title") or "").strip()
    number = (p.get("number") or "").strip()
    if (text.startswith("E/ECE/") or text.startswith("ECE/")
            or title.startswith("E/ECE/") or title.startswith("ECE/")):
        return True
    if "......" in title or "......" in text:
        return True
    if _re.fullmatch(r"\d{3,}", number):
        return True
    return False


def mark_footnote_noise(paragraphs: list[dict]) -> None:
    """本文内容（level 2以上）の後に出現する level=1 かつ数字のみ番号の段落を
    ページ脚注とみなし _nav_hidden=True にする（インプレース変更）。
    """
    import re
    seen_content = False
    for p in paragraphs:
        if p.get("type") == "image":
            continue
        level = p.get("level", 1)
        if level >= 2:
            seen_content = True
        if seen_content and level == 1 and re.fullmatch(r"\d{1,2}", p.get("number", "")):
            p["_nav_hidden"] = True


def mark_annex_paragraphs(paragraphs: list[dict]) -> None:
    """附属書（Annex）に属する段落に _in_annex フラグを付与する。

    _nav_hidden（TOC ノイズ）付与後に呼ぶこと。
    番号後退ヒューリスティックで附属書ゾーンを検出する。
    annex_id フィールドは mark_annex_ids() で附属書ID付与に使用する。
    """
    max_top = 0
    in_annex = False
    seen_body = False  # level>=2 段落が出現したら True（本文に入ったと判定）

    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        num = p.get("number", "")
        level = p.get("level", 1)
        try:
            top = int(num.split('.')[0])
        except (ValueError, IndexError):
            continue
        if top <= 0:
            continue

        if level >= 2:
            seen_body = True

        if not in_annex:
            if seen_body and max_top >= 2 and top < max_top // 2 + 1:
                in_annex = True
            elif seen_body:
                max_top = max(max_top, top)

        if in_annex:
            p["_in_annex"] = True


def build_annex_tree(paragraphs: list[dict]) -> list[dict]:
    """附属書ツリーを構築する（左ペイン用）。
    整数番号の第2出現ブロック（附属書 TOC セクション）から各附属書のタイトルを取得する。
    _nav_hidden フラグ付与前に呼ぶこと。
    """
    import re as _re

    # num="1" の出現位置を列挙して TOC セクション境界を特定
    occ1 = [i for i, p in enumerate(paragraphs)
            if p.get("type") != "image" and p.get("number", "") == "1"]
    if len(occ1) < 2:
        return []

    # 第2出現 (附属書TOCの先頭) ～ 第3出現 (本文の先頭) が附属書TOCセクション
    annex_toc_start = occ1[1]
    annex_toc_end   = occ1[2] if len(occ1) >= 3 else len(paragraphs)

    entries: dict = {}
    for i, p in enumerate(paragraphs):
        if i < annex_toc_start or i >= annex_toc_end:
            continue
        if p.get("type") == "image":
            continue
        num = p.get("number", "")
        if not _re.fullmatch(r"\d+", num):
            continue
        n = int(num)
        if not (1 <= n <= 22):
            continue
        title = (p.get("title") or "").strip()
        # "......" ドットリーダーを除去
        clean = _re.sub(r'\s*\.{4,}.*', '', title).rstrip('. ')
        if clean.lower().startswith("appendix"):
            continue
        if n not in entries:
            entries[n] = {
                "number": n,
                "title": clean,
                "title_ja": (p.get("title_ja") or "").rstrip(". "),
            }

    return [entries[n] for n in sorted(entries.keys())]


def mark_annex_ids(paragraphs: list[dict]) -> None:
    """附属書段落に _annex_id（1–22）を付与する。
    mark_annex_paragraphs() の後に呼ぶこと。

    annex_id フィールドが設定されている段落（patch_annex_ids.py でパッチ済み）は
    それを直接 _annex_id として使用する。未設定の段落は従来のヒューリスティックで判定する。
    """
    import re as _re

    # Step 1: main_body_end（最後の実質的な chapter 12 段落）を特定
    main_body_end = -1
    for i, p in enumerate(paragraphs):
        if p.get("type") == "image":
            continue
        num = p.get("number", "")
        try:
            top = int(num.split('.')[0])
        except (ValueError, IndexError):
            continue
        if top != 12:
            continue
        text = p.get("text", "") or ""
        title = p.get("title", "") or ""
        if "......" in text or "......" in title:
            continue
        if text.strip().startswith("E/ECE/") or title.strip().startswith("E/ECE/"):
            continue
        main_body_end = i

    if main_body_end < 0:
        return

    # Step 2: Annex 10+ の明示ヘッダー位置を収集
    annex_10plus = []  # list of (annex_num, start_idx)
    for i, p in enumerate(paragraphs):
        if i <= main_body_end or p.get("type") == "image":
            continue
        if p.get("level", 1) != 1:
            continue
        title = (p.get("title") or "").strip()
        m = _re.match(r'^Annex\s+(\d+)\b', title, _re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if n >= 10:
                annex_10plus.append((n, i))

    # Step 3: Annex 1–9 グループを検出（リスタート検出）
    annex_10_start = annex_10plus[0][1] if annex_10plus else len(paragraphs)
    groups = []  # list of list[int]
    cur = []
    last_top = None

    for i, p in enumerate(paragraphs):
        if i <= main_body_end or i >= annex_10_start:
            continue
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        num = p.get("number", "")
        try:
            top = int(num.split('.')[0])
        except (ValueError, IndexError):
            continue
        if top <= 0:
            continue
        if last_top is not None and top < last_top and last_top > 1:
            if cur:
                groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
        last_top = top

    if cur:
        groups.append(cur)

    # Step 4: グループ → Annex 1, 2, 3, … に ID を付与
    # グループ間の hidden 段落にも同じ annex_id を付与するため、
    # グループの先頭〜次グループの先頭までの範囲を連続で塗る。
    # Annex 1–9 の範囲を超えたグループは最後の Annex に含める。
    for g_idx, indices in enumerate(groups):
        annex_num = min(g_idx + 1, 9)
        start_idx = indices[0]
        end_idx = groups[g_idx + 1][0] if g_idx + 1 < len(groups) else annex_10_start
        for idx in range(start_idx, end_idx):
            if paragraphs[idx].get("type") != "image":
                paragraphs[idx]["_annex_id"] = annex_num
                paragraphs[idx]["_in_annex"] = True

    # Step 5: Annex 10+ に ID を付与
    for j, (annex_num, start) in enumerate(annex_10plus):
        end = annex_10plus[j + 1][1] if j + 1 < len(annex_10plus) else len(paragraphs)
        for i in range(start, end):
            if paragraphs[i].get("type") != "image":
                paragraphs[i]["_annex_id"] = annex_num
                paragraphs[i]["_in_annex"] = True

    # Step 6: patch_annex_ids.py で設定された annex_id を上書き適用（前向き伝播）。
    # ページヘッダーから確定した正しい附属書番号でヒューリスティック結果を補正する。
    current_override = None
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        if not p.get("_in_annex"):
            current_override = None
            continue
        aid = p.get("annex_id")
        if aid is not None:
            current_override = aid
        if current_override is not None:
            p["_annex_id"] = current_override


def build_tree(paragraphs: list[dict]) -> list[dict]:
    """段落リストから階層ツリーを構築する（左ペイン・中ペイン用）。
    type=="image" およびノイズ段落はナビツリーに含めない。
    左ペインには主要章のみ表示（整数番号が重複し始めたら附属書扱いで除外）。
    同じ整数章番号の後出現（本文）に title_ja があれば既存エントリを更新する。
    """
    import re as _re
    chapters = []
    chapter_map = {}
    chapter_list_entries: dict[str, dict] = {}  # int-num → chapters リスト内のノード
    seen_top_numbers: set[str] = set()
    top_level_closed = False  # 主要章の番号が重複した時点で左ペイン追加を終了

    for p in paragraphs:
        # 画像ブロック・ノイズはツリーナビに表示しない
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        node = {
            "uid": p["uid"],
            "number": p["number"],
            "title": p["title"].rstrip(". "),
            "title_ja": p.get("title_ja", "").rstrip(". "),
            "level": p["level"],
            "modified": p.get("modified", False),
            "children": [],
        }
        if p["level"] == 1:
            num = p["number"]
            is_integer = bool(_re.fullmatch(r"\d+", num))
            if is_integer and num in seen_top_numbers:
                top_level_closed = True  # 附属書の繰り返し番号が始まった
                # 本文の後出現に title_ja があれば chapters リストのエントリを更新する
                if num in chapter_list_entries and node["title_ja"]:
                    existing = chapter_list_entries[num]
                    if not existing["title_ja"]:
                        existing["title_ja"] = node["title_ja"]
                        existing["title"] = node["title"]
            if not top_level_closed:
                chapters.append(node)
                if is_integer:
                    seen_top_numbers.add(num)
                    chapter_list_entries[num] = node
            chapter_map[num] = node
        else:
            parent_num = p.get("parent")
            if parent_num and parent_num in chapter_map:
                chapter_map[parent_num]["children"].append(node)
            # 親が見つからない孤立ノードは左ペインに追加しない
        chapter_map[p["number"]] = node

    return chapters


_PARA_REF_RE = None  # 遅延初期化


def find_para_refs(text: str) -> list[str]:
    """英語テキストから 'paragraph(s) N.N.N...' パターンの段落番号を抽出する。"""
    global _PARA_REF_RE
    if _PARA_REF_RE is None:
        import re as _re_r
        _PARA_REF_RE = _re_r.compile(
            r'\bparagraphs?\s+(\d+(?:\.\d+)+)((?:\s*(?:,|and|to|or)\s+\d+(?:\.\d+)+)*)',
            _re_r.IGNORECASE,
        )
    import re as _re_r
    seen: set[str] = set()
    result: list[str] = []
    for m in _PARA_REF_RE.finditer(text):
        for num in _re_r.findall(r'\d+(?:\.\d+)+', m.group()):
            if num not in seen:
                seen.add(num)
                result.append(num)
    return result


def annotate_glossary(text: str, glossary_terms: dict[str, dict]) -> str:
    """テキスト内の用語をツールチップ用のdata属性付きspanに置換する。"""
    if not text:
        return text
    for term, info in sorted(glossary_terms.items(), key=lambda x: -len(x[0])):
        replacement = (
            f'<span class="glossary-term" '
            f'data-term="{term}" '
            f'data-definition="{info["definition_ja"]}" '
            f'data-ref="{info.get("paragraph_ref", "")}">'
            f'{term}</span>'
        )
        # 大文字小文字を区別しない置換（最初のマッチのみ）
        lower_text = text.lower()
        lower_term = term.lower()
        idx = lower_text.find(lower_term)
        if idx >= 0:
            text = text[:idx] + replacement + text[idx + len(term):]
    return text


def annotate_glossary_ja(text: str, glossary_ja_terms: dict[str, dict]) -> str:
    """日本語テキスト内の用語をツールチップ用のdata属性付きspanに置換する。
    全マッチを元テキスト上で収集してから右→左に挿入することで、
    longer-first の優先順位を保ちつつ既挿入 HTML への再マッチを防ぐ。
    """
    if not text:
        return text
    terms = sorted(glossary_ja_terms.keys(), key=lambda x: -len(x))
    if not terms:
        return text
    # Collect all non-overlapping matches on the original text (longest-term priority)
    covered: set[int] = set()
    matches: list[tuple[int, int, str]] = []
    for term in terms:
        start = 0
        while True:
            pos = text.find(term, start)
            if pos < 0:
                break
            end = pos + len(term)
            if not covered.intersection(range(pos, end)):
                matches.append((pos, end, term))
                covered.update(range(pos, end))
            start = pos + 1
    # Insert spans from right to left so earlier positions stay valid
    matches.sort(key=lambda x: x[0], reverse=True)
    for pos, end, term in matches:
        info = glossary_ja_terms[term]
        en = info["en_term"].replace('"', '&quot;')
        defn = info["definition_ja"].replace('"', '&quot;')
        ref = info["paragraph_ref"].replace('"', '&quot;')
        span = (
            f'<span class="glossary-term" '
            f'data-term="{en}" '
            f'data-ja-term="{term}" '
            f'data-definition="{defn}" '
            f'data-ref="{ref}">'
            f'{term}</span>'
        )
        text = text[:pos] + span + text[end:]
    return text


def build_regulation_page(regulation: str, version: str) -> None:
    structured_path = DATA_DIR / regulation / version / "structured.json"
    glossary_path = DATA_DIR / regulation / "glossary.json"

    if not structured_path.exists():
        raise click.ClickException(f"Not found: {structured_path}")

    data = load_json(structured_path)
    glossary_terms: dict = {}
    glossary_ja_terms: dict = {}
    if glossary_path.exists():
        glossary_data = load_json(glossary_path)
        for t in glossary_data.get("terms", []):
            term = t.get("term", "")
            defn = t.get("description") or t.get("ja", "")
            ref  = t.get("paragraph_ref", "")
            ja   = t.get("ja", "").strip()
            if term:
                glossary_terms[term] = {"definition_ja": defn, "paragraph_ref": ref}
            # 日本語用語マップ（ja フィールドがある場合のみ）
            if ja and ja not in glossary_ja_terms:
                glossary_ja_terms[ja] = {"en_term": term, "definition_ja": defn, "paragraph_ref": ref}

    # テキスト段落のみに用語アノテーションを付与（画像ブロックはスキップ）
    paragraphs = data["paragraphs"]

    # ノイズフラグ付与前にツリーを構築する
    tree = build_tree(paragraphs)
    annex_tree = build_annex_tree(paragraphs)

    # ノイズフラグを付与してから用語アノテーションを追加
    mark_footnote_noise(paragraphs)
    for p in paragraphs:
        if p.get("type") == "image":
            continue
        # ナビゲーションノイズフラグを付与（ページヘッダー・目次ドット行）
        if not p.get("_nav_hidden"):
            p["_nav_hidden"] = is_nav_noise(p)
        # テーブルセル検出: "パラメータ名  単位  列番号" 形式（PDFテーブルの誤抽出）
        if not p.get("_nav_hidden"):
            import re as _re2
            title = p.get("title", "") or ""
            if len(title) < 50 and _re2.search(r'\s{2,}\S+\s{2,}\d\s*$', title):
                p["_nav_hidden"] = True
                p["_table_cell"] = True
        p["text_annotated"] = annotate_glossary(p.get("text", "") or "", glossary_terms)
        if p.get("translation"):
            # 日本語訳は日本語用語アノテーションのみ（英語アノテーション HTML に再適用しない）
            p["translation_annotated"] = annotate_glossary_ja(p["translation"], glossary_ja_terms)
        else:
            p["translation_annotated"] = None
        # 要約の1文目を抽出（カードヘッダーの短い日本語表示用）
        summary = p.get("summary_ja", "") or ""
        dot_idx = summary.find("。")
        p["summary_ja_short"] = summary[: dot_idx + 1] if dot_idx >= 0 else summary

    # _nav_hidden 付与後に附属書フラグを付与（TOCノイズをスキップして正確に判定）
    mark_annex_paragraphs(paragraphs)
    mark_annex_ids(paragraphs)

    # 参照段落チップ: "paragraph N.N..." 参照を検出し _refs リストを付与する。
    # _nav_hidden・_annex_id が確定した後に実行すること。
    _ref_map: dict[str, dict] = {}
    for p in paragraphs:
        if p.get("type") == "image":
            continue
        num = p.get("number", "")
        if not num:
            continue
        existing = _ref_map.get(num)
        is_hidden = bool(p.get("_nav_hidden"))
        if existing is None or (existing["hidden"] and not is_hidden):
            _ref_map[num] = {
                "uid": p.get("uid", ""),
                "summary_ja": (p.get("summary_ja") or "").strip(),
                "annex_id": p.get("_annex_id"),
                "hidden": is_hidden,
            }
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            p["_refs"] = []
            continue
        refs: list[dict] = []
        seen_ref_nums: set[str] = set()
        for num in find_para_refs(p.get("text", "") or ""):
            if num in seen_ref_nums:
                continue
            ref = _ref_map.get(num)
            if ref and ref["summary_ja"] and not ref["hidden"]:
                seen_ref_nums.add(num)
                refs.append({
                    "number": num,
                    "uid": ref["uid"],
                    "summary_ja": ref["summary_ja"],
                    "annex_id": ref["annex_id"],
                })
        p["_refs"] = refs

    # 中ペインの重複排除: 同じ number が複数回出現する場合（附属書が同じ番号を繰り返す）
    # 文書順で最初に出現した可視段落のみ _first_occ=True とする
    seen_para_numbers: set[str] = set()
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        num = p.get("number", "")
        if num and num not in seen_para_numbers:
            p["_first_occ"] = True
            seen_para_numbers.add(num)
        else:
            p["_first_occ"] = False

    # 本文章番号のギャップ検出: 本文章番号が連続していない場合（例 ch5→ch7）、
    # 中間章の区切りを最初に出現する章の直前段落に _gap_chapters_before として付与する。
    tree_chapter_map: dict[int, dict] = {}
    for node in tree:
        n = node["number"]
        if n.isdigit():
            tree_chapter_map[int(n)] = node
    if tree_chapter_map:
        last_body_ch_int = 0
        seen_body_ch_ints: set[int] = set()
        for p in paragraphs:
            if p.get("type") == "image" or p.get("_nav_hidden") or p.get("_in_annex"):
                continue
            num = p.get("number", "")
            try:
                top = int(num.split('.')[0])
            except (ValueError, IndexError):
                continue
            if top not in tree_chapter_map:
                continue
            if top not in seen_body_ch_ints:
                seen_body_ch_ints.add(top)
                # last_body_ch_int==0 は最初の章なのでギャップ扱いしない
                # （前に奇妙な章番号が来ても前章扱いしないため）
                if last_body_ch_int > 0:
                    gap = [tree_chapter_map[n]
                           for n in sorted(tree_chapter_map.keys())
                           if last_body_ch_int < n < top and n not in seen_body_ch_ints]
                    if gap:
                        p["_gap_chapters_before"] = gap
                last_body_ch_int = max(last_body_ch_int, top)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("regulation_page.html")

    output_dir = SITE_DIR / regulation
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"

    html = template.render(
        regulation=regulation,
        version=version,
        title=data.get("title", regulation),
        source_date=data.get("source_date", ""),
        paragraphs=paragraphs,
        tree=tree,
        annex_tree=annex_tree,
        glossary_terms=glossary_terms,
    )
    output_path.write_text(html, encoding="utf-8")
    click.echo(f"Built: {output_path}")


def build_index_page(regulations: list[str]) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("index.html")

    reg_data = []
    for reg in regulations:
        # 最新バージョンを探す（バージョンディレクトリを日付降順でソート）
        versions = sorted(
            [d.name for d in (DATA_DIR / reg).iterdir()
             if d.is_dir() and not d.name.startswith('.')],
            key=lambda v: v.lower(),
            reverse=True,
        )
        latest = versions[0] if versions else None
        if latest:
            try:
                data = load_json(DATA_DIR / reg / latest / "structured.json")
                # 画像ブロックは翻訳カウントから除外
                text_blocks = [p for p in data["paragraphs"] if p.get("type", "paragraph") == "paragraph"]
                total = len(text_blocks)
                translated = sum(1 for p in text_blocks if p.get("status") in ("done", "translated"))
                reg_data.append({
                    "number": reg,
                    "title": data.get("title", reg),
                    "version": latest,
                    "date": data.get("source_date", ""),
                    "total": total,
                    "translated": translated,
                    "progress": int(translated / total * 100) if total else 0,
                })
            except Exception:
                pass

    html = template.render(regulations=reg_data)
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")
    click.echo(f"Built: {SITE_DIR / 'index.html'}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

@click.group()
def cli():
    """静的HTMLビルドツール"""


@cli.command()
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--version', required=True, help='バージョン (例: rev2)')
def page(reg: str, version: str):
    """特定の法規ページを生成する"""
    build_regulation_page(reg, version)


@cli.command()
def all():
    """data/ 以下の全法規のHTMLを生成する"""
    regulations = [d.name for d in DATA_DIR.iterdir() if d.is_dir()]
    for reg in regulations:
        versions = sorted(
            [d.name for d in (DATA_DIR / reg).iterdir()
             if d.is_dir() and not d.name.startswith('.')],
            key=lambda v: v.lower(),
            reverse=True,
        )
        if versions:
            build_regulation_page(reg, versions[0])
    build_index_page(regulations)


if __name__ == '__main__':
    cli()
