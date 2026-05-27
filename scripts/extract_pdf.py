#!/usr/bin/env python3
"""
extract_pdf.py  --  PDFからUN法規の構造化JSONを生成し、版間の差分を検出する。
図表（Figure/Table）は画像としてクロップし、JSONに type:"image" ブロックとして挿入する。

Usage:
    python extract_pdf.py extract --pdf raw_pdf/R13/2023-10-05_Rev9/R13.pdf \
                                   --reg R13 --version Rev9 --date 2023-10-05

    python extract_pdf.py diff --reg R13 --from rev1 --to Rev9
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
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# pdfplumber はオプション依存。このモジュールは PyMuPDF を優先使用する
pdfplumber = None  # PyMuPDF で十分なため未使用

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
    type: str = "paragraph"
    status: str = "untranslated"
    translation: Optional[str] = None
    summary_ja: Optional[str] = None
    modified: bool = False
    justification: Optional[str] = None
    prev_uid: Optional[str] = None


@dataclass
class ImageBlock:
    uid: str
    type: str = "image"
    status: str = "done"
    page: int = 0
    bbox: list = field(default_factory=list)
    src: str = ""
    label: Optional[str] = None   # "Figure 1", "Table 2" など
    caption: Optional[str] = None


def make_uid(regulation: str, text: str) -> str:
    """テキストのSHA-256から8文字のUIDを生成する。"""
    normalized = " ".join(text.split())
    digest = hashlib.sha256(f"{regulation}:{normalized}".encode()).hexdigest()
    return f"{regulation}-{digest[:8]}"


def make_image_uid(regulation: str, page_num: int, bbox: tuple, label: str) -> str:
    """画像ブロックのユニークIDを生成する。"""
    key = f"{regulation}:img:p{page_num}:{bbox}:{label}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"{regulation}-img-{digest[:8]}"


# --------------------------------------------------------------------------- #
# 図表検出・画像保存ロジック（PyMuPDF）
# --------------------------------------------------------------------------- #

# 画像レンダリング解像度
RENDER_DPI = 150
# 図表キャプションを探す範囲（点単位、bbox下辺から）
CAPTION_SEARCH_HEIGHT = 48
# 図表キャプションのパターン
_CAPTION_RE = re.compile(
    r'(Figure|Fig\.?|Table|Tbl\.?|Equation|Eq\.?)\s*\d*\.?\d*',
    re.IGNORECASE,
)


def _rects_overlap(r1: tuple, r2: tuple, margin: float = 2.0) -> bool:
    """2つのbbox (x0,y0,x1,y1) が重なっているか判定する。"""
    x0a, y0a, x1a, y1a = r1
    x0b, y0b, x1b, y1b = r2
    return not (x1a + margin < x0b or x1b + margin < x0a or
                y1a + margin < y0b or y1b + margin < y0a)


def _find_table_bboxes(page) -> list[tuple]:
    """ページ上の表領域のbboxリストを返す。"""
    # PyMuPDF 1.23+ の find_tables() を優先使用
    if hasattr(page, "find_tables"):
        try:
            tabs = page.find_tables()
            result = []
            for t in tabs:
                # 最小面積フィルタ（極小の誤検知を除外）
                x0, y0, x1, y1 = t.bbox
                if (x1 - x0) > 30 and (y1 - y0) > 20:
                    result.append(tuple(t.bbox))
            return result
        except Exception:
            pass

    # フォールバック: 線描画からグリッドを推定
    return _find_table_bboxes_from_drawings(page)


def _find_table_bboxes_from_drawings(page) -> list[tuple]:
    """水平・垂直線の集積領域を表のbboxとして返す（フォールバック）。"""
    drawings = page.get_drawings()
    h_segs, v_segs = [], []

    for d in drawings:
        for item in d.get("items", []):
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) < 2:
                h_segs.append((min(p1.x, p2.x), p1.y, max(p1.x, p2.x), p1.y))
            elif abs(p1.x - p2.x) < 2:
                v_segs.append((p1.x, min(p1.y, p2.y), p1.x, max(p1.y, p2.y)))

    # 水平・垂直ともに3本以上ある場合のみ表と見なす
    if len(h_segs) < 3 or len(v_segs) < 3:
        return []

    # 交差を確認して候補グループを形成
    groups = _cluster_line_segments(h_segs, v_segs)
    return groups


def _cluster_line_segments(h_segs: list, v_segs: list) -> list[tuple]:
    """交差する水平・垂直線セグメントから表領域のbboxを推定する。"""
    # 簡易実装: 全セグメントの最小外接矩形を1グループとして返す
    all_x = [s[0] for s in h_segs + v_segs] + [s[2] for s in h_segs + v_segs]
    all_y = [s[1] for s in h_segs + v_segs] + [s[3] for s in h_segs + v_segs]
    if not all_x:
        return []
    bbox = (min(all_x), min(all_y), max(all_x), max(all_y))
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w > 30 and h > 20:
        return [bbox]
    return []


def _find_embedded_image_bboxes(page) -> list[tuple]:
    """ページに埋め込まれたラスター画像のbboxリストを返す。"""
    bboxes = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
            for r in rects:
                if (r.width > 20 and r.height > 20):
                    bboxes.append((r.x0, r.y0, r.x1, r.y1))
        except Exception:
            pass
    return bboxes


def _find_caption(page, bbox: tuple) -> Optional[str]:
    """bbox直下の短いテキストをキャプションとして探す。"""
    x0, y0, x1, y1 = bbox
    search_rect = fitz.Rect(x0 - 10, y1, x1 + 10, y1 + CAPTION_SEARCH_HEIGHT)
    text = page.get_textbox(search_rect).strip()
    if text and _CAPTION_RE.search(text):
        return text[:200]
    return None


def _find_label_above(page, bbox: tuple) -> Optional[str]:
    """bbox直上の短いテキストをラベル（Figure N / Table N）として探す。"""
    x0, y0, x1, y1 = bbox
    search_rect = fitz.Rect(x0 - 10, max(0, y0 - CAPTION_SEARCH_HEIGHT), x1 + 10, y0)
    text = page.get_textbox(search_rect).strip()
    m = _CAPTION_RE.search(text) if text else None
    return m.group(0) if m else None


def _crop_and_save(
    page,
    page_num: int,
    bbox: tuple,
    block_type: str,
    regulation: str,
    version: str,
    assets_dir: Path,
    label_counter: dict,
) -> ImageBlock:
    """
    bboxの領域をPNGにクロップ保存し、ImageBlockを返す。

    Args:
        page:         PyMuPDF ページオブジェクト
        page_num:     1-based ページ番号
        bbox:         (x0, y0, x1, y1) in PDF points
        block_type:   "table" | "image"
        regulation:   法規番号
        version:      バージョン識別子
        assets_dir:   保存先ディレクトリ
        label_counter: {"figure": n, "table": n} で自動連番管理
    """
    x0, y0, x1, y1 = bbox
    # ラベル生成
    if block_type == "table":
        label_counter["table"] += 1
        label_suffix = f"tbl{label_counter['table']}"
        label_str = f"Table {label_counter['table']}"
    else:
        label_counter["figure"] += 1
        label_suffix = f"fig{label_counter['figure']}"
        label_str = f"Figure {label_counter['figure']}"

    # ページ上のラベルテキストがあれば上書き
    detected_label = _find_label_above(page, bbox) or _find_caption(page, bbox)
    if detected_label:
        label_str = detected_label

    # UID・ファイル名生成
    uid = make_image_uid(regulation, page_num, bbox, label_str)
    filename = f"{version}_p{page_num:03d}_{label_suffix}_{uid[-6:]}.png"
    save_path = assets_dir / filename

    # クロップ & 高解像度レンダリング
    mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    clip = fitz.Rect(x0, y0, x1, y1)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    pix.save(str(save_path))

    caption = _find_caption(page, bbox)
    # siteからの相対パス
    rel_src = f"assets/images/{filename}"

    return ImageBlock(
        uid=uid,
        page=page_num,
        bbox=list(bbox),
        src=rel_src,
        label=label_str,
        caption=caption,
    )


# --------------------------------------------------------------------------- #
# メイン抽出ロジック（テキスト + 図表をページ順・Y座標順で統合）
# --------------------------------------------------------------------------- #

# [[IMG:uid]] マーカーパターン
_IMG_MARKER_RE = re.compile(r'\[\[IMG:([^\]]+)\]\]')


def extract_blocks_with_media(
    pdf_path: Path,
    regulation: str,
    version: str,
    assets_dir: Path,
) -> tuple[str, list[ImageBlock]]:
    """
    PDFを解析し、テキストと図表を統合したストリームを返す。

    Returns:
        text_with_markers: テキスト中に [[IMG:uid]] マーカーを挿入した文字列
        image_blocks:      抽出された ImageBlock のリスト（uid でインデックス可能）
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed. Run: pip install pymupdf")

    assets_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    text_parts: list[str] = []
    image_blocks: list[ImageBlock] = []
    label_counter = {"figure": 0, "table": 0}
    img_by_uid: dict[str, ImageBlock] = {}

    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]

        # --- 表・埋め込み画像の検出 ---
        table_bboxes = _find_table_bboxes(page)
        image_bboxes = _find_embedded_image_bboxes(page)

        # 表の内部に完全に包含される画像は除外（重複防止）
        filtered_image_bboxes = [
            ib for ib in image_bboxes
            if not any(_rects_overlap(ib, tb, margin=4) for tb in table_bboxes)
        ]

        # 全メディアブロックを (y_top, type, bbox) でまとめる
        media_events: list[tuple[float, str, tuple]] = []
        for tb in table_bboxes:
            media_events.append((tb[1], "table", tb))
        for ib in filtered_image_bboxes:
            media_events.append((ib[1], "image", ib))
        media_events.sort(key=lambda x: x[0])

        # --- テキストブロックを収集（メディア領域を除外）---
        excluded_bboxes = table_bboxes + filtered_image_bboxes
        text_block_events: list[tuple[float, str]] = []

        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            bbox = block["bbox"]
            if any(_rects_overlap(bbox, ex) for ex in excluded_bboxes):
                continue
            block_text = " ".join(
                span["text"]
                for line in block.get("lines", [])
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            )
            if block_text.strip():
                text_block_events.append((bbox[1], block_text))

        # テキストとメディアを Y座標順に統合
        combined: list[tuple[float, str, object]] = []
        for y, txt in text_block_events:
            combined.append((y, "text", txt))
        for y, mtype, bbox in media_events:
            combined.append((y, mtype, bbox))
        combined.sort(key=lambda x: x[0])

        # ページのテキストとマーカーを構築
        for _, ctype, data in combined:
            if ctype == "text":
                text_parts.append(data)
            else:
                blk = _crop_and_save(
                    page, page_num, data, ctype,
                    regulation, version, assets_dir, label_counter,
                )
                image_blocks.append(blk)
                img_by_uid[blk.uid] = blk
                text_parts.append(f"\n[[IMG:{blk.uid}]]\n")

        text_parts.append("\n")  # ページ区切り

    doc.close()
    return "\n".join(text_parts), image_blocks


