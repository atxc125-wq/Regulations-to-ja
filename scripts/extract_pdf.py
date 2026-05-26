#!/usr/bin/env python3
"""
extract_pdf.py  --  PDFからUN法規の構造化JSONを生成し、版間の差分を検出する。

Usage:
    # PDFを抽出してJSONを生成
    python extract_pdf.py extract --pdf raw_pdf/R13/2023-06-15_rev2/R13.pdf \
                                   --reg R13 --version rev2 --date 2023-06-15

    # 2版間の差分を生成
    python extract_pdf.py diff --reg R13 --from rev1 --to rev2
"""

import re
import json
import hashlib
from pathlib import Path
from difflib import SequenceMatcher
from dataclasses import dataclass, field, asdict
from typing import Optional

import click

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# --------------------------------------------------------------------------- #
# データモデル
# --------------------------------------------------------------------------- #

@dataclass
class Paragraph:
    uid: str
    number: str
    title: str
    text: str
    level: int
    parent: Optional[str]
    status: str = "untranslated"
    translation: Optional[str] = None
    summary_ja: Optional[str] = None
    modified: bool = False
    justification: Optional[str] = None
    prev_uid: Optional[str] = None


def make_uid(regulation: str, text: str) -> str:
    """テキストのSHA-256から8文字のUIDを生成する。"""
    normalized = " ".join(text.split())
    digest = hashlib.sha256(f"{regulation}:{normalized}".encode()).hexdigest()
    return f"{regulation}-{digest[:8]}"


# --------------------------------------------------------------------------- #
# PDF テキスト抽出
# --------------------------------------------------------------------------- #

def extract_text_pdfplumber(pdf_path: Path) -> str:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber not installed. Run: pip install pdfplumber")
    with pdfplumber.open(pdf_path) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if text:
                pages.append(text)
    return "\n".join(pages)


def extract_text_pymupdf(pdf_path: Path) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed. Run: pip install pymupdf")
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pages.append(page.get_text("text"))
    doc.close()
    return "\n".join(pages)


def extract_text(pdf_path: Path) -> str:
    """利用可能なライブラリでPDFテキストを抽出する。"""
    if pdfplumber is not None:
        return extract_text_pdfplumber(pdf_path)
    if fitz is not None:
        return extract_text_pymupdf(pdf_path)
    raise RuntimeError("Neither pdfplumber nor PyMuPDF is installed.")


# --------------------------------------------------------------------------- #
# 段落パーサ
# --------------------------------------------------------------------------- #

# UN法規の段落番号パターン例: "1.", "1.1", "1.1.1", "1.1.1.1"
_PARA_HEADER = re.compile(
    r'^(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>[A-Z][^\n]{0,120})',
    re.MULTILINE,
)

# 目次ページを検出するキーワード
_TOC_KEYWORDS = re.compile(
    r'(table of contents|contents|page\s+\d+\s*$)',
    re.IGNORECASE,
)

# 脚注パターン（ページ下部の短い注記）
_FOOTNOTE = re.compile(r'^\s*\d+\)\s+', re.MULTILINE)


def _infer_level(number: str) -> int:
    """段落番号のドット数からレベルを推定する（1→1, 1.1→2, 1.1.1→3）。"""
    return number.count('.') + 1


def _infer_parent(number: str) -> Optional[str]:
    """段落番号から親番号を推定する。"""
    parts = number.rsplit('.', 1)
    if len(parts) == 1:
        return None
    return parts[0]


def _clean_text(text: str) -> str:
    """改行・余分な空白を正規化し、脚注を除去する。"""
    text = _FOOTNOTE.sub('', text)
    lines = [line.strip() for line in text.splitlines()]
    # 空行を1つに圧縮
    result, prev_blank = [], False
    for line in lines:
        if not line:
            if not prev_blank:
                result.append('')
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return ' '.join(l for l in result if l)


def parse_paragraphs(raw_text: str, regulation: str) -> list[Paragraph]:
    """
    生テキストからParagraphリストを生成する。

    戦略:
    1. 目次らしいブロックをスキップ
    2. 段落ヘッダ行を検出してスパンを確定
    3. 各スパン内テキストをクリーニング
    """
    lines = raw_text.splitlines()
    # 目次セクションを除外（最初の"1. Scope"が現れる行まで）
    start_line = 0
    for i, line in enumerate(lines):
        if re.match(r'^1\.\s+Scope', line.strip(), re.IGNORECASE):
            start_line = i
            break
    body = "\n".join(lines[start_line:])

    matches = list(_PARA_HEADER.finditer(body))
    paragraphs: list[Paragraph] = []

    for idx, m in enumerate(matches):
        number = m.group('number')
        title = m.group('title').strip()
        # テキストはこのヘッダの終わりから次のヘッダの始まりまで
        content_start = m.end()
        content_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        raw_content = body[content_start:content_end]
        text = _clean_text(raw_content)
        # タイトルをテキスト先頭に含めない
        full_text = f"{title} {text}".strip() if text else title

        paragraphs.append(Paragraph(
            uid=make_uid(regulation, full_text),
            number=number,
            title=title,
            text=full_text,
            level=_infer_level(number),
            parent=_infer_parent(number),
        ))

    return paragraphs


