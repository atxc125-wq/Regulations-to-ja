#!/usr/bin/env python3
"""
translate.py  --  未翻訳の段落を Gemini API（または Claude CLI）で翻訳・要約する。

glossary.json をシステムプロンプトに組み込み、専門用語の訳語ブレを防止する。
Structured Outputs（response_schema）により translation/summary_ja を安定出力する。

Usage:
    # 通常（GEMINI_API_KEY 環境変数が必要）
    python translate.py --reg R13 --version Rev9 --limit 3

    # フォールバック: ANTHROPIC_API_KEY を使用
    python translate.py --reg R13 --version Rev9 --engine anthropic

    # dry-run（API 未使用・対象段落を一覧表示）
    python translate.py --reg R13 --version Rev9 --dry-run

    # 使用する用語集を確認
    python translate.py --reg R13 --show-glossary
"""

import json
import os
import textwrap
import time
import urllib.request
import urllib.error
from pathlib import Path

import click

# Anthropic SDK (オプション)
try:
    import anthropic as anthropic_sdk
except Exception:
    anthropic_sdk = None

DATA_DIR = Path(__file__).parent.parent / "data"

# --------------------------------------------------------------------------- #
# Glossary ローダー
# --------------------------------------------------------------------------- #

def load_glossary(regulation: str) -> list[dict]:
    path = DATA_DIR / regulation / "glossary.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("terms", [])


def build_glossary_block(terms: list[dict]) -> str:
    if not terms:
        return ""
    rows = []
    for t in terms:
        en    = t.get("term", "")
        ja    = t.get("ja", "")
        desc  = t.get("description", "")
        alias = t.get("aliases", [])
        note  = f"（別称: {', '.join(alias)}）" if alias else ""
        rows.append(f"| {en} | {ja} | {desc}{note} |")

    return textwrap.dedent(f"""\

        ## 専門用語集（この訳語を必ず使用すること）
        原文に以下の用語が含まれる場合、対応する「日本語訳」欄の訳語を必ず使用すること。

        | 英語 | 日本語訳 | 説明・注記 |
        |---|---|---|
        {chr(10).join(rows)}
    """)


# --------------------------------------------------------------------------- #
# システム / ユーザープロンプト
# --------------------------------------------------------------------------- #

_SYSTEM_BASE = """\
あなたは国連自動車法規（UN Regulations）の日本語翻訳の専門家です。
以下の原則に従って翻訳・要約を行ってください。

## 翻訳原則
1. 法令文体を使用すること（例: 〜しなければならない、〜することができる）
2. 法的助動詞を以下のとおり厳密に区別すること:
   - shall     →「〜しなければならない」（法的義務）
   - shall not →「〜してはならない」（法的禁止）
   - should    →「〜すべきである」（推奨）
   - may       →「〜することができる」（許可）
   - need not  →「〜する必要はない」（免除）
3. 定義規定（"X" means ...）は「「X」とは、…をいう。」の形式にすること
4. 列挙（(a), (b), (c)...）は原文の記号を保持すること
5. 要約は最重要要件を1〜2文で端的に表すこと

## 出力品質の要件
6. **訳文は完全な日本語にすること。英単語・英略語を訳文に混入させてはならない。**
   - 技術略語（ABS, EBS等）は用語集の日本語訳を使用すること
   - 「Figure X」「Table X」等の図表参照は「図X」「表X」と訳すこと
7. 原文にHTMLタグ（`<span ...>`等）やdata属性が含まれる場合は、それらを完全に無視し、
   テキスト部分のみを翻訳対象とすること
8. 訳文・要約ともに先頭・末尾の余分な空白・改行を含めないこと
"""


def build_system_prompt(glossary_terms: list[dict]) -> str:
    return _SYSTEM_BASE + build_glossary_block(glossary_terms)


def build_user_prompt(number: str, title: str, text: str) -> str:
    return textwrap.dedent(f"""\
        以下のUN法規の段落を日本語に翻訳し、要約を作成してください。

        段落番号: {number}
        タイトル: {title}
        英語原文:
        {text}

        以下のJSONキーで回答してください:
        - translation: 完全な日本語訳（法令文体）
        - summary_ja:  1〜2文の日本語要約
    """)