# --------------------------------------------------------------------------- #
# テキスト抽出（PDFなし / テキストのみモード）
# --------------------------------------------------------------------------- #

def extract_text_pdfplumber(pdf_path: Path) -> str:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber not installed.")
    with pdfplumber.open(pdf_path) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if text:
                pages.append(text)
    return "\n".join(pages)


def extract_text_pymupdf(pdf_path: Path) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed.")
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text("text") for page in doc]
    doc.close()
    return "\n".join(pages)


def extract_text_only(pdf_path: Path) -> str:
    """図表抽出なしでテキストのみを取得する（フォールバック）。"""
    if fitz is not None:
        return extract_text_pymupdf(pdf_path)
    if pdfplumber is not None:
        return extract_text_pdfplumber(pdf_path)
    raise RuntimeError("Neither PyMuPDF nor pdfplumber is installed.")


# --------------------------------------------------------------------------- #
# 段落パーサ（[[IMG:uid]] マーカー対応版）
# --------------------------------------------------------------------------- #

_PARA_HEADER = re.compile(
    r'^(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>[A-Z][^\n]{0,120})',
    re.MULTILINE,
)
_FOOTNOTE = re.compile(r'^\s*\d+\)\s+', re.MULTILINE)


def _infer_level(number: str) -> int:
    return number.count('.') + 1


