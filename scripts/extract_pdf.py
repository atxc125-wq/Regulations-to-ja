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
from collections import Counter
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
    annex_id: Optional[int] = None


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
    annex_id: Optional[int] = None


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
# [[ANNEX:N]] マーカーパターン（ページ先頭の附属書番号）
_ANNEX_MARKER_RE = re.compile(r'\[\[ANNEX:(\d+)\]\]')
# ページ先頭の附属書ヘッダー検出
_PAGE_ANNEX_RE = re.compile(r'^Annex\s+(\d+)', re.IGNORECASE)
# ページ番号行（"21" や "page 21" の両形式に対応）
_PAGE_NUM_HEADER_RE = re.compile(r'^(?:page\s+)?\d+\s*$', re.IGNORECASE)
# 一部の古い版では各ページに "Regulation No. N" の繰り返しヘッダーが入る
_REG_NO_HEADER_RE = re.compile(r'^(?:UN\s+)?Regulation\s+No\.?\s*\d+\s*$', re.IGNORECASE)


def _detect_page_annex_id(page) -> Optional[int]:
    """ページ先頭の数行から附属書番号を検出する。

    UN法規PDFでは附属書ページの先頭に "E/ECE/..." ヘッダーがあり、
    その直後の行に "Annex N" が現れる。本文ページには現れないため
    この位置での検出を附属書判定に使う。
    カンマや 'paragraph' を含む行（本文中の参照）は除外する。
    ページ番号や "Regulation No. N" の繰り返しヘッダー行はスキップする。
    """
    text = page.get_text("text")
    past_ece = False
    skipped_reg_header = False
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('E/') or line.startswith('ECE/') or line.startswith('ECE-TRANS-'):
            past_ece = True
            continue
        if _PAGE_NUM_HEADER_RE.match(line):
            continue
        if not past_ece:
            break
        if not skipped_reg_header and _REG_NO_HEADER_RE.match(line):
            skipped_reg_header = True
            continue
        if ',' in line or 'paragraph' in line.lower():
            break
        m = _PAGE_ANNEX_RE.match(line)
        if m:
            return int(m.group(1))
        break
    return None


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

    last_annex_id: Optional[int] = None
    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]

        # ページ先頭の附属書ヘッダーを検出し、変化があればマーカーを挿入
        page_annex_id = _detect_page_annex_id(page)
        if page_annex_id is not None and page_annex_id != last_annex_id:
            text_parts.append(f"\n[[ANNEX:{page_annex_id}]]\n")
            last_annex_id = page_annex_id

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
            line_texts = []
            for line in block.get("lines", []):
                line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                if line_text.strip():
                    line_texts.append(line_text)
            block_text = "\n".join(line_texts)
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
    r'^\s*(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>[A-Z"“”][^\n]{0,120})',
    re.MULTILINE,
)
_FOOTNOTE = re.compile(r'^\s*\d+\)\s+', re.MULTILINE)

# UN法規定義章のインライン定義ヘッダー: “2.x.  “term” means...”
# UN文書の書式: 定義番号 + ピリオド + 2スペース以上 + 引用符
# 参照（”paragraph 2.5. above”）と区別するため2スペース以上を要求
_INLINE_DEF_SPLIT = re.compile(
    r'(\d+\.\d+(?:\.\d+)*)\.[ \t]{2,4}(?=["\u201c\u201d])',
)

# ページヘッダーパターン（除去対象）
# 旧形式: E/ECE/324/... 新形式（WP.29文書）: ECE/TRANS/WP.29/...（先頭の "E/" なし）
# 一部の古い版では各ページに "Regulation No. N" / "page N" の繰り返しヘッダーも入る
_PAGE_HEADER_RE = re.compile(
    r'^(?:(?:E/)?ECE/[^\n]*|(?:UN\s+)?Regulation\s+No\.?\s*\d+\s*|page\s+\d+\s*)$',
    re.MULTILINE | re.IGNORECASE,
)