# --------------------------------------------------------------------------- #
# 差分検出
# --------------------------------------------------------------------------- #

# 類似度の閾値
THRESHOLD_SLID = 0.92     # これ以上同じなら "SLID" (番号変更のみ)
THRESHOLD_MODIFIED = 0.50  # これ以上なら "MODIFIED"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class DiffEntry:
    type: str  # UNCHANGED | MODIFIED | ADDED | DELETED | SLID
    uid: str
    prev_uid: Optional[str]
    number: str
    title: str
    text_new: Optional[str]
    text_old: Optional[str]
    similarity: Optional[float]
    justification: Optional[str]


def diff_versions(
    old_paras: list[Paragraph],
    new_paras: list[Paragraph],
) -> list[DiffEntry]:
    """
    旧版と新版のParagraphリストを比較してDiffEntryリストを返す。

    アルゴリズム:
    1. UIDが一致するものは UNCHANGED（または内容が変わっていれば MODIFIED）
    2. 残りの旧paragraphと新paragraphの間で類似度マッチング
       - ratio >= THRESHOLD_SLID → SLID（段落番号ズレ）
       - ratio >= THRESHOLD_MODIFIED → MODIFIED
    3. マッチしなかった旧paragraph → DELETED
    4. マッチしなかった新paragraph → ADDED
    """
    old_by_uid = {p.uid: p for p in old_paras}
    new_by_uid = {p.uid: p for p in new_paras}

    entries: list[DiffEntry] = []
    matched_old_uids: set[str] = set()
    matched_new_uids: set[str] = set()

    # Pass 1: UIDが一致
    for new_p in new_paras:
        if new_p.uid in old_by_uid:
            old_p = old_by_uid[new_p.uid]
            matched_old_uids.add(old_p.uid)
            matched_new_uids.add(new_p.uid)
            change_type = "UNCHANGED"
            sim = _similarity(old_p.text, new_p.text)
            if sim < 1.0 and sim >= THRESHOLD_MODIFIED:
                change_type = "MODIFIED"
            entries.append(DiffEntry(
                type=change_type,
                uid=new_p.uid,
                prev_uid=old_p.uid,
                number=new_p.number,
                title=new_p.title,
                text_new=new_p.text,
                text_old=old_p.text if change_type == "MODIFIED" else None,
                similarity=round(sim, 4) if change_type == "MODIFIED" else None,
                justification=None,
            ))

    # Pass 2: 未マッチの旧・新パラグラフ間で類似度マッチング
    unmatched_old = [p for p in old_paras if p.uid not in matched_old_uids]
    unmatched_new = [p for p in new_paras if p.uid not in matched_new_uids]

    used_old: set[str] = set()
    used_new: set[str] = set()

    # 全ペアの類似度を計算し、スコア降順でマッチング（greedy）
    candidates: list[tuple[float, Paragraph, Paragraph]] = []
    for new_p in unmatched_new:
        for old_p in unmatched_old:
            sim = _similarity(old_p.text, new_p.text)
            if sim >= THRESHOLD_MODIFIED:
                candidates.append((sim, old_p, new_p))
    candidates.sort(key=lambda x: x[0], reverse=True)

    for sim, old_p, new_p in candidates:
        if old_p.uid in used_old or new_p.uid in used_new:
            continue
        used_old.add(old_p.uid)
        used_new.add(new_p.uid)
        matched_old_uids.add(old_p.uid)
        matched_new_uids.add(new_p.uid)
        change_type = "SLID" if sim >= THRESHOLD_SLID else "MODIFIED"
        entries.append(DiffEntry(
            type=change_type,
            uid=new_p.uid,
            prev_uid=old_p.uid,
            number=new_p.number,
            title=new_p.title,
            text_new=new_p.text,
            text_old=old_p.text,
            similarity=round(sim, 4),
            justification=None,
        ))

    # Pass 3: 残りをADDED / DELETED
    for new_p in unmatched_new:
        if new_p.uid not in matched_new_uids:
            entries.append(DiffEntry(
                type="ADDED",
                uid=new_p.uid,
                prev_uid=None,
                number=new_p.number,
                title=new_p.title,
                text_new=new_p.text,
                text_old=None,
                similarity=None,
                justification=None,
            ))
    for old_p in unmatched_old:
        if old_p.uid not in matched_old_uids:
            entries.append(DiffEntry(
                type="DELETED",
                uid=old_p.uid,
                prev_uid=old_p.uid,
                number=old_p.number,
                title=old_p.title,
                text_new=None,
                text_old=old_p.text,
                similarity=None,
                justification=None,
            ))

    # 段落番号順にソート
    entries.sort(key=lambda e: [int(x) for x in (e.number or "0").split('.')])
    return entries