def _infer_parent(number: str) -> Optional[str]:
    parts = number.rsplit('.', 1)
    return parts[0] if len(parts) > 1 else None


def _clean_text(text: str) -> str:
    text = _FOOTNOTE.sub('', text)
    lines = [line.strip() for line in text.splitlines()]
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


def parse_blocks(
    text_with_markers: str,
    regulation: str,
    image_blocks: list[ImageBlock],
) -> list:
    """
    [[IMG:uid]] マーカーを含むテキストから Paragraph + ImageBlock の
    混合リストを生成し、PDF上の出現順序を保持する。
    """
    img_by_uid = {b.uid: b for b in image_blocks}

    # 目次を除外（"1. Scope" が最初に現れる行まで）
    lines = text_with_markers.splitlines()
    start_line = 0
    for i, line in enumerate(lines):
        if re.match(r'^1\.\s+Scope', line.strip(), re.IGNORECASE):
            start_line = i
            break
    body = "\n".join(lines[start_line:])

    # マーカー位置と段落ヘッダ位置を両方記録してスキャン
    # アイテムを (pos_in_body, type, data) のリストとして構築
    events: list[tuple[int, str, object]] = []

    for m in _PARA_HEADER.finditer(body):
        events.append((m.start(), "para_header", m))

    for m in _IMG_MARKER_RE.finditer(body):
        uid = m.group(1)
        if uid in img_by_uid:
            events.append((m.start(), "image", uid))

    events.sort(key=lambda x: x[0])

    # 段落ヘッダとその内容テキストを収集
    para_headers = [(pos, data) for pos, t, data in events if t == "para_header"]
    result: list = []
    para_idx = 0

    # イベントを走査して Paragraph と ImageBlock を順番に出力
    for event_idx, (pos, etype, data) in enumerate(events):
        if etype == "image":
            result.append(img_by_uid[data])

        elif etype == "para_header":
            m = data
            number = m.group('number')
            title = m.group('title').strip()

            # コンテンツ範囲: このヘッダの終わりから次のヘッダ or 次のIMGマーカーまで
            next_para_start = None
            for npos, ntype, _ in events[event_idx + 1:]:
                if ntype == "para_header":
                    next_para_start = npos
                    break
                elif ntype == "image":
                    next_para_start = npos
                    break

            content_end = next_para_start if next_para_start else len(body)
            raw_content = body[m.end():content_end]
            # IMG マーカー自体はテキストから除去
            raw_content = _IMG_MARKER_RE.sub('', raw_content)
            text = _clean_text(raw_content)
            full_text = f"{title} {text}".strip() if text else title

            result.append(Paragraph(
                uid=make_uid(regulation, full_text),
                number=number,
                title=title,
                text=full_text,
                level=_infer_level(number),
                parent=_infer_parent(number),
            ))

    return result


