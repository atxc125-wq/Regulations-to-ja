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
    - PDFページヘッダー（文書番号行）
    - 目次ドット行
    """
    text = (p.get("text") or "").strip()
    title = (p.get("title") or "").strip()
    if text.startswith("E/ECE/"):
        return True
    if "......" in title or "......" in text:
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


def build_tree(paragraphs: list[dict]) -> list[dict]:
    """段落リストから階層ツリーを構築する（左ペイン・中ペイン用）。
    type=="image" およびノイズ段落はナビツリーに含めない。
    左ペインには主要章のみ表示（整数番号が重複し始めたら附属書扱いで除外）。
    """
    import re as _re
    chapters = []
    chapter_map = {}
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
            if not top_level_closed:
                chapters.append(node)
                if is_integer:
                    seen_top_numbers.add(num)
            chapter_map[num] = node
        else:
            parent_num = p.get("parent")
            if parent_num and parent_num in chapter_map:
                chapter_map[parent_num]["children"].append(node)
            # 親が見つからない孤立ノードは左ペインに追加しない
        chapter_map[p["number"]] = node

    return chapters


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


def build_regulation_page(regulation: str, version: str) -> None:
    structured_path = DATA_DIR / regulation / version / "structured.json"
    glossary_path = DATA_DIR / regulation / "glossary.json"

    if not structured_path.exists():
        raise click.ClickException(f"Not found: {structured_path}")

    data = load_json(structured_path)
    glossary_terms = {}
    if glossary_path.exists():
        glossary_data = load_json(glossary_path)
        glossary_terms = {
            t["term"]: {
                "definition_ja": t.get("description") or t.get("ja", ""),
                "paragraph_ref": t.get("paragraph_ref", ""),
            }
            for t in glossary_data.get("terms", [])
        }

    # テキスト段落のみに用語アノテーションを付与（画像ブロックはスキップ）
    paragraphs = data["paragraphs"]

    # ノイズフラグ付与より前にツリーを構築する
    # （is_nav_noise が目次エントリを隠してしまう前に章リストを確定させる）
    tree = build_tree(paragraphs)

    # ノイズフラグを付与してから用語アノテーションを追加
    mark_footnote_noise(paragraphs)
    for p in paragraphs:
        if p.get("type") == "image":
            continue
        # ナビゲーションノイズフラグを付与（ページヘッダー・目次ドット行）
        if not p.get("_nav_hidden"):
            p["_nav_hidden"] = is_nav_noise(p)
        p["text_annotated"] = annotate_glossary(p.get("text", "") or "", glossary_terms)
        if p.get("translation"):
            p["translation_annotated"] = annotate_glossary(p["translation"], glossary_terms)
        else:
            p["translation_annotated"] = None

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
                translated = sum(1 for p in text_blocks if p.get("status") == "translated")
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
