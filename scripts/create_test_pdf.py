#!/usr/bin/env python3
"""
create_test_pdf.py  --  extract_pdf.py のテスト用 合成 PDF を生成する。

内容:
  - UN R13 形式のテキスト段落
  - 罫線で構成された表（ブレーキ性能要件）
  - 埋め込みラスター画像（簡易図面）
"""
from pathlib import Path
import fitz  # PyMuPDF

# PyMuPDF 1.24+ でhelvB/helvO が使えないため Liberation フォントを使用
_FONT_DIR = Path("/usr/share/fonts/truetype/liberation")
FONT_REGULAR = str(_FONT_DIR / "LiberationSans-Regular.ttf")
FONT_BOLD    = str(_FONT_DIR / "LiberationSans-Bold.ttf")
FONT_ITALIC  = str(_FONT_DIR / "LiberationSans-Italic.ttf")

OUTPUT_PATH = Path(__file__).parent.parent / "raw_pdf" / "R13" / "2023-10-05_Rev9" / "R13_Rev9_test.pdf"


def _draw_table(page, x0: float, y0: float, cols: list[str], rows: list[list[str]]):
    """罫線＋テキストで簡易テーブルを描画する。"""
    col_w = 120.0
    row_h = 22.0
    n_cols = len(cols)
    n_rows = len(rows) + 1  # ヘッダ行 + データ行

    total_w = col_w * n_cols
    total_h = row_h * n_rows

    shape = page.new_shape()

    # 外枠
    shape.draw_rect(fitz.Rect(x0, y0, x0 + total_w, y0 + total_h))

    # 列ライン
    for c in range(1, n_cols):
        x = x0 + col_w * c
        shape.draw_line(fitz.Point(x, y0), fitz.Point(x, y0 + total_h))

    # 行ライン
    for r in range(1, n_rows):
        y = y0 + row_h * r
        shape.draw_line(fitz.Point(x0, y), fitz.Point(x0 + total_w, y))

    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()

    # ヘッダテキスト
    for c, col_name in enumerate(cols):
        cx = x0 + col_w * c + 4
        cy = y0 + row_h * 0.5 - 5
        page.insert_text(
            fitz.Point(cx, cy + row_h * 0.5),
            col_name,
            fontsize=8,
            fontfile=FONT_REGULAR,
            color=(0, 0, 0),
        )

    # データテキスト
    for r, row in enumerate(rows, start=1):
        for c, cell in enumerate(row):
            cx = x0 + col_w * c + 4
            cy = y0 + row_h * r
            page.insert_text(
                fitz.Point(cx, cy + row_h * 0.5),
                str(cell),
                fontsize=7.5,
                fontfile=FONT_REGULAR,
                color=(0.2, 0.2, 0.2),
            )


def _draw_figure(page, x0: float, y0: float, width: float, height: float):
    """簡易ブレーキ系統図（矩形＋ラベル）を描画する。"""
    shape = page.new_shape()

    # ペダル
    shape.draw_rect(fitz.Rect(x0, y0 + height * 0.3, x0 + 40, y0 + height * 0.7))
    # マスターシリンダ
    shape.draw_rect(fitz.Rect(x0 + 60, y0 + height * 0.35, x0 + 120, y0 + height * 0.65))
    # 配管
    shape.draw_line(
        fitz.Point(x0 + 120, y0 + height * 0.5),
        fitz.Point(x0 + 160, y0 + height * 0.5),
    )
    # ブレーキキャリパ
    shape.draw_rect(fitz.Rect(x0 + 160, y0 + height * 0.25, x0 + 220, y0 + height * 0.75))

    shape.finish(color=(0.1, 0.1, 0.6), width=1.2)
    shape.commit()

    # ラベル
    labels = [
        (x0 + 8,  y0 + height * 0.85, "Brake Pedal"),
        (x0 + 68, y0 + height * 0.85, "Master Cylinder"),
        (x0 + 168, y0 + height * 0.85, "Caliper"),
    ]
    for lx, ly, lt in labels:
        page.insert_text(fitz.Point(lx, ly), lt, fontsize=7, color=(0.1, 0.1, 0.5))

    # 外枠（図全体）
    shape2 = page.new_shape()
    shape2.draw_rect(fitz.Rect(x0 - 5, y0 - 5, x0 + width + 5, y0 + height + 5))
    shape2.finish(color=(0.7, 0.7, 0.7), width=0.5, dashes="[2 2] 0")
    shape2.commit()