# --------------------------------------------------------------------------- #
# 差分検出（テキスト段落のみ対象）
# --------------------------------------------------------------------------- #

THRESHOLD_SLID = 0.92
THRESHOLD_MODIFIED = 0.50


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class DiffEntry:
    type: str
    uid: str
    prev_uid: Optional[str]
    number: str
    title: str
    text_new: Optional[str]
    text_old: Optional[str]
    similarity: Optional[float]
    justification: Optional[str]


def diff_versions(old_paras: list[Paragraph], new_paras: list[Paragraph]) -> list[DiffEntry]:
    old_by_uid = {p.uid: p for p in old_paras}
    entries: list[DiffEntry] = []
    matched_old_uids: set[str] = set()
    matched_new_uids: set[str] = set()

    # Pass 1: UID完全一致
    for new_p in new_paras:
        if new_p.uid in old_by_uid:
            old_p = old_by_uid[new_p.uid]
            matched_old_uids.add(old_p.uid)
            matched_new_uids.add(new_p.uid)
            sim = _similarity(old_p.text, new_p.text)
            ctype = "MODIFIED" if sim < 1.0 and sim >= THRESHOLD_MODIFIED else "UNCHANGED"
            entries.append(DiffEntry(
                type=ctype, uid=new_p.uid, prev_uid=old_p.uid,
                number=new_p.number, title=new_p.title,
                text_new=new_p.text,
                text_old=old_p.text if ctype == "MODIFIED" else None,
                similarity=round(sim, 4) if ctype == "MODIFIED" else None,
                justification=None,
            ))

    # Pass 2: fuzzy マッチ
    unmatched_old = [p for p in old_paras if p.uid not in matched_old_uids]
    unmatched_new = [p for p in new_paras if p.uid not in matched_new_uids]
    used_old: set[str] = set()
    used_new: set[str] = set()

    candidates = []
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
        ctype = "SLID" if sim >= THRESHOLD_SLID else "MODIFIED"
        entries.append(DiffEntry(
            type=ctype, uid=new_p.uid, prev_uid=old_p.uid,
            number=new_p.number, title=new_p.title,
            text_new=new_p.text, text_old=old_p.text,
            similarity=round(sim, 4), justification=None,
        ))

    # Pass 3: ADDED / DELETED
    for new_p in unmatched_new:
        if new_p.uid not in matched_new_uids:
            entries.append(DiffEntry(
                type="ADDED", uid=new_p.uid, prev_uid=None,
                number=new_p.number, title=new_p.title,
                text_new=new_p.text, text_old=None,
                similarity=None, justification=None,
            ))
    for old_p in unmatched_old:
        if old_p.uid not in matched_old_uids:
            entries.append(DiffEntry(
                type="DELETED", uid=old_p.uid, prev_uid=old_p.uid,
                number=old_p.number, title=old_p.title,
                text_new=None, text_old=old_p.text,
                similarity=None, justification=None,
            ))

    entries.sort(key=lambda e: [int(x) for x in (e.number or "0").split('.')])
    return entries