# --------------------------------------------------------------------------- #
# Gemini API エンジン（直接 REST 呼び出し + Structured Outputs）
#
# google-genai SDK は google.auth 経由で cryptography を要求するため、
# システムの cryptography ライブラリと競合する環境では urllib で直接呼ぶ。
# --------------------------------------------------------------------------- #

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# JSON Schema: 本文翻訳用（単一段落）
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "translation": {"type": "STRING", "description": "完全な日本語翻訳文（法令文体）"},
        "summary_ja":  {"type": "STRING", "description": "1〜2文の日本語要約"},
    },
    "required": ["translation", "summary_ja"],
}

# JSON Schema: バッチ翻訳用（複数段落）
_BATCH_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "number":      {"type": "STRING", "description": "段落番号（原文のまま）"},
            "translation": {"type": "STRING", "description": "完全な日本語翻訳文（法令文体）"},
            "summary_ja":  {"type": "STRING", "description": "1〜2文の日本語要約"},
        },
        "required": ["number", "translation", "summary_ja"],
    },
}

# thinkingConfig: 思考トークンを無効化してコストを削減
_NO_THINKING = {"thinkingBudget": 0}



def _gemini_post(api_key: str, payload: dict, model: str = "gemini-2.5-flash",
                 max_retries: int = 3) -> dict:
    """Gemini API に POST して生レスポンス dict を返す共通関数。
    429 (RESOURCE_EXHAUSTED) の場合は retryDelay に従って待機後リトライする。
    """
    url = f"{_GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    body = json.dumps(payload).encode("utf-8")

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < max_retries:
                # retryDelay を解析して待機（例: "44.389s" → 44.4秒）
                wait = 60.0  # デフォルト待機
                try:
                    err_data = json.loads(raw)
                    for detail in err_data.get("error", {}).get("details", []):
                        if detail.get("@type", "").endswith("RetryInfo"):
                            delay_str = detail.get("retryDelay", "60s")
                            wait = float(delay_str.rstrip("s")) + 2
                            break
                except Exception:
                    pass
                click.echo(f"  ⚠ 429 Rate limit. Waiting {wait:.0f}s before retry "
                           f"(attempt {attempt+1}/{max_retries})...", err=True)
                time.sleep(wait)
            else:
                raise RuntimeError(f"Gemini API HTTP {e.code}: {raw[:400]}")


def _translate_gemini(
    api_key: str,
    system_prompt: str,
    number: str,
    title: str,
    text: str,
    model: str = "gemini-2.5-flash",
) -> dict:
    """本文1段落をGemini APIで翻訳する。"""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(number, title, text)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "temperature": 0.2,
            "thinkingConfig": _NO_THINKING,
        },
    }
    data = _gemini_post(api_key, payload, model)
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected Gemini response: {data}")
    result = json.loads(raw)
    return {k: v.strip() if isinstance(v, str) else v for k, v in result.items()}


def _translate_gemini_batch(
    api_key: str,
    system_prompt: str,
    paragraphs: list[tuple[str, str, str]],  # [(number, title, text), ...]
    model: str = "gemini-2.5-flash",
) -> dict[str, dict]:
    """複数段落を1回のAPI呼び出しで翻訳する。
    Returns: {number: {translation, summary_ja}, ...}
    """
    lines = []
    for number, title, text in paragraphs:
        lines.append(f"[段落 {number}]\nタイトル: {title}\n原文:\n{text}")
    user_msg = textwrap.dedent(f"""\
        以下の段落を日本語に翻訳し、各段落の要約を作成してください。
        法令文体を使用し、用語集の訳語を必ず使用すること。

        {chr(10).join(lines)}

        各段落について「number」「translation」「summary_ja」を含むJSONオブジェクトの配列で回答してください。
        段落番号は原文のまま返すこと。
    """)
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _BATCH_RESPONSE_SCHEMA,
            "temperature": 0.2,
            "thinkingConfig": _NO_THINKING,
        },
    }
    data = _gemini_post(api_key, payload, model)
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected Gemini response: {data}")
    items = json.loads(raw)
    return {
        item["number"]: {k: v.strip() if isinstance(v, str) else v for k, v in item.items()}
        for item in items
    }


