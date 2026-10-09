"""Generate synthetic textbook PDFs for tests (no copyrighted material)."""
from __future__ import annotations
import pymupdf

W, H = 612, 792
BODY, H1, H2, H3 = 10, 20, 14, 11.5


def _lines(page, x, y, lines, size=BODY, font="helv", lead=None, color=(0, 0, 0)):
    lead = lead or size * 1.3
    for ln in lines:
        page.insert_text((x, y), ln, fontsize=size, fontname=font, color=color)
        y += lead
    return y


def _png(w=120, h=80, shade=200) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, (shade, 80, 120))
    return pix.tobytes("png")


def _chrome(page, n, header="Test Textbook of Pediatrics"):
    page.insert_text((72, 40), header, fontsize=8, fontname="helv")
    page.insert_text((300, 770), str(n), fontsize=8, fontname="helv")


PARA_A = ["Thalassemia is an inherited disorder of hemoglobin synthesis. The pedi-",
          "atric patient presents with pallor and fatigue at age 6 months. Long-",
          "term transfusion is indicated when hemoglobin falls below 7.0 g/dL [1]."]
PARA_A_COL = ["Thalassemia is an inherited disorder of",
              "hemoglobin synthesis. The pedi-",
              "atric patient presents with pallor and",
              "fatigue at age 6 months. Long-",
              "term transfusion is indicated when",
              "hemoglobin falls below 7.0 g/dL [1]."]
PARA_B = ["Treatment with deferasirox 20 mg/kg per day reduces iron burden.",
          "Serum ferritin should be kept under 1000 ng/mL (range 500-1000) [2,3]."]
def wrap(lines, width=40):
    import textwrap
    return textwrap.wrap(" ".join(lines), width)


LEFT = ["Anemia is defined by hemoglobin concentration more than two standard",
        "deviations below the mean for age and sex. The prevalence is 25% in",
        "infants and 12.5% in school-age children according to surveys [1]."]
RIGHT = ["The second column continues with diagnostic evaluation. A complete",
         "blood count and reticulocyte count are the first tests. Dose is 5 mg",
         "every 8 hours for 14 days in children over 2 years."]