def create_pdf(output_path: Path):
    doc = fitz.open()

    # ------------------------------------------------------------------ #
    # Page 1: タイトルページ
    # ------------------------------------------------------------------ #
    page1 = doc.new_page(width=595, height=842)

    page1.insert_text(
        fitz.Point(72, 120),
        "UNITED NATIONS",
        fontsize=14, fontfile=FONT_REGULAR, color=(0.1, 0.1, 0.5),
    )
    page1.insert_text(
        fitz.Point(72, 150),
        "Agreement Concerning the Adoption of Harmonized Technical",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    page1.insert_text(
        fitz.Point(72, 165),
        "United Nations Regulations for Wheeled Vehicles",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    page1.insert_text(
        fitz.Point(72, 220),
        "REGULATION No. 13",
        fontsize=18, fontfile=FONT_BOLD, color=(0, 0, 0),
    )
    page1.insert_text(
        fitz.Point(72, 250),
        "Uniform provisions concerning the approval of vehicles of",
        fontsize=11, fontfile=FONT_REGULAR,
    )
    page1.insert_text(
        fitz.Point(72, 265),
        "categories M, N and O with regard to braking",
        fontsize=11, fontfile=FONT_REGULAR,
    )
    page1.insert_text(
        fitz.Point(72, 310),
        "Revision 9",
        fontsize=12, fontfile=FONT_BOLD,
    )
    page1.insert_text(
        fitz.Point(72, 330),
        "Date of entry into force: 5 October 2023",
        fontsize=10, fontfile=FONT_REGULAR, color=(0.4, 0.4, 0.4),
    )

    # ------------------------------------------------------------------ #
    # Page 2: 1. Scope + 2. Definitions
    # ------------------------------------------------------------------ #
    page2 = doc.new_page(width=595, height=842)
    y = 72.0

    page2.insert_text(fitz.Point(72, y), "1. Scope", fontsize=12, fontfile=FONT_BOLD)
    y += 20
    page2.insert_text(
        fitz.Point(72, y),
        "This Regulation applies to vehicles of categories M2, M3, N and O with regard",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page2.insert_text(
        fitz.Point(72, y),
        "to braking.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 22

    page2.insert_text(fitz.Point(72, y), "1.1 Vehicles of categories M2 and M3", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page2.insert_text(
        fitz.Point(72, y),
        "In the case of vehicles of categories M2 and M3, the braking requirements set",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page2.insert_text(
        fitz.Point(72, y),
        "out in Annex 4 shall apply.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 30

    page2.insert_text(fitz.Point(72, y), "2. Definitions", fontsize=12, fontfile=FONT_BOLD)
    y += 20
    page2.insert_text(
        fitz.Point(72, y),
        "For the purposes of this Regulation, the following definitions shall apply:",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 24

    page2.insert_text(fitz.Point(72, y), "2.1 Braking system", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page2.insert_text(
        fitz.Point(72, y),
        '"Braking system" means the combination of parts whose function is to',
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page2.insert_text(
        fitz.Point(72, y),
        "progressively reduce the speed of a moving vehicle, bring it to a halt, or keep",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page2.insert_text(
        fitz.Point(72, y),
        "it stationary if it is already halted; these functions are specified in paragraph 5.1.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 30

    # -------- Figure 1 --------
    page2.insert_text(fitz.Point(72, y), "Figure 1 - Basic braking system schematic", fontsize=9, fontfile=FONT_ITALIC, color=(0.3, 0.3, 0.3))
    y += 8
    _draw_figure(page2, x0=72, y0=y, width=250, height=90)
    y += 110
    page2.insert_text(
        fitz.Point(72, y),
        "Figure 1: Schematic representation of a basic hydraulic braking system",
        fontsize=8, fontfile=FONT_ITALIC, color=(0.4, 0.4, 0.4),
    )
    y += 30

    page2.insert_text(fitz.Point(72, y), "2.2 Service braking system", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page2.insert_text(
        fitz.Point(72, y),
        '"Service braking system" means the braking system which allows the driver to',
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page2.insert_text(
        fitz.Point(72, y),
        "control the movement of the vehicle and bring it to a halt safely, quickly and",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page2.insert_text(
        fitz.Point(72, y),
        "effectively, whatever its conditions of loading.",
        fontsize=10, fontfile=FONT_REGULAR,
    )

    # ------------------------------------------------------------------ #
    # Page 3: 2.3〜2.5 + Table 1 (ブレーキ性能要件)
    # ------------------------------------------------------------------ #
    page3 = doc.new_page(width=595, height=842)
    y = 72.0

    page3.insert_text(fitz.Point(72, y), "2.3 Secondary braking system", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page3.insert_text(
        fitz.Point(72, y),
        '"Secondary braking system" means the braking system which allows the driver to',
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page3.insert_text(
        fitz.Point(72, y),
        "halt the vehicle in the event of failure of the service braking system.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 24

    page3.insert_text(fitz.Point(72, y), "2.4 Parking braking system", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page3.insert_text(
        fitz.Point(72, y),
        '"Parking braking system" means the braking system which allows the vehicle to be',
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page3.insert_text(
        fitz.Point(72, y),
        "held stationary on an inclined surface even in the absence of the driver.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 24

    page3.insert_text(fitz.Point(72, y), "2.5 Endurance braking system", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page3.insert_text(
        fitz.Point(72, y),
        '"Endurance braking system" means a supplementary braking system able to stabilize',
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page3.insert_text(
        fitz.Point(72, y),
        "vehicle speed over a long downhill section.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 30

    # -------- Table 1 --------
    page3.insert_text(fitz.Point(72, y), "Table 1 - Braking performance requirements", fontsize=9, fontfile=FONT_ITALIC, color=(0.3, 0.3, 0.3))
    y += 10
    _draw_table(
        page3, x0=72, y0=y,
        cols=["System", "Min. decel. (m/s²)", "Pedal force (N)", "Efficiency (%)"],
        rows=[
            ["Service brake",    "5.0",  "≤ 700", "≥ 50"],
            ["Secondary brake",  "2.5",  "≤ 700", "≥ 25"],
            ["Parking brake",    "—",    "≤ 600", "Grade 18%"],
        ],
    )
    y += 22 * 4 + 15  # 4 rows × row_h + margin
    page3.insert_text(
        fitz.Point(72, y),
        "Table 1: Summary of minimum braking performance requirements (Annex 4)",
        fontsize=8, fontfile=FONT_ITALIC, color=(0.4, 0.4, 0.4),
    )
    y += 30

    # ------------------------------------------------------------------ #
    # Page 4: 5. General requirements + Table 2
    # ------------------------------------------------------------------ #
    page4 = doc.new_page(width=595, height=842)
    y = 72.0

    page4.insert_text(fitz.Point(72, y), "5. General requirements", fontsize=12, fontfile=FONT_BOLD)
    y += 20
    page4.insert_text(
        fitz.Point(72, y),
        "Every vehicle shall be equipped with a braking system which shall comply with",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page4.insert_text(
        fitz.Point(72, y),
        "the requirements set out in Annex 4.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 24

    page4.insert_text(fitz.Point(72, y), "5.1 Functions of braking systems", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page4.insert_text(
        fitz.Point(72, y),
        "The braking system of every vehicle shall fulfil the following functions:",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    for item in [
        "(a) service braking, enabling the movement of the vehicle to be controlled",
        "    and the vehicle to be brought to a halt safely, quickly and effectively;",
        "(b) secondary braking, enabling the vehicle to be brought to a halt in the",
        "    event of failure of the service braking system;",
        "(c) parking braking, enabling the vehicle to be held stationary on a gradient;",
        "(d) endurance braking, where fitted, to stabilize speed on long downhill sections.",
    ]:
        page4.insert_text(fitz.Point(72, y), item, fontsize=10, fontfile=FONT_REGULAR)
        y += 14
    y += 16

    page4.insert_text(fitz.Point(72, y), "5.2 Braking surfaces", fontsize=10, fontfile=FONT_BOLD)
    y += 16
    page4.insert_text(
        fitz.Point(72, y),
        "The braking systems prescribed in paragraph 5.1 shall be so designed that they",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page4.insert_text(
        fitz.Point(72, y),
        "act on braking surfaces permanently connected to the wheels through components",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page4.insert_text(
        fitz.Point(72, y),
        "of adequate strength. Where braking effort on an axle is provided by the engine",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 14
    page4.insert_text(
        fitz.Point(72, y),
        "or an electric motor, a disconnect device shall be permitted.",
        fontsize=10, fontfile=FONT_REGULAR,
    )
    y += 30

    # -------- Table 2 --------
    page4.insert_text(fitz.Point(72, y), "Table 2 - ABS requirements by vehicle category", fontsize=9, fontfile=FONT_ITALIC, color=(0.3, 0.3, 0.3))
    y += 10
    _draw_table(
        page4, x0=72, y0=y,
        cols=["Category", "ABS mandatory", "EBS optional", "Annex ref."],
        rows=[
            ["M2",  "Yes", "Yes", "Annex 13"],
            ["M3",  "Yes", "Yes", "Annex 13"],
            ["N2",  "Yes", "Optional", "Annex 13"],
            ["N3",  "Yes", "Yes", "Annex 13"],
            ["O3",  "No",  "—", "—"],
            ["O4",  "Yes", "—", "Annex 14"],
        ],
    )

    # ------------------------------------------------------------------ #
    # 保存
    # ------------------------------------------------------------------ #
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()
    print(f"Created test PDF: {output_path}")
    print(f"  Pages: 4")
    print(f"  Contains: 2 tables (drawn with lines), 1 figure (vector drawing)")


if __name__ == "__main__":
    create_pdf(OUTPUT_PATH)