def apply_diff_to_new(new_paras: list[Paragraph], diff_entries: list[DiffEntry]) -> list[Paragraph]:
    diff_by_uid = {e.uid: e for e in diff_entries}
    result = []
    for p in new_paras:
        entry = diff_by_uid.get(p.uid)
        if entry and entry.type in ("MODIFIED", "SLID"):
            p.modified = True
            p.prev_uid = entry.prev_uid
            p.justification = entry.justification
            if entry.type == "MODIFIED":
                p.status = "untranslated"
        result.append(p)
    return result


# --------------------------------------------------------------------------- #
# JSON 入出力
# --------------------------------------------------------------------------- #

DATA_DIR = Path(__file__).parent.parent / "data"
SITE_DIR = Path(__file__).parent.parent / "docs"


def _block_to_dict(block) -> dict:
    """Paragraph または ImageBlock を dict に変換する。"""
    return asdict(block)


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
# CLI コマンド
# --------------------------------------------------------------------------- #

@click.group()
def cli():
    """UN法規PDFの構造化・差分検出ツール"""


@cli.command()
@click.option('--pdf', required=True, type=click.Path(exists=True), help='入力PDFパス')
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--version', required=True, help='バージョン識別子 (例: Rev9)')
@click.option('--date', required=True, help='発行日 YYYY-MM-DD')
@click.option('--title', default='', help='法規タイトル（省略時はPDFから推定）')
@click.option('--no-media', is_flag=True, help='図表画像の抽出をスキップする')
def extract(pdf: str, reg: str, version: str, date: str, title: str, no_media: bool):
    """PDFからJSONを生成する（図表を画像として抽出・埋め込む）"""
    pdf_path = Path(pdf)
    assets_dir = SITE_DIR / reg / "assets" / "images"

    if no_media or fitz is None:
        click.echo("Extracting text only (no media) ...")
        raw_text = extract_text_only(pdf_path)
        blocks = parse_blocks(raw_text, reg, [])
    else:
        click.echo(f"Extracting text + media from {pdf_path} ...")
        click.echo(f"  Assets output: {assets_dir}")
        raw_text, image_blocks = extract_blocks_with_media(pdf_path, reg, version, assets_dir)
        click.echo(f"  Extracted {len(image_blocks)} image/table block(s).")
        blocks = parse_blocks(raw_text, reg, image_blocks)

    text_blocks = [b for b in blocks if isinstance(b, Paragraph)]
    img_blocks = [b for b in blocks if isinstance(b, ImageBlock)]
    click.echo(f"Found {len(text_blocks)} paragraph(s) + {len(img_blocks)} image block(s).")

    data = {
        "regulation": reg,
        "version": version,
        "source_date": date,
        "title": title or reg,
        "paragraphs": [_block_to_dict(b) for b in blocks],
    }
    save_structured(data, reg, version)


