# UN Regulations 日本語訳サイト

国連自動車法規（UN Regulations）の非公式日本語翻訳・閲覧サイトです。

## ディレクトリ構成

```
Regulations-to-ja/
├── raw_pdf/                        # 原文PDF（版ごとに管理）
│   └── R13/
│       ├── 2020-01-01_rev1/        # R13 第1版
│       └── 2023-06-15_rev2/        # R13 第2版
│
├── data/                           # 構造化・翻訳済みデータ
│   └── R13/
│       ├── rev1/structured.json    # rev1 段落JSON
│       ├── rev2/structured.json    # rev2 段落JSON（差分フラグ付き）
│       ├── diff/rev1_to_rev2.json  # 差分レポート
│       └── glossary.json           # 用語集
│
├── scripts/
│   ├── extract_pdf.py              # PDF → 構造化JSON + 差分検出
│   ├── translate.py                # JSON → Claude API 翻訳・要約
│   └── build_html.py               # JSON → 静的HTML（Jinja2）
│
├── templates/                      # Jinja2 HTMLテンプレート
│   ├── regulation_page.html
│   └── index.html
│
├── site/                           # 生成済み静的HTML
│   ├── index.html
│   ├── R13/index.html
│   ├── css/main.css
│   └── js/viewer.js
│
└── requirements.txt
```

## 処理パイプライン

### Step 1: PDF抽出（extract_pdf.py）

```bash
# PDFからJSONを生成
python scripts/extract_pdf.py extract \
  --pdf raw_pdf/R13/2023-06-15_rev2/R13.pdf \
  --reg R13 --version rev2 --date 2023-06-15

# 2版間の差分を生成し、rev2のJSONに変更フラグを付与
python scripts/extract_pdf.py diff \
  --reg R13 --from rev1 --to rev2
```

### Step 2: 翻訳（translate.py）

```bash
export ANTHROPIC_API_KEY="your-key"

# 未翻訳段落のみを翻訳（差分部分だけ処理するため低コスト）
python scripts/translate.py --reg R13 --version rev2

# 対象を確認するだけ（API呼び出しなし）
python scripts/translate.py --reg R13 --version rev2 --dry-run
```

### Step 3: HTMLビルド（build_html.py）

```bash
# 特定法規のHTMLを生成
python scripts/build_html.py page --reg R13 --version rev2

# 全法規を一括ビルド
python scripts/build_html.py all
```

## structured.json スキーマ

各段落は以下のフィールドを持ちます：

| フィールド | 説明 |
|---|---|
| `uid` | `R13-<SHA256[:8]>` 形式の段落固有ID |
| `number` | 段落番号（例: `"5.1"`） |
| `title` | 段落タイトル |
| `text` | 英語原文 |
| `level` | 階層レベル（1=章, 2=節, 3=項）|
| `parent` | 親段落の番号 |
| `status` | `untranslated` / `translated` / `error` |
| `translation` | 日本語翻訳文 |
| `summary_ja` | 日本語要約（1〜2文） |
| `modified` | `true` = 前版から変更あり |
| `justification` | 変更理由（MODIFIED/SLIDの場合） |
| `prev_uid` | 前版対応段落のUID（SLID追跡用） |

## 免責事項

本サイトの翻訳は非公式参考訳です。法的効力は国連発行のオリジナル英語テキストにのみあります。
本サイトはUNECEによって承認・後援されたものではありません。
