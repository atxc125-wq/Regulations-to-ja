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


def reg_sort_key(reg: str):
    """法規一覧の表示順を決めるソートキー。

    R.E.3（車両全体の定義・分類を定める基礎文書）は常に先頭に固定し、
    残りは R<番号> の数値順に並べる。
    """
    import re as _re
    if reg == "R.E.3":
        return (0, 0, reg)
    m = _re.match(r'^R(\d+)', reg)
    if m:
        return (1, int(m.group(1)), reg)
    return (2, 0, reg)


_TOC_TRAILING_DOTS_RE = None  # 遅延初期化


def _is_toc_title_overflow(title: str, text: str) -> bool:
    """title フィールドが目次のドットリーダー手前で途切れ、続き＋ドット
    リーダー（＋ページ番号）がそのまま text フィールドに漏れている行を検出する。

    例（R.E.3）: title="Standard annex on the procedure for determining the
    "H" point and the actual torso angle for"（ドットの直前で切れている）、
    text="...for seating positions in motor vehicles ....................."。

    text 全体が「本文（コロンを含まない） + 4連続以上のドット + 任意の
    ページ番号」のみで構成される場合に限り目次行とみなす。コロンを含む場合
    （証明書フォームの "Signed: ......... Date: ........." 等）は除外する
    ことで、本物の記入欄を誤って隠さないようにする。
    """
    global _TOC_TRAILING_DOTS_RE
    import re as _re
    if _TOC_TRAILING_DOTS_RE is None:
        _TOC_TRAILING_DOTS_RE = _re.compile(r'^(?P<core>.*?)\.{4,}(?:\s*\d{1,3})?\s*$', _re.DOTALL)
    m = _TOC_TRAILING_DOTS_RE.match(text or "")
    if not m:
        return False
    return ":" not in m.group("core")


def find_toc_dup_uids(paragraphs: list[dict]) -> set:
    """目次のドットリーダー行（"Scope .......... 5" 等）を検出する。

    同じ (annex_id, number) を持つ「本物」の章見出し（ドットリーダーを含まない）
    が別に存在する場合のみ、ドットリーダー行を目次の重複とみなす。Annex 1 の
    通信書式の欄名（"Place ........."、"Signature........." 等）は同じ番号を
    持つ本物の対応段落が存在しないため、ここでは重複と判定されず、誤って
    完全非表示にされることはない。

    判定は基本的に title フィールドのみで行う（text フィールドには証明書
    フォームの記入欄（"Signed: ......... Date: ........."）等、目次とは無関係
    なドットリーダーが含まれることがあり、これを単純に重複判定に使うと
    本物の記入欄を誤って消してしまう）。ただし title が長い目次行の途中で
    切れてドットリーダー自体が text 側にしか現れないケース（_is_toc_title_
    overflow 参照）は、text 全体がコロンを含まない「本文＋ドット＋ページ番号」
    だけで構成されることを確認したうえで例外的に重複とみなす。
    """
    import re as _re
    buckets: dict = {}
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_is_proposal_only"):
            continue
        number = (p.get("number") or "").strip()
        if p.get("level", 1) != 1 or not _re.fullmatch(r"\d{1,2}", number):
            continue
        buckets.setdefault((p.get("annex_id"), number), []).append(p)

    dup_uids = set()
    for group in buckets.values():
        if len(group) < 2:
            continue
        has_clean = any("......" not in (g.get("title") or "") for g in group)
        if not has_clean:
            continue
        for g in group:
            title = g.get("title") or ""
            if "......" in title or _is_toc_title_overflow(title, g.get("text") or ""):
                dup_uids.add(g.get("uid"))
    return dup_uids


def find_pseudo_number_dup_uids(paragraphs: list[dict]) -> set:
    """ページ番号が段落番号として誤認識された見出し
    （例: ページ番号 "106" の直後に来る "Annex 9 - Appendix 1" のような
    タイトル行が連結され、見かけ上 number="106" の段落になってしまうケース）
    のうち、内容を持たない重複（同一タイトルの繰り返し走りヘッダー等）だけを
    ノイズと判定する。

    extract_pdf.py 側の脚注重複検出（同一 (annex_id, title) 内で本文の類似度
    >= 0.9 のペアを重複とみなす）と同じロジックを、3桁以上の番号を持つ段落
    （誤認識防止のため脚注重複検出の対象外にしていたグループ）にも適用する。
    実質的な本文が付随する場合は重複ペアが見つからず、ここではノイズと判定
    されない（"Annex 9 - Appendix 1" や "F (MHz)" の定義など、ページ番号が
    たまたま紛れ込んだだけの本物の見出し・内容を保護する）。
    """
    import re as _re
    from difflib import SequenceMatcher

    buckets: dict = {}
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_is_proposal_only"):
            continue
        number = (p.get("number") or "").strip()
        if not _re.fullmatch(r"\d{3,}", number):
            continue
        key = (p.get("annex_id"), (p.get("title") or "").strip())
        buckets.setdefault(key, []).append(p)

    dup_uids = set()
    for group in buckets.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                sim = SequenceMatcher(None, a.get("text") or "", b.get("text") or "").ratio()
                if sim >= 0.9:
                    dup_uids.add(a.get("uid"))
                    dup_uids.add(b.get("uid"))
    return dup_uids