def translate_headings_gemini(
    api_key: str,
    system_prompt: str,
    headings: list[tuple[str, str]],   # [(number, title_en), ...]
    model: str = "gemini-2.5-flash",
) -> dict[str, str]:
    """
    複数の見出しを1回のAPI呼び出しで一括翻訳する。
    headings: [(段落番号, 英語タイトル), ...]
    Returns: {段落番号: 日本語タイトル, ...}
    """
    lines = "\n".join(f'"{num}": "{title}"' for num, title in headings)
    user_msg = textwrap.dedent(f"""\
        以下は UN法規の段落見出し（英語）の一覧です。
        各見出しを簡潔な日本語に翻訳してください。
        用語集の訳語を必ず使用し、法令用語として自然な表現にすること。
        見出しは名詞句（体言止め）で訳すこと。長くても15文字以内を目安にすること。

        {lines}

        段落番号をキー、日本語訳を値とするJSONオブジェクトで回答してください。
    """)
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "thinkingConfig": _NO_THINKING,
        },
    }
    data = _gemini_post(api_key, payload, model)
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected Gemini response: {data}")
    result = json.loads(raw)
    return {k: v.strip() if isinstance(v, str) else v for k, v in result.items()}


# --------------------------------------------------------------------------- #
# Anthropic API エンジン（フォールバック）
# --------------------------------------------------------------------------- #

def _translate_anthropic(
    api_key: str,
    system_prompt: str,
    number: str,
    title: str,
    text: str,
    model: str = "claude-opus-4-7",
) -> dict:
    if anthropic_sdk is None:
        raise RuntimeError("anthropic SDK not installed. Run: pip install anthropic")

    client = anthropic_sdk.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": build_user_prompt(number, title, text)}],
    )
    raw = message.content[0].text.strip()
    # コードフェンス除去
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    start, end = raw.find('{'), raw.rfind('}') + 1
    if start == -1:
        raise ValueError(f"No JSON in response: {raw[:200]}")
    return json.loads(raw[start:end])


# --------------------------------------------------------------------------- #
# ディスパッチャ
# --------------------------------------------------------------------------- #

def translate_paragraph(
    engine: str,          # "gemini" | "anthropic"
    system_prompt: str,
    number: str,
    title: str,
    text: str,
    model: str = "gemini-2.5-flash",
) -> dict:
    """エンジンを選択して翻訳を実行する。"""
    if engine == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 環境変数が設定されていません。")
        return _translate_gemini(api_key, system_prompt, number, title, text, model)

    elif engine == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY 環境変数が設定されていません。")
        return _translate_anthropic(api_key, system_prompt, number, title, text, model)

    else:
        raise ValueError(f"Unknown engine: {engine!r}. Use 'gemini' or 'anthropic'.")


# --------------------------------------------------------------------------- #
# JSON 入出力
# --------------------------------------------------------------------------- #

def load_structured(regulation: str, version: str) -> dict:
    path = DATA_DIR / regulation / version / "structured.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_structured(data: dict, regulation: str, version: str) -> None:
    path = DATA_DIR / regulation / version / "structured.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 附属書 (Annex) 段落 UID 検索
# --------------------------------------------------------------------------- #

