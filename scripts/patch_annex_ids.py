#!/usr/bin/env python3
"""
patch_annex_ids.py -- 既存 structured.json に annex_id を付与する（翻訳を保持）。

PDFを再解析してページ先頭の "Annex N" ヘッダーから各段落の附属書番号を特定し、
既存 structured.json の対応する段落に annex_id フィールドを追加する。
翻訳・修正フラグなど既存フィールドはすべて保持する。

Usage:
    python patch_annex_ids.py --pdf raw_pdf/R13/2020-01-01_rev1/R013r9e.pdf \
                               --reg R13 --version Rev9

    python patch_annex_ids.py --all
"""

import json
import re
from pathlib import Path

import click

try:
    import fitz
except ImportError:
    fitz = None

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

_PAGE_ANNEX_RE = re.compile(r'^Annex\s+(\d+)', re.IGNORECASE)
_PARA_HEADER = re.compile(
    r'^\s*(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>[A-Z“”"][^\n]{0,120})',
    re.MULTILINE,
)
_PAGE_HEADER_RE = re.compile(r'^E/ECE/[^\n]*$', re.MULTILINE)


def _detect_page_annex_id(page) -> int | None:
    """ページ先頭の数行から附属書番号を検出する。"""
    text = page.get_text("text")
    past_ece = False
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('E/') or line.startswith('ECE/') or line.startswith('ECE-TRANS-'):
            past_ece = True
            continue
        if line.isdigit():
            continue
        if not past_ece:
            break
        if ',' in line or 'paragraph' in line.lower():
            break
        m = _PAGE_ANNEX_RE.match(line)
        if m:
            return int(m.group(1))
        break
    return None


def _is_valid_para_number(number: str) -> bool:
    try:
        return all(int(x) >= 1 for x in number.split('.'))
    except ValueError:
        return False


def _num_tuple(number: str) -> tuple:
    try:
        return tuple(int(x) for x in number.split('.'))
    except ValueError:
        return ()


def _is_valid_sequence(last_tuple: tuple, new_tuple: tuple) -> bool:
    if not last_tuple:
        return True
    if new_tuple >= last_tuple:
        return True
    if new_tuple[0] < last_tuple[0]:
        return True
    return False


def build_uid_to_annex_map(pdf_path: Path, regulation: str) -> dict[str, int]:
    """PDF から段落UID → annex_id のマッピングを構築する。"""
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed.")

    import hashlib

    def make_uid(reg: str, text: str) -> str:
        normalized = " ".join(text.split())
        digest = hashlib.sha256(f"{reg}:{normalized}".encode()).hexdigest()
        return f"{reg}-{digest[:8]}"

    def clean_text(text: str) -> str:
        lines = [l.strip() for l in text.splitlines()]
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

    doc = fitz.open(str(pdf_path))
    text_parts = []
    last_annex_id = None

    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        ann_id = _detect_page_annex_id(page)
        if ann_id is not None and ann_id != last_annex_id:
            text_parts.append(f"\n[[ANNEX:{ann_id}]]\n")
            last_annex_id = ann_id
        text_parts.append(page.get_text("text"))
        text_parts.append("\n")

    doc.close()
    full_text = "\n".join(text_parts)

    # 目次を除外
    lines = full_text.splitlines()
    start_line = 0
    for i, line in enumerate(lines):
        if re.match(r'^1\.\s+Scope', line.strip(), re.IGNORECASE):
            start_line = i
            break
    body = "\n".join(lines[start_line:])
    body = _PAGE_HEADER_RE.sub('', body)

    # イベント収集
    _annex_marker_re = re.compile(r'\[\[ANNEX:(\d+)\]\]')
    events = []
    for m in _PARA_HEADER.finditer(body):
        if m.group('title').strip().startswith('E/ECE/'):
            continue
        if not _is_valid_para_number(m.group('number')):
            continue
        events.append((m.start(), "para", m))
    for m in _annex_marker_re.finditer(body):
        events.append((m.start(), "annex", int(m.group(1))))
    events.sort(key=lambda x: x[0])

    uid_to_annex: dict[str, int] = {}
    current_annex_id = None
    last_num_tuple: tuple = ()

    for event_idx, (pos, etype, data) in enumerate(events):
        if etype == "annex":
            current_annex_id = data
        elif etype == "para":
            m = data
            number = m.group('number')
            title = m.group('title').strip()
            num_tuple = _num_tuple(number)
            if not _is_valid_sequence(last_num_tuple, num_tuple):
                continue
            last_num_tuple = num_tuple

            next_start = None
            for npos, ntype, _ in events[event_idx + 1:]:
                if ntype in ("para", "annex"):
                    next_start = npos
                    break
            content_end = next_start if next_start else len(body)
            raw = body[m.end():content_end]
            raw = re.sub(r'\[\[ANNEX:\d+\]\]', '', raw)
            raw = re.sub(r'\[\[IMG:[^\]]+\]\]', '', raw)
            text = clean_text(raw)
            full_text_para = f"{title} {text}".strip() if text else title

            uid = make_uid(regulation, full_text_para)
            if current_annex_id is not None:
                uid_to_annex[uid] = current_annex_id

    return uid_to_annex


def patch_structured_json(regulation: str, version: str, uid_to_annex: dict[str, int]) -> int:
    """structured.json に annex_id を付与する（翻訳など既存フィールドは保持）。"""
    path = DATA_DIR / regulation / version / "structured.json"
    data = json.loads(path.read_text(encoding='utf-8'))

    updated = 0
    for p in data['paragraphs']:
        if p.get('type') != 'paragraph':
            continue
        uid = p.get('uid', '')
        if uid in uid_to_annex:
            p['annex_id'] = uid_to_annex[uid]
            updated += 1

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return updated


KNOWN_PDFS = {
    # R13 は正しいRevision 9 PDFが未入手のため除外 (R013r9e.pdf は別バージョン)
    ("R79", "Rev5"): BASE_DIR / "raw_pdf/R79/2023-01-01_Rev5/R079r5e.pdf",
    ("R179", "Rev1"): BASE_DIR / "raw_pdf/R179/2026-03-10_Rev1/ECE-TRANS-WP.29-2026-36e_R179.pdf",
}


@click.group()
def cli():
    """structured.json への annex_id パッチツール"""


@cli.command()
@click.option('--pdf', required=True, type=click.Path(exists=True))
@click.option('--reg', required=True)
@click.option('--version', required=True)
def patch(pdf: str, reg: str, version: str):
    """指定PDFから annex_id を検出して structured.json をパッチする。"""
    click.echo(f"Scanning {pdf} ...")
    uid_map = build_uid_to_annex_map(Path(pdf), reg)
    click.echo(f"  Found annex_id for {len(uid_map)} UIDs.")
    n = patch_structured_json(reg, version, uid_map)
    click.echo(f"  Updated {n} paragraphs in {reg}/{version}/structured.json")


@cli.command(name='all')
def patch_all():
    """全規制のstructured.jsonをパッチする（KNOWN_PDFSに登録されたもの）。"""
    for (reg, version), pdf_path in KNOWN_PDFS.items():
        if not pdf_path.exists():
            click.echo(f"[SKIP] PDF not found: {pdf_path}")
            continue
        click.echo(f"Processing {reg} {version} ...")
        uid_map = build_uid_to_annex_map(pdf_path, reg)
        click.echo(f"  Found annex_id for {len(uid_map)} UIDs.")
        n = patch_structured_json(reg, version, uid_map)
        click.echo(f"  Updated {n} paragraphs.")


if __name__ == '__main__':
    cli()