def apply_diff_to_new(
    new_paras: list[Paragraph],
    diff_entries: list[DiffEntry],
) -> list[Paragraph]:
    """差分情報をnew_parasのParagraphオブジェクトに反映する。"""
    diff_by_uid = {e.uid: e for e in diff_entries}
    result = []
    for p in new_paras:
        entry = diff_by_uid.get(p.uid)
        if entry and entry.type in ("MODIFIED", "SLID"):
            p.modified = True
            p.prev_uid = entry.prev_uid
            p.justification = entry.justification
            if entry.type == "MODIFIED":
                p.status = "untranslated"  # 要再翻訳
        result.append(p)
    return result


# --------------------------------------------------------------------------- #
# JSON入出力ヘルパー
# --------------------------------------------------------------------------- #

DATA_DIR = Path(__file__).parent.parent / "data"


def load_structured(regulation: str, version: str) -> dict:
    path = DATA_DIR / regulation / version / "structured.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_structured(data: dict, regulation: str, version: str) -> None:
    path = DATA_DIR / regulation / version / "structured.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    click.echo(f"Saved: {path}")


def save_diff(diff_data: dict, regulation: str, from_ver: str, to_ver: str) -> None:
    path = DATA_DIR / regulation / "diff" / f"{from_ver}_to_{to_ver}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diff_data, f, ensure_ascii=False, indent=2)
    click.echo(f"Saved diff: {path}")


# --------------------------------------------------------------------------- #
# CLIコマンド
# --------------------------------------------------------------------------- #

@click.group()
def cli():
    """UN法規PDFの構造化・差分検出ツール"""


@cli.command()
@click.option('--pdf', required=True, type=click.Path(exists=True), help='入力PDFパス')
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--version', required=True, help='バージョン識別子 (例: rev2)')
@click.option('--date', required=True, help='発行日 YYYY-MM-DD')
@click.option('--title', default='', help='法規タイトル（省略時はPDFから推定）')
def extract(pdf: str, reg: str, version: str, date: str, title: str):
    """PDFからJSONを生成する"""
    pdf_path = Path(pdf)
    click.echo(f"Extracting text from {pdf_path} ...")
    raw_text = extract_text(pdf_path)

    click.echo("Parsing paragraphs ...")
    paras = parse_paragraphs(raw_text, reg)
    click.echo(f"Found {len(paras)} paragraphs.")

    data = {
        "regulation": reg,
        "version": version,
        "source_date": date,
        "title": title or reg,
        "paragraphs": [asdict(p) for p in paras],
    }
    save_structured(data, reg, version)


@cli.command()
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--from', 'from_ver', required=True, help='比較元バージョン (例: rev1)')
@click.option('--to', 'to_ver', required=True, help='比較先バージョン (例: rev2)')
def diff(reg: str, from_ver: str, to_ver: str):
    """2バージョン間の差分JSONを生成し、新版JSONに差分フラグを付与する"""
    old_data = load_structured(reg, from_ver)
    new_data = load_structured(reg, to_ver)

    old_paras = [Paragraph(**p) for p in old_data['paragraphs']]
    new_paras = [Paragraph(**p) for p in new_data['paragraphs']]

    click.echo(f"Comparing {from_ver} ({len(old_paras)} paras) → {to_ver} ({len(new_paras)} paras) ...")
    entries = diff_versions(old_paras, new_paras)

    counts = {}
    for e in entries:
        counts[e.type] = counts.get(e.type, 0) + 1
    click.echo(f"Diff result: {counts}")

    # 差分情報を新版のstructured.jsonに反映
    updated_new = apply_diff_to_new(new_paras, entries)
    new_data['paragraphs'] = [asdict(p) for p in updated_new]
    save_structured(new_data, reg, to_ver)

    # 差分JSONを保存
    diff_data = {
        "regulation": reg,
        "from_version": from_ver,
        "to_version": to_ver,
        "from_date": old_data.get('source_date'),
        "to_date": new_data.get('source_date'),
        "summary": {k: v for k, v in counts.items()},
        "changes": [
            asdict(e) for e in entries
            if e.type != "UNCHANGED"
        ],
    }
    save_diff(diff_data, reg, from_ver, to_ver)


if __name__ == '__main__':
    cli()