def _find_annex_indices(paragraphs: list[dict], annex_number: int) -> set[int]:
    """指定された附属書に属する段落のインデックスセットを返す。
    Annex 10以上: "Annex N" タイトルの level=1 段落をアンカーにして範囲を特定。
    Annex 1-9: 章番号のリスタートを追跡して附属書グループを特定。
    """
    import re

    # --- Annex 10+ : 明示的なタイトルヘッダーで特定 ---
    if annex_number >= 10:
        start_idx = None
        end_idx = len(paragraphs)
        for i, p in enumerate(paragraphs):
            if p.get('type', 'paragraph') != 'paragraph':
                continue
            if p.get('level', 1) != 1:
                continue
            title = p.get('title', '').strip()
            if re.match(rf'^Annex\s+{annex_number}\b', title):
                if start_idx is None:
                    start_idx = i
            elif start_idx is not None:
                m = re.match(r'^Annex\s+(\d+)\b', title)
                if m and int(m.group(1)) != annex_number:
                    end_idx = i
                    break
        if start_idx is None:
            return set()
        return {i for i, p in enumerate(paragraphs[start_idx:end_idx], start_idx)
                if p.get('type', 'paragraph') == 'paragraph'}

    # --- Annex 1-9 : リスタートグループで特定 ---
    # 本文終端を特定: 実コンテンツ（TOCノイズ・フォームではない）で章番号12のうち最後の段落
    main_body_end = 0
    for i, p in enumerate(paragraphs):
        if p.get('type', 'paragraph') != 'paragraph':
            continue
        num = p.get('number', '')
        try:
            top = int(num.split('.')[0])
        except (ValueError, IndexError):
            continue
        if top != 12:
            continue
        text = p.get('text', '') or ''
        title = p.get('title', '') or ''
        # TOC ドット行・ページヘッダー・フォーム行をスキップ
        if '....' in text or '....' in title:
            continue
        if text.strip().startswith('E/ECE/') or title.strip().startswith('E/ECE/'):
            continue
        main_body_end = i
    if main_body_end == 0:
        return set()

    # 本文終端以降でリスタートグループを収集（インデックス付き）
    groups: list[list[int]] = []          # 各グループ: 段落インデックスのリスト
    current_group: list[int] = []
    max_top_in_group = 0
    last_top = 12

    for i, p in enumerate(paragraphs[main_body_end + 1:], main_body_end + 1):
        if p.get('type', 'paragraph') != 'paragraph':
            continue
        # Annex 10以降は別メソッドが担当するので到達したら終了
        title = p.get('title', '').strip()
        if re.match(r'^Annex\s+1\d\b', title) and p.get('level', 1) == 1:
            break
        num = p.get('number', '')
        try:
            top = int(num.split('.')[0])
        except (ValueError, IndexError):
            continue
        if top <= 0:
            continue

        if top < last_top and last_top > 1:
            # リスタート検出 → 新グループ開始
            if current_group:
                groups.append(current_group)
            current_group = [i]
            max_top_in_group = top
        else:
            current_group.append(i)
            max_top_in_group = max(max_top_in_group, top)

        last_top = top

    if current_group:
        groups.append(current_group)

    # グループを附属書番号に対応付ける
    # 帳票グループ判定: テキストの半数超が "....." を含む → フォームグループ（Annexes 1-3）
    def is_form_group(idx_list: list[int]) -> bool:
        if not idx_list:
            return True
        dotted = sum(
            1 for idx in idx_list
            if '....' in (paragraphs[idx].get('text', '') or paragraphs[idx].get('title', ''))
        )
        return dotted >= len(idx_list) * 0.4

    substantive_groups: list[list[int]] = [g for g in groups if not is_form_group(g)]

    # substantive_groups[0] = Annex 4, [1] = Annex 5, …
    target_idx = annex_number - 4
    if target_idx < 0 or target_idx >= len(substantive_groups):
        return set()

    return set(substantive_groups[target_idx])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

@click.group()
def cli():
    """UN法規テキスト翻訳ツール"""


@cli.command("body")
@click.option('--reg',        required=True,                      help='法規番号 (例: R13)')
@click.option('--version',    default='',                         help='バージョン (例: Rev9)。--show-glossary 時は省略可')
@click.option('--engine',     default='gemini',
              type=click.Choice(['gemini', 'anthropic']),
              show_default=True,                                   help='使用する翻訳エンジン')
@click.option('--model',      default='gemini-2.5-flash',
              show_default=True,                                   help='使用するモデル名')
@click.option('--dry-run',    is_flag=True,                       help='API未使用。対象段落を一覧表示して終了')
@click.option('--limit',      default=0,   type=int,              help='翻訳する最大段落数（0=無制限）')
@click.option('--delay',      default=1.0, type=float,            help='API呼び出し間隔（秒）')
@click.option('--prefix',     default='',                         help='段落番号のプレフィックスでフィルタ (例: 5.1)')
@click.option('--annex',      default=0,   type=int,              help='附属書番号でフィルタ (例: 4, 13, 18, 21)')
@click.option('--batch-size', default=10,  type=int, show_default=True,
              help='1回のAPI呼び出しで翻訳する段落数。Gemini専用。1=単一翻訳モード')