@cli.command()
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--from', 'from_ver', required=True, help='比較元バージョン')
@click.option('--to', 'to_ver', required=True, help='比較先バージョン')
def diff(reg: str, from_ver: str, to_ver: str):
    """2バージョン間の差分JSONを生成する（テキスト段落のみ比較）"""
    old_data = load_structured(reg, from_ver)
    new_data = load_structured(reg, to_ver)

    # 画像ブロックを除いてテキスト段落のみ比較
    def to_paras(data: dict) -> list[Paragraph]:
        return [
            Paragraph(**{k: v for k, v in p.items() if k in Paragraph.__dataclass_fields__})
            for p in data['paragraphs']
            if p.get('type', 'paragraph') == 'paragraph'
        ]

    old_paras = to_paras(old_data)
    new_paras = to_paras(new_data)

    click.echo(f"Comparing {from_ver} ({len(old_paras)} paras) → {to_ver} ({len(new_paras)} paras) ...")
    entries = diff_versions(old_paras, new_paras)

    counts: dict[str, int] = {}
    for e in entries:
        counts[e.type] = counts.get(e.type, 0) + 1
    click.echo(f"Diff result: {counts}")

    # 差分フラグを新版のブロックリストに反映（テキスト段落のみ更新）
    updated_new_paras = apply_diff_to_new(new_paras, entries)
    para_dict = {p.uid: p for p in updated_new_paras}

    new_blocks = []
    for b in new_data['paragraphs']:
        if b.get('type', 'paragraph') == 'paragraph' and b['uid'] in para_dict:
            new_blocks.append(_block_to_dict(para_dict[b['uid']]))
        else:
            new_blocks.append(b)  # 画像ブロックはそのまま保持
    new_data['paragraphs'] = new_blocks
    save_structured(new_data, reg, to_ver)

    diff_data = {
        "regulation": reg,
        "from_version": from_ver,
        "to_version": to_ver,
        "from_date": old_data.get('source_date'),
        "to_date": new_data.get('source_date'),
        "summary": counts,
        "changes": [asdict(e) for e in entries if e.type != "UNCHANGED"],
    }
    save_diff(diff_data, reg, from_ver, to_ver)


if __name__ == '__main__':
    cli()