def is_nav_noise(
    p: dict,
    toc_dup_uids: set | None = None,
    pseudo_num_dup_uids: set | None = None,
) -> bool:
    """ナビゲーションツリーに表示すべきでないノイズ段落かどうかを判定する。
    - PDFページヘッダー（文書番号行、"E/ECE/..."）
    - 目次ドット行（"......"）。ただし本物の対応段落がない場合は欄名等の
      実内容とみなし非表示にしない（find_toc_dup_uids 参照）。
    - PDFページ走りヘッダー（3桁以上の純整数番号 = ページ番号が段落番号に
      誤認識されたもの）。ただし内容を伴う重複でない場合は実質的な見出し・
      内容とみなし非表示にしない（find_pseudo_number_dup_uids 参照）。
    """
    import re as _re
    text = (p.get("text") or "").strip()
    title = (p.get("title") or "").strip()
    number = (p.get("number") or "").strip()
    if (text.startswith("E/ECE/") or text.startswith("ECE/")
            or title.startswith("E/ECE/") or title.startswith("ECE/")):
        return True
    if "......" in title or "......" in text:
        if toc_dup_uids is not None:
            return p.get("uid") in toc_dup_uids
        return True
    if _re.fullmatch(r"\d{3,}", number):
        if pseudo_num_dup_uids is not None:
            return p.get("uid") in pseudo_num_dup_uids
        return True
    return False


_NOISE_TITLE_RE_BH = None  # 遅延初期化


def _looks_like_noise_bh(title: str) -> bool:
    """脚注・文書参照・図ラベル残骸など、実質的な見出しではないテキストを判定する
    （extract_pdf.py の _looks_like_noise と同じ判定基準。位置ベースではなく
    内容ベースで判定することで、サブ番号を持たない短い章を誤って脚注とみなし
    内容を隠してしまう問題を避ける）。
    """
    global _NOISE_TITLE_RE_BH
    import re
    if _NOISE_TITLE_RE_BH is None:
        _NOISE_TITLE_RE_BH = re.compile(
            r'^Note by the secretariat\b'
            r'|(?:E/)?ECE/(?:TRANS/WP\.29)?/\S'
            r'|TRANS/WP\.29/\S'
            # 多くの規則で繰り返される定型脚注（"As defined in the Consolidated
            # Resolution on the Construction of Vehicles (R.E.3)..."）。文書参照
            # 番号の表記がページごとに僅かに異なるため完全一致での重複検出に
            # 漏れることがあり、内容ベースでも直接検出する。
            r'|^As defined in the Consolidated Resolution on the Construction of Vehicles\b'
            # 同様に多くの規則で繰り返される定型脚注（締約国の識別番号一覧への
            # 参照）。"distinguishing"/"distinguish"の表記ゆれがある。
            r'|^The distinguish(?:ing)? numbers? of the Contracting Parties\b',
            re.IGNORECASE,
        )
    if _NOISE_TITLE_RE_BH.search(title):
        return True
    if not re.search(r'[A-Za-z]{3,}', title):
        return True
    # ドットリーダー残骸を除去してから長さ判定（extract_pdf.py 側と同じ理由）
    title_clean = re.sub(r'\s*\.{4,}.*', '', title).rstrip('. ')
    if len(title_clean) > 100:
        return True
    return False


def mark_footnote_noise(paragraphs: list[dict]) -> None:
    """level=1 かつ数字のみ番号の段落のうち、脚注・文書参照・ページ走り等の
    ノイズと判定された段落を _nav_hidden=True にする（インプレース変更）。

    同一タイトルが複数回出現する場合もページ脚注（繰り返し注記）と判定する
    （実章タイトルが文書内で偶然重複することはないため）。ただし附属書ごとに
    独立して同じ汎用見出し（例: "Test conditions"）を持つことがあるため、
    重複判定は (annex_id, title) 単位で行う。さらに、附属書内のページ見出し
    （"Annex 3" 等）が本文冒頭に紛れ込んで同一タイトルとして複数回検出される
    ケースや、連続する図のキャプションが定型文を共有して見出しが一致してし
    まうケースがあるため、本文全体の類似度も確認し、実際にほぼ同一の文面が
    繰り返されている（＝ページ脚注である）ペアそのものだけを重複ノイズと判定
    する（グループ単位ではなく、高い類似度を持つペアだけを除去することで、
    たまたま同じタイトルを共有する別々の実段落を保護する）。
    extract_pdf.py の parse_blocks() 内の同名フィルタと判定基準を揃えている。
    """
    import re
    from difflib import SequenceMatcher
    DUP_TEXT_SIM_THRESHOLD = 0.9
    candidates = [
        p for p in paragraphs
        if p.get("type") != "image" and not p.get("_is_proposal_only")
        and p.get("level", 1) == 1
        and re.fullmatch(r"\d{1,2}", p.get("number", ""))
    ]
    groups: dict = {}
    for p in candidates:
        key = (p.get("annex_id"), p.get("title", ""))
        groups.setdefault(key, []).append(p)

    duplicate_ids = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                sim = SequenceMatcher(None, group[i].get("text", ""), group[j].get("text", "")).ratio()
                if sim >= DUP_TEXT_SIM_THRESHOLD:
                    duplicate_ids.add(id(group[i]))
                    duplicate_ids.add(id(group[j]))

    for p in candidates:
        if _looks_like_noise_bh(p.get("title", "")) or id(p) in duplicate_ids:
            p["_nav_hidden"] = True