@click.option('--show-glossary', is_flag=True,                    help='用語集とシステムプロンプトを表示して終了')
def body_cmd(
    reg: str, version: str, engine: str, model: str,
    dry_run: bool, limit: int, delay: float, prefix: str, annex: int,
    batch_size: int, show_glossary: bool,
):
    """
    未翻訳段落を Gemini API（デフォルト）または Anthropic API で翻訳・要約する。

    必要な環境変数:
      Gemini:    GEMINI_API_KEY
      Anthropic: ANTHROPIC_API_KEY
    """
    glossary_terms = load_glossary(reg)
    system_prompt  = build_system_prompt(glossary_terms)

    if show_glossary:
        click.echo(f"Glossary for {reg}: {len(glossary_terms)} terms\n")
        for t in glossary_terms:
            click.echo(f"  {t['term']:42s} → {t['ja']}")
        click.echo()
        click.echo("--- System prompt preview ---")
        click.echo(system_prompt[:800])
        return

    if not version:
        raise click.UsageError("--version が必要です。")

    data   = load_structured(reg, version)
    blocks = data['paragraphs']

    # type=="paragraph" かつ status=="untranslated" のブロックのみ対象
    annex_indices: set = set()
    if annex > 0:
        annex_indices = _find_annex_indices(blocks, annex)
        if not annex_indices:
            raise click.ClickException(f"Annex {annex} が見つかりません。")

    targets = [
        b for i, b in enumerate(blocks)
        if b.get('type', 'paragraph') == 'paragraph'
        and b.get('status') == 'untranslated'
        and (not prefix or b.get('number', '') == prefix or b.get('number', '').startswith(prefix + '.'))
        and (not annex_indices or i in annex_indices)
    ]

    if limit > 0:
        targets = targets[:limit]

    click.echo(f"Target: {reg}/{version}  |  untranslated: {len(targets)}  |  engine: {engine}")
    click.echo(f"Glossary: {len(glossary_terms)} term(s) loaded.")
    if engine == "gemini" and batch_size > 1:
        click.echo(f"Batch size: {batch_size} (thinking disabled)")

    if dry_run:
        click.echo("\nDry-run — paragraphs that would be translated:")
        for p in targets:
            click.echo(f"  [{p['number']:6s}] {p['title']}")
        return

    # API キーの存在チェック（早期終了）
    key_var = "GEMINI_API_KEY" if engine == "gemini" else "ANTHROPIC_API_KEY"
    api_key = os.environ.get(key_var, "")
    if not api_key:
        raise click.ClickException(
            f"{key_var} 環境変数が設定されていません。\n"
            f"  export {key_var}=<your-key>\n"
            f"  --dry-run オプションで動作確認のみ行うことができます。"
        )

    processed = 0
    errors    = 0
    SAVE_INTERVAL = 20

    use_batch = (engine == "gemini" and batch_size > 1)

    if use_batch:
        # --- バッチ翻訳モード ---
        for batch_start in range(0, len(targets), batch_size):
            batch = targets[batch_start:batch_start + batch_size]
            nums  = ", ".join(p['number'] for p in batch)
            click.echo(f"\nBatch [{batch_start+1}–{batch_start+len(batch)}] {nums}")
            try:
                results = _translate_gemini_batch(
                    api_key, system_prompt,
                    [(p['number'], p['title'], p['text']) for p in batch],
                    model=model,
                )
                for p in batch:
                    if p['number'] in results:
                        r = results[p['number']]
                        p['translation'] = r.get('translation', '').strip()
                        p['summary_ja']  = r.get('summary_ja', '').strip()
                        p['status']      = 'done'
                        processed += 1
                        preview = p['summary_ja'][:60].replace('\n', ' ')
                        click.echo(f"  ✓ [{p['number']}] {preview}{'...' if len(p['summary_ja']) > 60 else ''}")
                    else:
                        click.echo(f"  ✗ [{p['number']}] 結果が返りませんでした", err=True)
                        p['status'] = 'error'
                        errors += 1
            except Exception as e:
                click.echo(f"  ✗ Batch ERROR: {e}", err=True)
                for p in batch:
                    p['status'] = 'error'
                errors += len(batch)

            if (processed + errors) % SAVE_INTERVAL < batch_size:
                save_structured(data, reg, version)
                click.echo(f"  [checkpoint: {processed} saved]")

            if delay > 0 and batch_start + batch_size < len(targets):
                time.sleep(delay)

    else:
        # --- 単一翻訳モード ---
        for p in targets:
            click.echo(f"\nTranslating [{p['number']}] {p['title']} ...")
            try:
                result = translate_paragraph(
                    engine, system_prompt,
                    p['number'], p['title'], p['text'],
                    model=model,
                )
                p['translation'] = result['translation'].strip()
                p['summary_ja']  = result['summary_ja'].strip()
                p['status']      = 'done'
                processed += 1
                preview = p['summary_ja'][:70].replace('\n', ' ')
                click.echo(f"  ✓ {preview}{'...' if len(p['summary_ja']) > 70 else ''}")

            except Exception as e:
                click.echo(f"  ✗ ERROR: {e}", err=True)
                p['status'] = 'error'
                errors += 1

            if (processed + errors) % SAVE_INTERVAL == 0:
                save_structured(data, reg, version)
                click.echo(f"  [checkpoint: {processed} saved]")

            if delay > 0 and (processed + errors) < len(targets):
                time.sleep(delay)

    save_structured(data, reg, version)
    click.echo(f"\n{'='*52}")
    click.echo(f"Done: {processed} translated, {errors} error(s).")
    click.echo(f"Saved → data/{reg}/{version}/structured.json")