def _is_valid_para_number(number: str) -> bool:
    """段落番号として有効かどうかを検証する。
    UN法規の段落番号は正整数ドット区切りで、各コンポーネントは 1 以上。
    図中のY軸ラベル（0.7, 0.3 等）を除外するためのガード。
    """
    try:
        return all(int(x) >= 1 for x in number.split('.'))
    except ValueError:
        return False


def _num_tuple(number: str) -> tuple:
    """段落番号を整数タプルに変換する。"""
    try:
        return tuple(int(x) for x in number.split('.'))
    except ValueError:
        return ()


def _is_valid_sequence(last_tuple: tuple, new_tuple: tuple) -> bool:
    """
    new_tuple が last_tuple の有効な後続番号かを判定する。

    有効なケース:
      - 子・兄弟・叔父方向（単調増加）: new_tuple > last_tuple
      - 附属書リスタート: 最初の成分が大幅減少（例: (12,...) → (1,...) や (1,...)）
        ただし 0 始まりは _is_valid_para_number で除去済み

    無効なケース:
      - 図のラベル等で軽微な後退: (5,2,1,28,5) → (5,2,1,27,...) など
    """
    if not last_tuple:
        return True
    if new_tuple >= last_tuple:
        return True
    # 附属書リスタート: 最初の成分が現在より小さく、かつ現在の最初の成分が大きい場合
    # 例: 12章終了後に Annex 1 が始まる (last=(12,...) → new=(1,...))
    if new_tuple[0] < last_tuple[0]:
        return True  # 章番号の後退は附属書移行として許可
    return False


_NOISE_TITLE_RE = re.compile(
    r'^Note by the secretariat\b'
    r'|(?:E/)?ECE/(?:TRANS/WP\.29)?/\S'
    r'|TRANS/WP\.29/\S',
    re.IGNORECASE,
)


def _looks_like_noise(title: str) -> bool:
    """脚注・文書参照・図ラベル残骸など、実質的な見出しではないテキストを判定する。
    章タイトルとして妥当な短い名詞句かどうかを内容ベースで判定する
    （脱稿前は出現位置ベースで判定していたが、サブ番号を持たない短い章
    （例: "9. Production definitively discontinued"）を誤って脚注と
    みなし内容を丸ごと欠落させてしまうため、内容ベースの判定に変更）。
    """
    if _NOISE_TITLE_RE.search(title):
        return True
    if not re.search(r'[A-Za-z]{3,}', title):
        return True  # 英字3文字以上の単語を含まない（図ラベル等の残骸）
    # TOC のドットリーダー＋ページ番号残骸（例: "Place ........... 16"）を
    # 除去してから長さを判定する。除去前の生文字列で判定すると、短い欄名
    # （Place/Date/Signature 等）がドットの分だけ長くなり誤って脚注と
    # みなされてしまう。
    title_clean = re.sub(r'\s*\.{4,}.*', '', title).rstrip('. ')
    if len(title_clean) > 100:
        return True  # 見出し1行に収まらない長さ＝地の文や脚注の誤検出
    return False


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


def _strip_page_headers(text: str) -> str:
    """E/ECE/で始まるページヘッダー行をテキストから除去する。"""
    return _PAGE_HEADER_RE.sub('', text)