def two_column_book(path, with_scanned=False, many_figures=False):
    doc = pymupdf.open()
    toc = []
    # ---- Chapter 1 : pages 1-3 (two-column, headings, table, figure, refs)
    p = doc.new_page(width=W, height=H); _chrome(p, 1)
    p.insert_text((72, 90), "Chapter 1", fontsize=12, fontname="hebo")
    p.insert_text((72, 120), "Thalassemia and Anemia", fontsize=H1, fontname="hebo")
    p.insert_text((72, 160), "Definition and Epidemiology", fontsize=H2, fontname="hebo")
    _lines(p, 72, 185, wrap(LEFT, 42))
    y = _lines(p, 72, 250, PARA_A_COL)
    p.insert_text((72, 300), "Pathophysiology", fontsize=H3, fontname="hebo")
    _lines(p, 72, 340, wrap(PARA_B, 42))
    _lines(p, 320, 185, wrap(RIGHT, 42))
    _lines(p, 320, 260, wrap(["Another paragraph in the right column explains that the dose of folic acid is 1 mg daily for most patients."], 42))
    doc.new_page(width=W, height=H)
    p = doc[1]; _chrome(p, 2)
    p.insert_text((72, 90), "Clinical Manifestations", fontsize=H2, fontname="hebo")
    _lines(p, 72, 115, ["Children show growth failure and splenomegaly [4]. Hemoglobin of",
                        "9.5 g/dL is typical for thalassemia intermedia."])
    p.insert_text((72, 170), "Table 1.1 Hematologic findings by type", fontsize=BODY, fontname="hebo")
    rows = [["Type", "Hb (g/dL)", "MCV (fL)"], ["Minor", "10.5", "65"], ["Intermedia", "7.5-9.5", "60"], ["Major", "<7.0", "55"]]
    x0, y0, cw, rh = 72, 180, 110, 20
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            p.draw_rect(rect, width=0.8)
            p.insert_text((rect.x0 + 4, rect.y0 + 14), v, fontsize=9, fontname="helv")
    p.insert_image(pymupdf.Rect(72, 290, 252, 400), stream=_png(180, 110))
    p.insert_text((72, 416), "Figure 1.1. Peripheral blood smear showing microcytosis.", fontsize=9, fontname="helv")
    if many_figures:
        for i in range(2, 5):
            p.insert_image(pymupdf.Rect(300, 290 + (i - 2) * 140 - 0, 420, 370 + (i - 2) * 140 - 0 if False else 350 + (i - 2) * 140), stream=_png(100 + i * 10, 70, 100 + i * 30))
            p.insert_text((300, 360 + (i - 2) * 140), f"Figure 1.{i}. Additional smear image {i}.", fontsize=9, fontname="helv")
    # callout in shaded box
    box = pymupdf.Rect(72, 640, 540, 720)
    p.draw_rect(box, color=None, fill=(0.92, 0.92, 0.92))
    p.insert_text((80, 656), "KEY POINTS", fontsize=BODY, fontname="hebo")
    _lines(p, 80, 672, ["- Screen early with complete blood count.", "- Avoid iron supplementation unless deficiency is proven."])
    p = doc.new_page(width=W, height=H); _chrome(p, 3)
    p.insert_text((72, 90), "References", fontsize=H2, fontname="hebo")
    _lines(p, 72, 115, ["1. Author A, Author B. Hemoglobin disorders in children. J Pediatr 2019;45:100-8.",
                        "2. Author C. Iron chelation therapy. Blood 2018;132:23-30. doi:10.0000/abc",
                        "3. Author D. Ferritin targets. Lancet 2020;395:1-9.",
                        "4. Author E. Splenomegaly in thalassemia. Pediatrics 2017;140:e1."])
    toc.append([1, "Chapter 1 Thalassemia and Anemia", 1])
    # ---- Chapter 2 : pages 4-5 (single-column)
    p = doc.new_page(width=W, height=H); _chrome(p, 4)
    p.insert_text((72, 90), "Chapter 2", fontsize=12, fontname="hebo")
    p.insert_text((72, 120), "Asthma in Children", fontsize=H1, fontname="hebo")
    p.insert_text((72, 160), "Diagnosis", fontsize=H2, fontname="hebo")
    _lines(p, 72, 185, ["Asthma presents with recurrent wheezing and cough. Salbutamol 2.5 mg",
                        "nebulized every 20 minutes for 1 hour is used in acute exacerbations.",
                        "See Chapter 1 for anemia and Table 1.1 for hematologic values."])
    p.insert_text((72, 260), "Management", fontsize=H2, fontname="hebo")
    _lines(p, 72, 285, ["1. Assess severity.", "2. Give bronchodilator.", "3. Reassess after 1 hour."])
    p = doc.new_page(width=W, height=H); _chrome(p, 5)
    p.insert_text((72, 90), "Inhaled corticosteroids are first-line controllers [1].", fontsize=BODY, fontname="helv")
    p.insert_text((72, 110), "References", fontsize=H2, fontname="hebo")
    _lines(p, 72, 135, ["1. Author F. Asthma guideline. Allergy 2021;76:1-10."])
    toc.append([1, "Chapter 2 Asthma in Children", 4])
    if with_scanned:
        p = doc.new_page(width=W, height=H)
        p.insert_image(pymupdf.Rect(0, 0, W, H), stream=_png(600, 780, 230))   # image-only page
        toc.append([1, "Chapter 3 Scanned Chapter", 6])
    doc.set_toc(toc)
    doc.set_metadata({"title": "Test Textbook of Pediatrics"})
    doc.save(path)
    doc.close()
    return path


def single_column_pdf(path):
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    p.insert_text((72, 90), "A Simple Single Column Document", fontsize=H1, fontname="hebo")
    _lines(p, 72, 130, ["This document has one column of text only. It is used to verify that",
                        "single-column pages keep their natural order and do not break lines",
                        "in the middle of a paragraph, preserving 3.5% accuracy."])
    doc.save(path); doc.close()
    return path