@cli.command("headings")
@click.option('--reg',        required=True,                      help='法規番号 (例: R13)')
@click.option('--version',    required=True,                      help='バージョン (例: Rev9)')
@click.option('--engine',     default='gemini',
              type=click.Choice(['gemini', 'anthropic']),
              show_default=True,                                   help='使用する翻訳エンジン')
@click.option('--model',      default='gemini-2.5-flash',
              show_default=True,                                   help='使用するモデル名')
@click.option('--overwrite',  is_flag=True,                       help='既存の title_ja も上書きする')
@click.option('--dry-run',    is_flag=True,                       help='API未使用。対象見出しを一覧表示して終了')
@click.option('--chunk-size', default=200, type=int, show_default=True,
              help='1回のAPI呼び出しで送る見出し数。大きすぎると漏れが発生する')
@click.option('--delay',      default=2.0, type=float, show_default=True,
              help='チャンク間の待機秒数')
def headings_cmd(reg: str, version: str, engine: str, model: str,
                 overwrite: bool, dry_run: bool, chunk_size: int, delay: float):
    """
    段落・章の見出し（title）を一括翻訳し title_ja フィールドに保存する。
    --chunk-size で分割して複数回のAPI呼び出しに分けることができる。
    """
    glossary_terms = load_glossary(reg)
    system_prompt  = build_system_prompt(glossary_terms)

    data   = load_structured(reg, version)
    blocks = data['paragraphs']

    # 翻訳が必要な見出しを収集
    all_targets = [
        b for b in blocks
        if b.get('type', 'paragraph') == 'paragraph'
        and (overwrite or not b.get('title_ja'))
    ]

    # 重複番号の段落は title_ja が一意に決まらないためスキップ
    from collections import Counter
    all_nums = Counter(b['number'] for b in blocks if b.get('type', 'paragraph') == 'paragraph')
    dup_nums = {n for n, c in all_nums.items() if c > 1}
    targets = [b for b in all_targets if b['number'] not in dup_nums]
    skipped = len(all_targets) - len(targets)

    click.echo(f"Headings to translate: {len(targets)}  (reg={reg} ver={version})")
    if skipped:
        click.echo(f"  Skipped {skipped} duplicate-numbered paragraphs (can't uniquely translate by number)")

    if dry_run:
        for b in targets:
            click.echo(f"  [{b['number']:6s}] {b['title']}")
        return

    key_var = "GEMINI_API_KEY" if engine == "gemini" else "ANTHROPIC_API_KEY"
    api_key = os.environ.get(key_var, "")
    if not api_key:
        raise click.ClickException(f"{key_var} 環境変数が設定されていません。")

    heading_pairs = [(b['number'], b['title']) for b in targets]
    chunks = [heading_pairs[i:i+chunk_size] for i in range(0, len(heading_pairs), chunk_size)]
    click.echo(f"Translating in {len(chunks)} chunk(s) of up to {chunk_size} headings each...")

    all_results: dict[str, str] = {}
    for ci, chunk in enumerate(chunks):
        click.echo(f"\n  Chunk {ci+1}/{len(chunks)} ({len(chunk)} headings)...")
        try:
            if engine == "gemini":
                results = translate_headings_gemini(api_key, system_prompt, chunk, model)
            else:
                results = {}
                for num, title in chunk:
                    r = _translate_anthropic(api_key, system_prompt, num, title, title)
                    results[num] = r.get('translation', title)
            all_results.update(results)
        except Exception as e:
            click.echo(f"  ✗ Chunk {ci+1} ERROR: {e}", err=True)

        if delay > 0 and ci + 1 < len(chunks):
            time.sleep(delay)

    # title_ja を書き込む
    written = 0
    for b in targets:
        ja = all_results.get(b['number'], '').strip()
        if ja:
            b['title_ja'] = ja
            written += 1
            click.echo(f"  [{b['number']:6s}] {b['title']:40s} → {ja}")
        else:
            click.echo(f"  [{b['number']:6s}] WARNING: no result for this entry")

    save_structured(data, reg, version)
    click.echo(f"\n✓ {written}/{len(targets)} headings translated.")
    click.echo(f"Saved → data/{reg}/{version}/structured.json")


if __name__ == '__main__':
    cli()