def mark_annex_paragraphs(paragraphs: list[dict]) -> None:
    """附属書（Annex）に属する段落に _in_annex フラグを付与する。

    _nav_hidden（TOC ノイズ）付与後に呼ぶこと。
    番号後退ヒューリスティックで附属書ゾーンを検出する。
    annex_id フィールドは mark_annex_ids() で附属書ID付与に使用する。

    脚注がページ下部の参照テキスト（"1 As defined in the Consolidated
    Resolution..." 等）を本文の見出しと誤認識し、たまたま小さい番号
    （例: "1"）を持ってしまうことがある。これを後退と誤判定して附属書
    ゾーンに入ったと即断すると、以降の本物の章（例: 3, 4, 5 章）が軒並み
    附属書扱いになってしまう。そこで後退を検出しても即座に確定させず、
    それ以降ずっと番号が戻らない（=本物の附属書境界）場合のみ確定する。
    """
    items: list[tuple[int, int, int]] = []  # (paragraphs内インデックス, top番号, level)
    for idx, p in enumerate(paragraphs):
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
        items.append((idx, top, level))

    max_top = 0
    seen_body = False  # level>=2 段落が出現したら True（本文に入ったと判定）
    annex_start_idx = None

    for k, (idx, top, level) in enumerate(items):
        if level >= 2:
            seen_body = True

        if (annex_start_idx is None and seen_body and max_top >= 2
                and top < max_top // 2 + 1):
            # 後退を検出。以降ずっと max_top を超えなければ本物の附属書境界と確定する。
            if not any(later_top > max_top for _, later_top, _ in items[k + 1:]):
                annex_start_idx = idx
                break

        if annex_start_idx is None and seen_body:
            max_top = max(max_top, top)

    if annex_start_idx is not None:
        for idx, p in enumerate(paragraphs):
            if idx >= annex_start_idx and p.get("type") != "image":
                p["_in_annex"] = True


def _is_toc_like_entry(p: dict) -> bool:
    """段落がドットリーダー＋ページ番号だけの目次行（実質的な本文を持たない）
    かどうかを判定する。ドットリーダー（"...." または "……"）をすべて除去し、
    末尾のページ番号を取り除いた「素の文字列」が title と text でほぼ同じ長さ
    であれば、本文を持たない目次行と判定する。"Introduction"・"Preamble" など
    タイトルだけが短く、本文（text）には実質的な内容が続く段落を、目次セクション
    の番号レンジ内に紛れ込んだという理由だけで誤って隠してしまわないようにする。
    """
    import re as _re

    def _bare(s: str) -> str:
        s = _re.sub(r'[.…]{2,}', ' ', s)
        s = _re.sub(r'\s+\d+\s*$', '', s)
        return _re.sub(r'\s+', ' ', s).strip()

    bare_title = _bare(p.get("title") or "")
    bare_text = _bare(p.get("text") or "")
    return len(bare_text) <= len(bare_title) + 20


def build_annex_tree(paragraphs: list[dict], annex_titles_fallback: dict | None = None) -> list[dict]:
    """附属書ツリーを構築する（左ペイン用）。
    整数番号の第2出現ブロック（附属書 TOC セクション）から各附属書のタイトルを取得する。
    _nav_hidden フラグ付与前に呼ぶこと。

    annex_titles_fallback: TOCセクションから附属書タイトルが1件も取れない場合
    （例: "Annex N - Title" 形式のTOCで附属書見出しが段落として捕捉されない旧版PDF）
    に使用するタイトル辞書。structured.json の "annex_titles" フィールド由来。
    形式: {"1": {"title": "...", "title_ja": "..."}, ...}
    """
    import re as _re

    # num="1" の出現位置を列挙して TOC セクション境界を特定
    occ1 = [i for i, p in enumerate(paragraphs)
            if p.get("type") != "image" and p.get("number", "") == "1"]

    entries: dict = {}
    candidate_indices: list[int] = []
    annex_toc_start = None
    annex_toc_end = None
    if len(occ1) >= 2:
        # 第2出現 (附属書TOCの先頭) ～ 第3出現 (本文の先頭) が附属書TOCセクション
        annex_toc_start = occ1[1]
        annex_toc_end   = occ1[2] if len(occ1) >= 3 else len(paragraphs)

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
            candidate_indices.append(i)
            if n not in entries:
                entries[n] = {
                    "number": n,
                    "title": clean,
                    "title_ja": (p.get("title_ja") or "").rstrip(". "),
                }

    toc_section_valid = bool(entries)
    if annex_titles_fallback:
        # 段落に検出済みの annex_id（extract_pdf.py 由来の確定値）と
        # ヒューリスティックが見つけた附属書番号集合を照合する。
        # 一致しない場合、TOCセクション特定自体が誤っている（本文を附属書TOCと
        # 誤認した等）と判断し、annex_id 集合 + annex_titles フィールドで再構築する。
        annex_ids = sorted({p["annex_id"] for p in paragraphs
                             if p.get("type") != "image" and p.get("annex_id") is not None})
        if annex_ids and set(entries.keys()) != set(annex_ids):
            entries = {}
            toc_section_valid = False

    if toc_section_valid and annex_toc_start is not None:
        # 附属書TOCセクション（各附属書の見出し行＋Appendix小項目行）は
        # ページ番号がドットリーダーの後に続く形式のため、find_toc_dup_uids()
        # の重複判定（本文に同一番号の対応段落がある場合のみ検出）では、対応する
        # 本文側の番号が一致しない（Appendix小項目はページ番号がそのまま番号
        # 欄に入り、本文側に同じ番号の段落が存在しない）ケースを検出できない。
        # この区間は第2出現～第3出現の "1" で挟まれた附属書TOCセクションだが、
        # "Introduction"・"Preamble" のような実質的な本文を持つ段落が偶然この
        # 範囲に入り込むこともあるため、_is_toc_like_entry() で本文を持たない
        # ドットリーダー＋ページ番号のみの行であることを確認した段落だけを隠す
        # （TOCセクション特定がフォールバックで無効化された場合は本文を誤認
        # している可能性があるため隠さない）。
        for i in range(annex_toc_start, annex_toc_end):
            p_i = paragraphs[i]
            if p_i.get("type") != "image" and _is_toc_like_entry(p_i):
                p_i["_nav_hidden"] = True

    if not entries and annex_titles_fallback:
        for n in annex_ids:
            info = annex_titles_fallback.get(str(n)) or {}
            entries[n] = {
                "number": n,
                "title": (info.get("title") or "").strip(),
                "title_ja": (info.get("title_ja") or "").strip(),
            }

    return [entries[n] for n in sorted(entries.keys())]


def mark_annex_ids(paragraphs: list[dict], last_chapter: int = 12) -> None:
    """附属書段落に _annex_id（1–22）を付与する。
    mark_annex_paragraphs() の後に呼ぶこと。

    annex_id フィールドが設定されている段落（patch_annex_ids.py でパッチ済み）は
    それを直接 _annex_id として使用する。未設定の段落は従来のヒューリスティックで判定する。
    last_chapter: 本文の最終章番号（既定12は標準的なUN規則の章数。R.E.3等の
    異なる章構成の文書では build_tree() の結果から実際の最終章番号を渡すこと）。
    """
    import re as _re

    # Step 1: main_body_end（最後の実質的な最終章段落）を特定
    # last_chapter から降順に試し、目次エントリのみで本文が存在しない場合は
    # ひとつ手前の章で再試行する（例: R155 の "13. Annexes" TOC エントリ問題）。
    main_body_end = -1
    for try_chapter in range(last_chapter, 0, -1):
        for i, p in enumerate(paragraphs):
            if p.get("type") == "image":
                continue
            num = p.get("number", "")
            try:
                top = int(num.split('.')[0])
            except (ValueError, IndexError):
                continue
            if top != try_chapter:
                continue
            text = p.get("text", "") or ""
            title = p.get("title", "") or ""
            if "......" in text or "......" in title:
                continue
            if text.strip().startswith("E/ECE/") or title.strip().startswith("E/ECE/"):
                continue
            if _is_toc_like_entry(p):
                continue
            main_body_end = i
        if main_body_end >= 0:
            break

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
                # JSON の annex_id が候補番号より小さい場合はより小さい附属書の
                # 内部見出し（例：「Annex 19 test reports」が附属書2の中にある）
                # と判断して除外する。
                json_aid = p.get("annex_id")
                if json_aid is not None and int(json_aid) < n:
                    continue
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
    # Annex 1–9 の範囲のみに適用し、Annex 10+ の ID を破壊しない。
    current_override = None
    for i, p in enumerate(paragraphs):
        if i >= annex_10_start:
            break
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

    # Step 7: 画像のみで構成される附属書（本文段落を持たない）への対応。
    # 画像段落はステップ1-6で常に除外されるため、annex_id が明示的に
    # 設定されている画像はここで直接 _annex_id を適用する。
    for p in paragraphs:
        if p.get("type") != "image":
            continue
        aid = p.get("annex_id")
        if aid is not None:
            p["_annex_id"] = aid
            p["_in_annex"] = True


def mark_annex_parts(paragraphs: list[dict]) -> None:
    """附属書内の Part 境界を検出し各種フラグを付与する。

    2通りの検出モード:
    (a) アルファベット番号モード: 附属書内に level=1 で number が単一大文字アルファベット
        (A, B, C …) の段落が存在する場合、それを Part ヘッダーとみなす。
        _part_header=True を付与し、part_idx = ord(num) - ord('A') とする。
    (b) 番号再出現モード: アルファベットヘッダーがない場合、同一附属書内で level=1 の
        number が再出現した時点を新 Part の開始と判定する（従来ロジック）。

    付与フィールド: _part_idx / _part_label / _part_first / _part_header / _has_multiple_parts
    mark_annex_ids() の後に呼ぶこと。
    """
    import string as _string
    from collections import defaultdict

    annex_groups: dict = defaultdict(list)
    for p in paragraphs:
        if p.get("_in_annex") and p.get("_annex_id") is not None:
            annex_groups[p["_annex_id"]].append(p)

    for _aid, group in annex_groups.items():
        # アルファベット Part ヘッダーが存在するか判定
        has_alpha_parts = any(
            p.get("level") == 1
            and len(p.get("number") or "") == 1
            and (p.get("number") or "").isalpha()
            and (p.get("number") or "").upper() in "ABCDEFGHIJ"
            for p in group
        )

        part_idx = 0
        seen_level1: set = set()

        for p in group:
            level = p.get("level", 1)
            num = (p.get("number") or "")
            p["_part_header"] = False

            if has_alpha_parts:
                if level == 1 and len(num) == 1 and num.isalpha() and num.upper() in "ABCDEFGHIJ":
                    part_idx = ord(num.upper()) - ord("A")
                    p["_part_header"] = True
            else:
                if level == 1 and num:
                    if num in seen_level1:
                        part_idx += 1
                        seen_level1 = set()
                    seen_level1.add(num)

            p["_part_idx"] = part_idx
            p["_part_first"] = False

        # 各 Part の最初の可視段落に _part_first=True を付与
        # アルファベット Part モードの場合は附属書タイトル行（number=''）を除外し、
        # Part ヘッダー段落 (A./B./C.) が _part_first を取得するようにする。
        max_part = part_idx
        seen_parts: set = set()
        for p in group:
            if p.get("_nav_hidden") or p.get("type") == "image":
                continue
            if has_alpha_parts and p.get("level") == 1 and not (p.get("number") or ""):
                continue  # 附属書タイトルはPartに属させない
            pidx = p.get("_part_idx", 0)
            if pidx not in seen_parts:
                p["_part_first"] = True
                seen_parts.add(pidx)

        has_multi = max_part > 0
        for p in group:
            p["_has_multiple_parts"] = has_multi
            pidx = p.get("_part_idx", 0)
            label = _string.ascii_uppercase[pidx] if pidx < 26 else str(pidx + 1)
            p["_part_label"] = label
            # _display_number: アルファベット Part ヘッダーのある多 Part 附属書では
            # 「A-1.2」のように Part ラベルを番号の前に付加する。
            # Part ヘッダー自体（A./B./C.）は変更なし。
            num = (p.get("number") or "")
            if has_alpha_parts and has_multi and num and not p.get("_part_header"):
                p["_display_number"] = f"{label}-{num}"
            else:
                p["_display_number"] = num

    # 非附属書段落 + _display_number 未設定段落のデフォルト
    for p in paragraphs:
        if "_part_idx" not in p:
            p["_part_idx"] = 0
            p["_part_label"] = ""
            p["_part_first"] = False
            p["_part_header"] = False
            p["_has_multiple_parts"] = False
        if "_display_number" not in p:
            p["_display_number"] = (p.get("number") or "")


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
    next_chapter_num = 1  # 主要章は 1 から始まる連番のはず

    for p in paragraphs:
        # 画像ブロック・ノイズはツリーナビに表示しない
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        num = p["number"]
        is_integer = bool(_re.fullmatch(r"\d+", num))
        # 目次の区切り行（例: "Annexes" や "Appendix N"）がページ番号を章番号として
        # 誤って取り込んだものは、主要章の連番（1, 2, 3, …）から外れることで判別できる。
        # ただし表紙ページの注記等、たまたま章番号と無関係な数字（発行年・ページ番号等）
        # を持つだけの実質的な本文段落を誤って消してしまわないよう、_is_toc_like_entry()
        # で本文を持たない目次行であることを確認した場合のみ完全非表示にする。
        # 本文を持つ場合（表紙の注記等）は本文表示は維持しつつ、左ペインの章一覧には
        # 連番から外れた章として追加しない（is_seq_mismatch）。
        is_seq_mismatch = (p["level"] == 1 and is_integer and not top_level_closed
                            and num not in seen_top_numbers and int(num) != next_chapter_num)
        if is_seq_mismatch and _is_toc_like_entry(p):
            p["_nav_hidden"] = True
            continue
        # TOC のドットリーダー＋ページ番号の残骸（例: "....... 16"）を除去する
        title_clean = _re.sub(r'\s*\.{4,}.*', '', p["title"]).rstrip(". ")
        title_ja_clean = _re.sub(r'\s*\.{4,}.*', '', p.get("title_ja", "")).rstrip(". ")
        node = {
            "uid": p["uid"],
            "number": p["number"],
            "title": title_clean,
            "title_ja": title_ja_clean,
            "level": p["level"],
            "modified": p.get("modified", False),
            "children": [],
        }
        if p["level"] == 1:
            if is_integer and num in seen_top_numbers:
                top_level_closed = True  # 附属書の繰り返し番号が始まった
                # 本文の後出現に title_ja があれば chapters リストのエントリを更新する
                if num in chapter_list_entries and node["title_ja"]:
                    existing = chapter_list_entries[num]
                    if not existing["title_ja"]:
                        existing["title_ja"] = node["title_ja"]
                        existing["title"] = node["title"]
            if not top_level_closed and not is_seq_mismatch:
                chapters.append(node)
                if is_integer:
                    seen_top_numbers.add(num)
                    chapter_list_entries[num] = node
                    next_chapter_num = int(num) + 1
            chapter_map[num] = node
        else:
            parent_num = p.get("parent")
            if parent_num and parent_num in chapter_map:
                chapter_map[parent_num]["children"].append(node)
            # 親が見つからない孤立ノードは左ペインに追加しない
        chapter_map[p["number"]] = node

    return chapters


_PLAIN_REF_RE = None  # 遅延初期化
_ANNEX_REF_RE = None
_THIS_REG_RE  = None


def find_para_refs(text: str, current_annex_id=None) -> list[dict]:
    """英語テキストから段落参照を抽出し、参照先のアネックスIDとともに返す。

    Returns: [{number: str, annex_id: int|None}]
    - "paragraph N.N of Annex M"          → annex_id=M
    - "paragraph N.N of this Regulation"  → annex_id=None（本則）
    - 上記以外の "paragraph N.N..."        → annex_id=current_annex_id
    """
    global _PLAIN_REF_RE, _ANNEX_REF_RE, _THIS_REG_RE
    import re as _re
    if _PLAIN_REF_RE is None:
        _ANNEX_REF_RE = _re.compile(
            r'\bparagraphs?\s+([\d.]+?\.?)\s+of\s+(?:Part\s+\w+\s+of\s+)?Annex\s+(\d+)'
            r'(?:\s+to\s+this\s+Regulation)?',
            _re.IGNORECASE,
        )
        _THIS_REG_RE = _re.compile(
            r'\bparagraphs?\s+([\d.]+?\.?)\s+of\s+this\s+Regulation',
            _re.IGNORECASE,
        )
        # "X, Y, or Z"のようなオックスフォードコンマ付きリストは、YとZの間に
        # コンマと接続詞が両方現れる（",|and|to|or"を単独でしか許さないと3件目
        # 以降が繋がらず欠落する）ため、コンマの後に接続詞が続くケースも許容する。
        _PLAIN_REF_RE = _re.compile(
            r'\bparagraphs?\s+(\d+(?:\.\d+)+\.?)'
            r'((?:(?:\s*,\s*(?:and|or|to)?\s+|\s+(?:and|or|to)\s+)\d+(?:\.\d+)+\.?)*)',
            _re.IGNORECASE,
        )

    seen: set[tuple] = set()
    result: list[dict] = []

    def add_ref(number: str, annex_id) -> None:
        number = number.rstrip('.')
        if not number:
            return
        key = (number, annex_id)
        if key not in seen:
            seen.add(key)
            result.append({'number': number, 'annex_id': annex_id})

    # Annex参照・本則参照の span を記録してから plain 参照と重複スキップ
    special_spans: list[tuple[int, int]] = []

    for m in _ANNEX_REF_RE.finditer(text):
        add_ref(m.group(1), int(m.group(2)))
        special_spans.append((m.start(), m.end()))

    for m in _THIS_REG_RE.finditer(text):
        add_ref(m.group(1), None)
        special_spans.append((m.start(), m.end()))

    for m in _PLAIN_REF_RE.finditer(text):
        if any(s <= m.start() < e for s, e in special_spans):
            continue
        for num in _re.findall(r'\d+(?:\.\d+)+\.?', m.group()):
            add_ref(num, current_annex_id)

    return result


def annotate_glossary(text: str, glossary_terms: dict[str, dict]) -> str:
    """テキスト内の用語を右→左挿入でHTMLスパンに置換する（大文字小文字無視、最長優先）。"""
    if not text:
        return text
    terms = sorted(glossary_terms.keys(), key=lambda x: -len(x))
    if not terms:
        return text
    lower_text = text.lower()
    covered: set[int] = set()
    matches: list[tuple[int, int, str]] = []
    for term in terms:
        lower_term = term.lower()
        start = 0
        while True:
            pos = lower_text.find(lower_term, start)
            if pos < 0:
                break
            end = pos + len(term)
            if not covered.intersection(range(pos, end)):
                matches.append((pos, end, term))
                covered.update(range(pos, end))
            start = pos + 1
    matches.sort(key=lambda x: x[0], reverse=True)
    for pos, end, term in matches:
        info = glossary_terms[term]
        defn = info["definition_ja"].replace('"', '&quot;')
        ref  = info.get("paragraph_ref", "").replace('"', '&quot;')
        span = (
            f'<span class="glossary-term" '
            f'data-term="{term}" '
            f'data-definition="{defn}" '
            f'data-ref="{ref}">'
            f'{text[pos:end]}</span>'
        )
        text = text[:pos] + span + text[end:]
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


def load_proposals(regulation: str) -> list[dict]:
    """改正提案データ（data/<reg>/proposals.json）を読み込む。存在しない場合は空リスト。

    確定差分（modified/text_old、diff_versions()）とは別系統のデータであり、
    structured.json には一切書き戻さない（未採択の提案と確定済み改正を混同しないため）。
    """
    path = DATA_DIR / regulation / "proposals.json"
    if not path.exists():
        return []
    return load_json(path).get("proposals", [])


def apply_proposals(paragraphs: list[dict], proposals: list[dict]) -> None:
    """改正提案（ADDED/MODIFIED/DELETED）を段落リストに適用する（インプレース変更）。

    MODIFIED/DELETED は対象段落（target_uid）に _proposals リスト（新しい提案が先頭）
    として付与する。ADDED は anchor_uid の直後に仮想段落を挿入し、_is_proposal_only=True
    で印をつける（他のノイズ判定・章/附属書判定ロジックをそのまま素通りさせるため、
    実在の段落と同じ level/parent/number 形式で構築する）。
    """
    by_target: dict[str, list[dict]] = {}
    by_anchor: dict[str, list[dict]] = {}
    for prop in proposals:
        if prop.get("type") == "ADDED":
            anchor = prop.get("anchor_uid")
            if anchor:
                by_anchor.setdefault(anchor, []).append(prop)
        else:
            target = prop.get("target_uid")
            if target:
                by_target.setdefault(target, []).append(prop)

    for props in by_target.values():
        props.sort(key=lambda p: p.get("source_date") or "", reverse=True)
    for props in by_anchor.values():
        props.sort(key=lambda p: p.get("source_date") or "")

    for p in paragraphs:
        props = by_target.get(p.get("uid"))
        if props:
            p["_proposals"] = props

    if not by_anchor:
        return

    new_paragraphs: list[dict] = []
    for p in paragraphs:
        new_paragraphs.append(p)
        for prop in by_anchor.get(p.get("uid"), []):
            new_paragraphs.append({
                "uid": f"PROPOSAL-{prop.get('id')}",
                "number": prop.get("number", ""),
                "title": prop.get("title", ""),
                "text": "",
                "level": p.get("level", 1),
                "parent": p.get("parent"),
                "type": "paragraph",
                "status": "proposal",
                "translation": None,
                "summary_ja": None,
                "modified": False,
                "justification": None,
                "prev_uid": None,
                "text_old": None,
                "annex_id": None,
                "_is_proposal_only": True,
                "_proposals": [prop],
            })
    paragraphs[:] = new_paragraphs


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

    # 改正提案（未採択）の適用。ツリー構築前に行うことで、ADDED提案の仮想段落も
    # 通常の段落と同様に左ペイン・章/附属書判定に参加させる。
    proposals = load_proposals(regulation)
    if proposals:
        apply_proposals(paragraphs, proposals)

    # ノイズフラグ付与前にツリーを構築する
    tree = build_tree(paragraphs)
    annex_tree = build_annex_tree(paragraphs, data.get("annex_titles"))

    # ノイズフラグを付与してから用語アノテーションを追加
    mark_footnote_noise(paragraphs)
    toc_dup_uids = find_toc_dup_uids(paragraphs)
    pseudo_num_dup_uids = find_pseudo_number_dup_uids(paragraphs)
    for p in paragraphs:
        if p.get("type") == "image":
            continue
        if p.get("_is_proposal_only"):
            # 改正提案の仮想段落は実在が確定しているため、ノイズ判定をスキップする
            p["_nav_hidden"] = False
            p["text_annotated"] = ""
            p["translation_annotated"] = None
            p["summary_ja_short"] = ""
            continue
        # ナビゲーションノイズフラグを付与（ページヘッダー・目次ドット行）
        if not p.get("_nav_hidden"):
            p["_nav_hidden"] = is_nav_noise(p, toc_dup_uids, pseudo_num_dup_uids)
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
    chapter_numbers_int = [int(n["number"]) for n in tree if n["number"].isdigit()]
    last_chapter = max(chapter_numbers_int) if chapter_numbers_int else 12
    mark_annex_paragraphs(paragraphs)
    mark_annex_ids(paragraphs, last_chapter=last_chapter)
    mark_annex_parts(paragraphs)

    # 参照段落チップ: "paragraph N.N..." 参照を検出し _refs リストを付与する。
    # _nav_hidden・_annex_id が確定した後に実行すること。
    _ref_map: dict[tuple, dict] = {}  # (number, annex_id) → info
    for p in paragraphs:
        if p.get("type") == "image":
            continue
        num = p.get("number", "")
        if not num:
            continue
        annex_id = p.get("_annex_id")
        key = (num, annex_id)
        existing = _ref_map.get(key)
        is_hidden = bool(p.get("_nav_hidden"))
        if existing is None or (existing["hidden"] and not is_hidden):
            _ref_map[key] = {
                "uid": p.get("uid", ""),
                "summary_ja": (p.get("summary_ja") or "").strip(),
                "annex_id": annex_id,
                "hidden": is_hidden,
            }
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            p["_refs"] = []
            continue
        refs: list[dict] = []
        seen_ref_keys: set[tuple] = set()
        current_annex_id = p.get("_annex_id")
        for ref_info in find_para_refs(p.get("text", "") or "", current_annex_id):
            num    = ref_info["number"]
            ann_id = ref_info["annex_id"]
            key    = (num, ann_id)
            if key in seen_ref_keys:
                continue
            entry = _ref_map.get(key)
            # アネックス指定で見つからない場合はチップ非表示（本則の同番号を誤表示しない）
            if entry and entry["summary_ja"] and not entry["hidden"]:
                seen_ref_keys.add(key)
                refs.append({
                    "number": num,
                    "uid": entry["uid"],
                    "summary_ja": entry["summary_ja"],
                    "annex_id": entry["annex_id"],
                })
        p["_refs"] = refs

    # 中ペインの重複排除: 同じ number が複数回出現する場合（附属書が同じ番号を繰り返す）
    # 文書順で最初に出現した可視段落のみ _first_occ=True とする
    seen_para_numbers: set[str] = set()
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        if p.get("_is_proposal_only"):
            # 改正提案の仮想段落は重複排除の対象外（常に表示し、既存段落の番号も奪わない）
            p["_first_occ"] = True
            continue
        num = p.get("number", "")
        if num and num not in seen_para_numbers:
            p["_first_occ"] = True
            seen_para_numbers.add(num)
        else:
            p["_first_occ"] = False

    # 附属書内ナビの重複排除: 同じ (annex_id, part_idx, number) 内で最初に出現した
    # 段落のみを _first_occ_annex=True とする（各 Part 内で番号が重複するケースに対応）。
    seen_annex_numbers: dict = {}
    for p in paragraphs:
        if p.get("type") == "image" or p.get("_nav_hidden"):
            continue
        annex_id = p.get("_annex_id")
        if not p.get("_in_annex") or annex_id is None:
            p["_first_occ_annex"] = True
            continue
        num = p.get("number", "")
        if not num:
            p["_first_occ_annex"] = True
            continue
        part_idx = p.get("_part_idx", 0)
        key = (annex_id, part_idx, num)
        if key not in seen_annex_numbers:
            p["_first_occ_annex"] = True
            seen_annex_numbers[key] = True
        else:
            p["_first_occ_annex"] = False

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

    # R13 限定: 他のAI/外部ツールが段落単位で読み取れるよう、元データを
    # そのまま docs/R13/data.json として静的公開する（全フィールドを含む）。
    # data はビルド処理中に _nav_hidden 等の内部フィールドが書き込まれているため、
    # ここでは structured.json を読み直した未加工のコピーを書き出す。
    if regulation == "R13":
        raw_data = load_json(structured_path)
        data_json_path = output_dir / "data.json"
        data_json_path.write_text(
            json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        click.echo(f"Built: {data_json_path}")


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


def build_diff_page(regulation: str, from_ver: str, to_ver: str) -> None:
    """改正提案などの版間差分一覧ページ（別ページ）を生成する。"""
    diff_path = DATA_DIR / regulation / "diff" / f"{from_ver}_to_{to_ver}.json"
    if not diff_path.exists():
        raise click.ClickException(f"Not found: {diff_path}")

    data = load_json(diff_path)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("diff_page.html")

    output_dir = SITE_DIR / regulation / "diff"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{from_ver}_to_{to_ver}.html"

    html = template.render(
        regulation=regulation,
        from_version=data.get("from_version", from_ver),
        to_version=data.get("to_version", to_ver),
        summary=data.get("summary", {}),
        changes=data.get("changes", []),
    )
    output_path.write_text(html, encoding="utf-8")
    click.echo(f"Built: {output_path}")


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


@cli.command(name='diff-page')
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--from', 'from_ver', required=True, help='比較元バージョン')
@click.option('--to', 'to_ver', required=True, help='比較先バージョン')
def diff_page(reg: str, from_ver: str, to_ver: str):
    """改正提案などの版間差分一覧ページを生成する（data/<reg>/diff/<from>_to_<to>.json が必要）"""
    build_diff_page(reg, from_ver, to_ver)


@cli.command()
def all():
    """data/ 以下の全法規のHTMLを生成する"""
    regulations = sorted(
        (d.name for d in DATA_DIR.iterdir() if d.is_dir()),
        key=reg_sort_key,
    )
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