def shared_page_book(path):
    """Two chapters sharing a page (chapter 2 starts mid-page), bold ALL-CAPS / Title Case headings at body size,
    a composite figure (image fragments + live text labels) with a caption beside it, and font-encoding artifacts."""
    doc = pymupdf.open()
    # ---- page 1: chapter 10 start
    p = doc.new_page(width=W, height=H)
    p.insert_text((72, 40), "Chapter 10  Alpha Disorders", fontsize=10, fontname="helv")        # running header (small)
    p.insert_text((72, 90), "Chapter 10", fontsize=16, fontname="hebo")
    p.insert_text((72, 120), "Alpha Disorders", fontsize=21, fontname="hebo")
    p.insert_text((72, 145), "Jane Q. Author", fontsize=12, fontname="heit")
    _lines(p, 72, 160, ["Te disease is common. Tere are many cases. Alpha is rare- like", "beta. In 2- 7 days a%er onset fever occurs; a- b c- d e- f g- h."], size=9)
    _lines(p, 72, 188, ["The disease starts after a cold, and there are other signs."], size=9)
    p.insert_text((72, 220), "ETIOLOGY", fontsize=10, fontname="hebo")
    _lines(p, 72, 235, ["Cause is unknown in 40% of cases."], size=9)
    p.insert_text((72, 270), "Clinical Course", fontsize=10, fontname="hebo")
    _lines(p, 72, 285, ["Fever lasts 5 days."], size=9)
    p.insert_text((72, 320), "PROGNOSIS", fontsize=10, fontname="hebo")
    _lines(p, 72, 335, ["Good in 95% of patients."], size=9)
    # composite figure: fragments + text labels, caption to the right
    p.draw_oval(pymupdf.Rect(100, 420, 300, 480), width=1)
    p.draw_rect(pymupdf.Rect(100, 490, 300, 500), width=1)
    p.draw_rect(pymupdf.Rect(100, 505, 200, 515), width=1)
    p.insert_text((110, 440), "WARNING SIGNS", fontsize=7, fontname="helv")
    p.insert_text((110, 530), "Shock (DSS) 2 organs", fontsize=7, fontname="helv")
    p.insert_text((110, 545), "Liver: AST >= 1000", fontsize=7, fontname="helv")
    p.insert_text((330, 520), "Fig. 10.1 Case classification", fontsize=8, fontname="helv")
    p.insert_text((330, 531), "and severity levels.", fontsize=8, fontname="helv")
    # ---- page 2: end of chapter 10, then chapter 11 begins mid-page
    p = doc.new_page(width=W, height=H)
    p.insert_text((72, 40), "Chapter 10  Alpha Disorders", fontsize=10, fontname="helv")
    p.insert_text((72, 80), "OUTCOME", fontsize=10, fontname="hebo")
    _lines(p, 72, 95, ["Complications are rare in 3% of children."], size=9)
    p.insert_text((114, 770), "\x01\x02\x03\x04\x05\x06\x07\x08 \x01\x02\x03\x04\x05\x06\x07\x08\x01\x02\x03", fontsize=7, fontname="helv")
    p.insert_text((72, 400), "Chapter 11", fontsize=16, fontname="hebo")
    p.insert_text((72, 425), "Beta Disorders", fontsize=21, fontname="hebo")
    _lines(p, 72, 470, ["Beta disorders affect 12% of infants."], size=9)
    doc.save(path); doc.close()
    return path


def two_column_with_junk_footer(path):
    """Single-page chapter: two columns plus a full-width undecodable footer line that must not hide the gutter."""
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    p.insert_text((72, 90), "Chapter 5", fontsize=16, fontname="hebo")
    p.insert_text((72, 115), "Solo Chapter", fontsize=21, fontname="hebo")
    _lines(p, 72, 160, wrap(["Left column first paragraph runs down the left side of the page and carries on for several lines of text."], 42), size=9)
    _lines(p, 320, 160, wrap(["Right column paragraph must be read only after the entire left column has finished being read."], 42), size=9)
    _lines(p, 72, 300, wrap(["Left column second paragraph sits below the first one in the same column of the page."], 42), size=9)
    p.insert_text((114, 770), "\x01\x02\x03\x04\x05\x06\x07\x08 \x01\x02\x03\x04\x05\x06\x07\x08\x01\x02\x03", fontsize=7, fontname="helv")
    doc.save(path); doc.close()
    return path
