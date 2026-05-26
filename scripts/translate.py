#!/usr/bin/env python3
"""
translate.py  --  未翻訳の段落をClaude APIで翻訳・要約する。

Usage:
    python translate.py --reg R13 --version rev2
    python translate.py --reg R13 --version rev2 --dry-run
"""

import json
import time
from pathlib import Path
from dataclasses import asdict

import click

try:
    import anthropic
except ImportError:
    anthropic = None

DATA_DIR = Path(__file__).parent.parent / "data"

# --------------------------------------------------------------------------- #
# プロンプト
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """あなたは国連自動車法規（UN Regulations）の日本語翻訳の専門家です。
以下の原則に従って翻訳・要約を行ってください。

1. 翻訳は法令文体（〜する、〜しなければならない等）を使用すること
2. 技術用語は一般的な日本語自動車業界の慣用表現を優先すること
3. 要約は1〜2文で、その段落の核心を端的に表すこと
4. 原文の法的ニュアンス（shall/should/may の違い）を正確に反映すること
   - shall → 〜しなければならない（義務）
   - should → 〜すべきである（推奨）
   - may → 〜してもよい（許可）
"""

def build_user_prompt(number: str, title: str, text: str) -> str:
    return f"""以下のUN法規の段落を日本語に翻訳し、要約を作成してください。

段落番号: {number}
タイトル: {title}
原文:
{text}

以下のJSON形式で回答してください（他のテキストは不要です）:
{{
  "translation": "日本語翻訳文（改行あり・法令文体）",
  "summary_ja": "1〜2文の日本語要約"
}}"""


# --------------------------------------------------------------------------- #
# 翻訳処理
# --------------------------------------------------------------------------- #

def translate_paragraph(client, number: str, title: str, text: str) -> dict:
    """1段落を翻訳して {"translation": ..., "summary_ja": ...} を返す。"""
    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_user_prompt(number, title, text)}
        ],
    )
    raw = message.content[0].text.strip()
    # JSON部分を抽出（前後にテキストが混入した場合への対処）
    start = raw.find('{')
    end = raw.rfind('}') + 1
    return json.loads(raw[start:end])


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
@click.option('--reg', required=True, help='法規番号 (例: R13)')
@click.option('--version', required=True, help='バージョン (例: rev2)')
@click.option('--dry-run', is_flag=True, help='API呼び出しをせずに対象段落を一覧表示する')
@click.option('--limit', default=0, help='翻訳する最大段落数（0=無制限）')
@click.option('--delay', default=1.0, help='API呼び出し間隔（秒）')
def main(reg: str, version: str, dry_run: bool, limit: int, delay: float):
    """未翻訳段落をClaude APIで翻訳・要約する"""
    if not dry_run and anthropic is None:
        raise click.ClickException("anthropic SDK not installed. Run: pip install anthropic")

    data = load_structured(reg, version)
    paragraphs = data['paragraphs']

    targets = [p for p in paragraphs if p.get('status') == 'untranslated']
    click.echo(f"Found {len(targets)} untranslated paragraphs in {reg}/{version}.")

    if dry_run:
        for p in targets:
            click.echo(f"  [{p['number']}] {p['title']}")
        return

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 環境変数から自動取得
    processed = 0

    for p in targets:
        if limit > 0 and processed >= limit:
            click.echo(f"Limit reached ({limit}). Stopping.")
            break

        click.echo(f"Translating [{p['number']}] {p['title']} ...")
        try:
            result = translate_paragraph(client, p['number'], p['title'], p['text'])
            p['translation'] = result['translation']
            p['summary_ja'] = result['summary_ja']
            p['status'] = 'translated'
            processed += 1
            click.echo(f"  → Done. Summary: {p['summary_ja'][:60]}...")
        except Exception as e:
            click.echo(f"  ERROR: {e}", err=True)
            p['status'] = 'error'

        if delay > 0 and processed < len(targets):
            time.sleep(delay)

    save_structured(data, reg, version)
    click.echo(f"\nTranslated {processed} paragraphs. Saved to {reg}/{version}/structured.json")


if __name__ == '__main__':
    main()