def _split_inline_definitions(para: 'Paragraph', regulation: str) -> list:
    """段落テキスト内のインライン定義ヘッダー（; 2.x. "term"）で分割する。
    分割不要な場合は [para] を返す。
    """
    text = para.text
    matches = list(_INLINE_DEF_SPLIT.finditer(text))
    if not matches:
        return [para]

    result = []

    # 最初のマッチ前の部分 → 元の段落のテキストを縮小
    first_text = text[:matches[0].start()].rstrip(' ;:\n')
    if first_text:
        para.text = first_text
        para.uid = make_uid(regulation, first_text)
        result.append(para)

    # 各インライン定義 → 新規 Paragraph
    for i, m in enumerate(matches):
        emb_number = m.group(1)
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        emb_content = text[content_start:content_end].rstrip(' ;:\n')

        # タイトル: “ term “ の部分を抽出（ASCII引用符 U+0022 対応）
        title_m = re.match(r'”([^”]+)”', emb_content)
        emb_title = (f'"{title_m.group(1).strip()}"'
                     if title_m else emb_content[:80].rstrip())

        result.append(Paragraph(
            uid=make_uid(regulation, emb_content),
            number=emb_number,
            title=emb_title,
            text=emb_content,
            level=_infer_level(emb_number),
            parent=_infer_parent(emb_number),
            annex_id=para.annex_id,
        ))

    return result


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
    # 番号とタイトルが別行に分かれているレイアウトにも対応するため、
    # 当該行と次行を連結した上でマッチさせる。
    lines = text_with_markers.splitlines()
    start_line = 0
    for i, line in enumerate(lines):
        window = line.strip()
        if i + 1 < len(lines):
            window += ' ' + lines[i + 1].strip()
        if re.match(r'^1\.?\s+Scope', window, re.IGNORECASE):
            start_line = i
            break
    body = "\n".join(lines[start_line:])

    # ページヘッダー（E/ECE/...）をテキストから除去
    body = _strip_page_headers(body)

    # マーカー位置と段落ヘッダ位置を両方記録してスキャン
    events: list[tuple[int, str, object]] = []

    for m in _PARA_HEADER.finditer(body):
        # ページヘッダー由来の偽陽性を除去（タイトルがE/ECE/またはECE/で始まる場合）
        if re.match(r'(?:E/)?ECE/', m.group('title').strip()):
            continue
        # 図中のラベル（0.7, 0.3 等）を除去: 各コンポーネントは 1 以上が必須
        if not _is_valid_para_number(m.group('number')):
            continue
        events.append((m.start(), "para_header", m))

    for m in _IMG_MARKER_RE.finditer(body):
        uid = m.group(1)
        if uid in img_by_uid:
            events.append((m.start(), "image", uid))

    for m in _ANNEX_MARKER_RE.finditer(body):
        events.append((m.start(), "annex_marker", int(m.group(1))))

    events.sort(key=lambda x: x[0])

    raw_result: list = []
    last_num_tuple: tuple = ()  # 単調増加チェック用
    current_annex_id: Optional[int] = None  # ページヘッダーから検出した附属書番号

    # イベントを走査して Paragraph と ImageBlock を順番に出力
    for event_idx, (pos, etype, data) in enumerate(events):
        if etype == "annex_marker":
            current_annex_id = data

            # マーカーから次のイベント（段落見出し・画像・次の附属書マーカー）
            # までの先頭テキストを欠落させずに保持する。附属書見出し直後は
            # 通常 "Annex N" + タイトル行のみで内容は無いが、目次の項番号が
            # 同一行に連結している等の理由で最初の実段落が検出できない場合
            # （例: "Contents 1. Preface ...... 99" が改行されず1行になる）、
            # 本来の実質的な前文コンテンツがここに含まれることがあるため。
            lead_end = None
            for npos, ntype, _ in events[event_idx + 1:]:
                lead_end = npos
                break
            content_end = lead_end if lead_end is not None else len(body)
            raw_content = body[pos:content_end]
            raw_content = _ANNEX_MARKER_RE.sub('', raw_content)
            cleaned = _clean_text(raw_content)
            cleaned = re.sub(r'^Annex\s+\d+\s*', '', cleaned).strip()
            if cleaned:
                raw_result.append(Paragraph(
                    uid=make_uid(regulation, cleaned),
                    number='',
                    title=cleaned[:80],
                    text=cleaned,
                    level=1,
                    parent=None,
                    annex_id=current_annex_id,
                ))

        elif etype == "image":
            img = img_by_uid[data]
            img.annex_id = current_annex_id
            raw_result.append(img)

        elif etype == "para_header":
            m = data
            number = m.group('number')
            title = m.group('title').strip()

            # 単調増加チェック: 同一章内で番号が後退するケースは図内ラベル等と判断
            num_tuple = _num_tuple(number)
            if not _is_valid_sequence(last_num_tuple, num_tuple):
                continue
            last_num_tuple = num_tuple

            # コンテンツ範囲: このヘッダの終わりから次のヘッダ・次のIMGマーカー・
            # 次の附属書マーカーまで（附属書境界を越えて内容が混ざるのを防ぐ）
            next_para_start = None
            for npos, ntype, _ in events[event_idx + 1:]:
                if ntype in ("para_header", "image", "annex_marker"):
                    next_para_start = npos
                    break

            content_end = next_para_start if next_para_start else len(body)
            raw_content = body[m.end():content_end]
            raw_content = _IMG_MARKER_RE.sub('', raw_content)
            raw_content = _ANNEX_MARKER_RE.sub('', raw_content)
            text = _clean_text(raw_content)
            full_text = f"{title} {text}".strip() if text else title

            raw_result.append(Paragraph(
                uid=make_uid(regulation, full_text),
                number=number,
                title=title,
                text=full_text,
                level=_infer_level(number),
                parent=_infer_parent(number),
                annex_id=current_annex_id,
            ))

    # ポストプロセス: インライン定義を分割し、脚注・文書参照等のノイズ段落を除去
    # ページ脚注（各ページに同一文面が繰り返される注記）はタイトルが複数回
    # 重複出現することで見分けられる（実章タイトルが偶然重複することはない）。
    # ただし附属書ごとに独立して「1. Test conditions」等の汎用的な短い見出しを
    # 持つことは珍しくないため、重複判定は (annex_id, title) 単位で行う
    # （附属書をまたいだ同名見出しの偶然の一致を誤って脚注とみなさないため）。
    # さらに、附属書内のページ見出し（"Annex 3" 等）が本文の冒頭に紛れ込んで
    # 同一タイトルとして複数回検出されるケースや、連続する図のキャプションが
    # 定型文を共有して見出しが一致してしまうケースがあるため、タイトルが
    # 重複する候補は本文全体の類似度も確認し、実際にほぼ同一の文面が繰り返
    # されている（= ページ脚注である）場合のブロックのみを重複ノイズと判定
    # する（グループ単位ではなく、高い類似度を持つペアそのものだけを除去
    # することで、たまたま同じタイトルを共有する別々の実段落を保護する）。
    DUP_TEXT_SIM_THRESHOLD = 0.9
    candidates_by_key: dict = {}
    for b in raw_result:
        if isinstance(b, ImageBlock):
            continue
        if b.level == 1 and re.fullmatch(r'\d{1,2}', b.number):
            candidates_by_key.setdefault((b.annex_id, b.title), []).append(b)

    duplicate_block_ids = set()
    for group in candidates_by_key.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if SequenceMatcher(None, group[i].text, group[j].text).ratio() >= DUP_TEXT_SIM_THRESHOLD:
                    duplicate_block_ids.add(id(group[i]))
                    duplicate_block_ids.add(id(group[j]))

    result: list = []
    for block in raw_result:
        if isinstance(block, ImageBlock):
            result.append(block)
            continue
        # ノイズ候補: level=1、数字のみの番号（脚注記号・文書参照はこの形を取る）
        if (block.level == 1 and re.fullmatch(r'\d{1,2}', block.number)
                and (_looks_like_noise(block.title) or id(block) in duplicate_block_ids)):
            continue
        # インライン定義を分割して追加
        for split_block in _split_inline_definitions(block, regulation):
            result.append(split_block)

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
