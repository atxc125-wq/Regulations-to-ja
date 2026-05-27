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

# JSON Schema（responseSchema で translation/summary_ja を確実に返させる）
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "translation": {
            "type": "STRING",
            "description": "完全な日本語翻訳文（法令文体）",
        },
        "summary_ja": {
            "type": "STRING",
            "description": "1〜2文の日本語要約",
        },
    },
    "required": ["translation", "summary_ja"],
}


def _translate_gemini(
    api_key: str,
    system_prompt: str,
    number: str,
    title: str,
    text: str,
    model: str = "gemini-2.5-pro",
) -> dict:
    """
    Google Gemini API で翻訳を実行する（urllib 直接呼び出し）。
    responseMimeType + responseSchema で Structured Outputs を使用する。
    """
    url = f"{_GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"

    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {"role": "user", "parts": [{"text": build_user_prompt(number, title, text)}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API HTTP {e.code}: {err_body[:400]}")

    # レスポンスから候補テキストを取得
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response structure: {data}")

    return json.loads(raw)


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
) -> dict:
    """エンジンを選択して翻訳を実行する。"""
    if engine == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 環境変数が設定されていません。")
        return _translate_gemini(api_key, system_prompt, number, title, text)

    elif engine == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY 環境変数が設定されていません。")
        return _translate_anthropic(api_key, system_prompt, number, title, text)

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
# CLI
# --------------------------------------------------------------------------- #

@click.command()
@click.option('--reg',     required=True,                      help='法規番号 (例: R13)')
@click.option('--version', default='',                         help='バージョン (例: Rev9)。--show-glossary 時は省略可')
@click.option('--engine',  default='gemini',
              type=click.Choice(['gemini', 'anthropic']),
              show_default=True,                                help='使用する翻訳エンジン')
@click.option('--dry-run', is_flag=True,                       help='API未使用。対象段落を一覧表示して終了')
@click.option('--limit',   default=0,   type=int,              help='翻訳する最大段落数（0=無制限）')
@click.option('--delay',   default=1.0, type=float,            help='API呼び出し間隔（秒）')
@click.option('--show-glossary', is_flag=True,                 help='用語集とシステムプロンプトを表示して終了')
def main(
    reg: str, version: str, engine: str,
    dry_run: bool, limit: int, delay: float, show_glossary: bool,
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
    targets = [
        b for b in blocks
        if b.get('type', 'paragraph') == 'paragraph'
        and b.get('status') == 'untranslated'
    ]

    click.echo(f"Target: {reg}/{version}  |  untranslated: {len(targets)}  |  engine: {engine}")
    click.echo(f"Glossary: {len(glossary_terms)} term(s) loaded.")

    if dry_run:
        click.echo("\nDry-run — paragraphs that would be translated:")
        for p in (targets[:limit] if limit else targets):
            click.echo(f"  [{p['number']:6s}] {p['title']}")
        return

    # API キーの存在チェック（早期終了）
    key_var = "GEMINI_API_KEY" if engine == "gemini" else "ANTHROPIC_API_KEY"
    if not os.environ.get(key_var):
        raise click.ClickException(
            f"{key_var} 環境変数が設定されていません。\n"
            f"  export {key_var}=<your-key>\n"
            f"  --dry-run オプションで動作確認のみ行うことができます。"
        )

    processed = 0
    errors    = 0

    for p in targets:
        if limit > 0 and processed >= limit:
            click.echo(f"\nLimit reached ({limit}). Stopping.")
            break

        click.echo(f"\nTranslating [{p['number']}] {p['title']} ...")
        try:
            result = translate_paragraph(
                engine, system_prompt,
                p['number'], p['title'], p['text'],
            )
            p['translation'] = result['translation']
            p['summary_ja']  = result['summary_ja']
            p['status']      = 'done'
            processed += 1
            preview = p['summary_ja'][:70].replace('\n', ' ')
            click.echo(f"  ✓ {preview}{'...' if len(p['summary_ja']) > 70 else ''}")

        except Exception as e:
            click.echo(f"  ✗ ERROR: {e}", err=True)
            p['status'] = 'error'
            errors += 1

        if delay > 0 and (processed + errors) < len(targets):
            time.sleep(delay)

    save_structured(data, reg, version)
    click.echo(f"\n{'='*52}")
    click.echo(f"Done: {processed} translated, {errors} error(s).")
    click.echo(f"Saved → data/{reg}/{version}/structured.json")


if __name__ == '__main__':
    main()
