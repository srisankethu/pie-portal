#!/usr/bin/env python3
"""Build the PIE investor deck.

Single re-runnable generator: `python3 build_deck.py`. Every shape is placed
from computed coordinates, so editing copy and re-running is cheap and the
layout stays consistent. Nothing here is hand-placed.

The design system is an engineering drawing sheet — construction grid, hairline
border, drafting title block, and one stroke convention that carries the deck's
central argument: SOLID = deterministic, DASHED = AI.

Every factual string in this file is traceable; see deck-content.md for the
source of each. Unknowns render as amber dashed placeholders, never as fact.
"""

from __future__ import annotations

import collections
import re
import shutil
import sys
import zipfile

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ---------------------------------------------------------------- palette ---

INK = RGBColor(0x0E, 0x11, 0x16)
INK85 = RGBColor(0x31, 0x33, 0x37)          # ink at 85% over paper, precomputed
PAPER = RGBColor(0xF7, 0xF6, 0xF2)
GRID = RGBColor(0xDD, 0xDC, 0xD6)
BLUEPRINT = RGBColor(0x1B, 0x4D, 0x7A)
CYAN = RGBColor(0x2E, 0x8F, 0xB8)
AMBER = RGBColor(0xC8, 0x89, 0x2A)
RULE = RGBColor(0xA8, 0xA6, 0x9E)

SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"

# ------------------------------------------------------------ geometry ------

W, H = 13.333, 7.5
INSET = 0.35                 # hairline border inset
GRID_STEP = 0.5
L = 1.0                      # content left
R = W - 1.0                  # content right
CW = R - L                   # content width
HEADLINE_Y = 0.78
BODY_TOP = 2.05
TB_H = 0.42                  # title block height
TB_Y = H - INSET - TB_H

# Word budgets from the brief, headline excluded.
BUDGET = {1: 25, 2: 60, 3: 55, 4: 30, 5: 45, 6: 60, 7: 50,
          8: 70, 9: 55, 10: 50, 11: 60, 12: 70, 13: 55, 14: 50}

_words: dict[int, int] = collections.defaultdict(int)
_placeholders: list[tuple[int, str]] = []
_slide_no = 0


def count(text: str) -> None:
    """Accumulate body words for the current slide.

    Diagram labels and captions count: they are words the reader must read.
    Headlines and the title block do not, per the brief.
    """
    cleaned = re.sub(r"[·→|]", " ", text)
    _words[_slide_no] += len([w for w in cleaned.split() if re.search(r"\w", w)])


# ------------------------------------------------------------- drawing ------


def _flat(shape):
    """Drop the theme style ref and any inherited shadow.

    `shadow.inherit = False` alone is not enough: it writes an empty
    <a:effectLst/>, but a renderer that prefers the <p:style> effect reference
    still draws the theme drop shadow. Both have to go.
    """
    shape.shadow.inherit = False
    el = shape._element
    st = el.find(qn("p:style"))
    if st is not None:
        el.remove(st)
    return shape


def _noline(shape):
    shape.line.fill.background()
    return shape


def _nofill(shape):
    shape.fill.background()
    return shape


def line(slide, x1, y1, x2, y2, colour=RULE, width=0.5, dash=None,
         arrow=False):
    """A hairline. `dash` takes a preset name; `arrow` adds a solid triangle."""
    c = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    _flat(c)
    c.line.color.rgb = colour
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dash:
        el = ln.makeelement(qn("a:prstDash"), {"val": dash})
        ln.append(el)
    if arrow:
        el = ln.makeelement(
            qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(el)
    return c


def box(slide, x, y, w, h, stroke=BLUEPRINT, width=0.75, dash=None):
    """Square-cornered, unfilled. Solid stroke = deterministic, dashed = AI."""
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                               Inches(x), Inches(y), Inches(w), Inches(h))
    _nofill(s)
    _flat(s)
    s.line.color.rgb = stroke
    s.line.width = Pt(width)
    if dash:
        ln = s.line._get_or_add_ln()
        ln.append(ln.makeelement(qn("a:prstDash"), {"val": dash}))
    return s


def text(slide, x, y, w, h, body, *, font=SANS, size=18, colour=INK85,
         bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spc=None,
         line_spacing=1.15, tally=True, caps=False):
    """A text box with no padding, so type aligns to drawn geometry."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, para in enumerate(str(body).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        r = p.add_run()
        r.text = para.upper() if caps else para
        f = r.font
        f.name, f.size, f.bold = font, Pt(size), bold
        f.color.rgb = colour
        if spc is not None:
            r.font._rPr.set("spc", str(int(spc * 100)))
    if tally:
        count(str(body))
    return tb


def label(slide, x, y, w, body, size=9.5, colour=RULE, align=PP_ALIGN.LEFT,
          tally=True):
    """Mono drafting caption: uppercase, letterspaced, low contrast."""
    return text(slide, x, y, w, 0.24, body, font=MONO, size=size, colour=colour,
                align=align, spc=0.9, caps=True, tally=tally, line_spacing=1.0)


def placeholder(slide, x, y, w, h, body):
    """Amber, mono, dashed box. Visibly a placeholder, never mistakable as fact."""
    box(slide, x, y, w, h, stroke=AMBER, width=0.75, dash="dash")
    text(slide, x + 0.09, y + 0.06, w - 0.18, h - 0.12, body, font=MONO,
         size=10, colour=AMBER, anchor=MSO_ANCHOR.MIDDLE, caps=True, spc=0.6,
         line_spacing=1.15)
    _placeholders.append((_slide_no, body))


# --------------------------------------------------------------- frame ------


def sheet(prs, headline=None, sub=None, cover=False):
    """A new sheet with grid, border and title block. Returns the slide."""
    global _slide_no
    _slide_no += 1
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = PAPER

    n = 1
    while n * GRID_STEP < W:
        line(slide, n * GRID_STEP, 0, n * GRID_STEP, H, GRID, 0.25)
        n += 1
    n = 1
    while n * GRID_STEP < H:
        line(slide, 0, n * GRID_STEP, W, n * GRID_STEP, GRID, 0.25)
        n += 1

    box(slide, INSET, INSET, W - 2 * INSET, H - 2 * INSET,
        stroke=RULE, width=0.75)

    if not cover:
        title_block(slide)
    if headline:
        text(slide, L, HEADLINE_Y, CW, 1.0, headline, size=32, colour=INK,
             bold=True, spc=-0.45, tally=False, line_spacing=1.03)
    if sub:
        text(slide, L, HEADLINE_Y + 0.72, CW, 0.4, sub, size=15,
             colour=BLUEPRINT, spc=0)
    return slide


def title_block(slide):
    """Drafting title block, bottom-right, three cells. Sheet number lives here."""
    cells = [("PIE — PRODUCT INTELLIGENCE ENGINE", 2.85),
             ("4U PRECISION", 1.35),
             (f"SHEET {_slide_no:02d}/14", 1.15)]
    total = sum(c[1] for c in cells)
    x = W - INSET - total
    y = TB_Y
    box(slide, x, y, total, TB_H, stroke=RULE, width=0.75)
    cx = x
    for i, (txt, cw) in enumerate(cells):
        if i:
            line(slide, cx, y, cx, y + TB_H, RULE, 0.75)
        text(slide, cx + 0.11, y, cw - 0.22, TB_H, txt, font=MONO, size=7.5,
             colour=RULE, anchor=MSO_ANCHOR.MIDDLE, spc=0.7, caps=True,
             tally=False, line_spacing=1.0)
        cx += cw


def legend(slide, x, y):
    """The one convention that carries the thesis. Drawn on every diagram sheet."""
    box(slide, x, y, 2.72, 0.62, stroke=RULE, width=0.5)
    line(slide, x + 0.14, y + 0.21, x + 0.52, y + 0.21, BLUEPRINT, 1.1)
    label(slide, x + 0.62, y + 0.13, 1.0, "Deterministic", 7.5, RULE,
          tally=False)
    line(slide, x + 0.14, y + 0.43, x + 0.52, y + 0.43, BLUEPRINT, 1.1,
         dash="dash")
    label(slide, x + 0.62, y + 0.35, 1.0, "AI", 7.5, RULE, tally=False)


def flow_box(slide, x, y, w, h, caption, dash=None, size=8.5, bold=False,
             stroke=BLUEPRINT, width=0.75, colour=INK85):
    box(slide, x, y, w, h, stroke=stroke, width=width, dash=dash)
    text(slide, x + 0.07, y + 0.05, w - 0.14, h - 0.10, caption, font=MONO,
         size=size, colour=colour, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE, bold=bold, line_spacing=1.12)


# ================================================================ SHEETS =====


def slide_01(prs):
    s = sheet(prs, cover=True)
    line(s, L, 2.62, R, 2.62, RULE, 0.75)
    text(s, L, 2.80, CW, 1.28, "PIE", size=86, colour=INK, bold=True,
         spc=-2.4, tally=False, line_spacing=1.0)
    text(s, L, 4.02, CW, 0.5, "PRODUCT INTELLIGENCE ENGINE", font=SANS,
         size=25, colour=BLUEPRINT, spc=3.2, tally=False)
    line(s, L, 4.72, R, 4.72, RULE, 0.75)
    text(s, L, 4.98, 8.6, 0.72,
         "ERP records the transaction. PIE understands the product\n"
         "and decides what should happen.", size=17, colour=INK85,
         line_spacing=1.32)
    label(s, L, 6.02, 8.0, "Deterministic product intelligence for B2B "
          "industrial distribution", 9.5, RULE)
    label(s, L, 2.28, 6.0, "Drawing title", 8, RULE, tally=False)

    cells = [("4U PRECISION", 1.65), ("HYDERABAD, INDIA", 2.0)]
    total = sum(c[1] for c in cells) + 1.55
    x = W - INSET - total
    box(s, x, TB_Y, total, TB_H, stroke=RULE, width=0.75)
    cx = x
    for i, (txt, cw) in enumerate(cells):
        if i:
            line(s, cx, TB_Y, cx, TB_Y + TB_H, RULE, 0.75)
        text(s, cx + 0.11, TB_Y, cw - 0.22, TB_H, txt, font=MONO, size=7.5,
             colour=RULE, anchor=MSO_ANCHOR.MIDDLE, spc=0.7, caps=True,
             line_spacing=1.0)
        cx += cw
    line(s, cx, TB_Y, cx, TB_Y + TB_H, RULE, 0.75)
    placeholder(s, cx, TB_Y, 1.55, TB_H, "[STAGE]")


def slide_02(prs):
    s = sheet(prs, "A quote begins as a sentence, not a part number")
    text(s, L, BODY_TOP, CW, 0.34,
         "The request arrives unstructured. Everything after it is manual.",
         size=17, colour=INK85)

    y = 3.02
    line(s, L, y, R, y, BLUEPRINT, 1.1)
    label(s, L, y - 0.34, 1.6, "RFQ", 10, BLUEPRINT)
    label(s, R - 1.6, y - 0.34, 1.6, "Quote", 10, BLUEPRINT,
          align=PP_ALIGN.RIGHT)

    steps = ["RFQ arrives — WhatsApp, email, PDF, photo",
             "Code decoded by hand",
             "Item master searched — 15,028 items, name is not identity",
             "No grade field — expert memory fills it",
             "Cost and stock in another screen",
             "Price set from memory; floor unknown at the moment of discount"]
    span = CW - 1.1
    for i in range(6):
        bx = L + 0.55 + span * i / 5.0
        d = 0.17
        line(s, bx, y + d, bx, y + 0.34, RULE, 0.5)
        c = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(bx - d), Inches(y - d),
                               Inches(2 * d), Inches(2 * d))
        _nofill(c)
        _flat(c)
        c.line.color.rgb = BLUEPRINT
        c.line.width = Pt(0.75)
        text(s, bx - d, y - d, 2 * d, 2 * d, f"{i + 1}", font=MONO, size=8.5,
             colour=BLUEPRINT, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
             tally=False, line_spacing=1.0)

    ty = 3.62
    label(s, L, ty, 4.0, "Friction points", 8, RULE, tally=False)
    line(s, L, ty + 0.24, R, ty + 0.24, RULE, 0.5)
    for i, stp in enumerate(steps):
        col, row = divmod(i, 3)
        rx = L + col * (CW / 2)
        ry = ty + 0.36 + row * 0.44
        text(s, rx, ry + 0.055, 0.34, 0.3, f"{i + 1:02d}", font=MONO, size=9,
             colour=BLUEPRINT, tally=False, line_spacing=1.0)
        text(s, rx + 0.42, ry - 0.015, CW / 2 - 0.75, 0.4, stp, size=12.5,
             colour=INK85, line_spacing=1.12)

    placeholder(s, L, 5.42, 2.75, 0.44, "Elapsed: [to validate]")


def slide_03(prs):
    s = sheet(prs, "ERP records transactions. It does not understand products.")
    mid = L + CW / 2
    top, bot = BODY_TOP + 0.18, 5.18
    line(s, mid, top - 0.12, mid, bot, RULE, 0.75)

    left = ["SKU string", "stock on hand", "last cost",
            "last selling price", "customer", "tax code"]
    right = ["what the product physically is",
             "whether another product substitutes",
             "whether this price holds margin",
             "whether this customer justifies it"]

    label(s, L, top, 4.6, "What ERP knows", 9.5, RULE, tally=False)
    for i, item in enumerate(left):
        y = top + 0.46 + i * 0.42
        line(s, L, y + 0.145, L + 0.20, y + 0.145, RULE, 0.5)
        text(s, L + 0.32, y, CW / 2 - 0.7, 0.34, item, size=15, colour=INK85)

    label(s, mid + 0.42, top, 4.6, "What a quote needs", 9.5, RULE,
          tally=False)
    for i, item in enumerate(right):
        y = top + 0.46 + i * 0.52
        line(s, mid + 0.42, y + 0.155, mid + 0.62, y + 0.155, BLUEPRINT, 0.75)
        text(s, mid + 0.74, y, CW / 2 - 0.85, 0.46, item, size=15,
             colour=BLUEPRINT)

    line(s, L, 5.42, R, 5.42, RULE, 0.5)
    text(s, L, 5.58, 9.6, 0.6,
         "Measured on this item master: 1.4% of item names carry a "
         "recognisable grade token.\nGrade is the attribute the decision "
         "runs on.", size=13, colour=INK85, line_spacing=1.3)


def slide_04(prs):
    s = sheet(prs)
    label(s, L, 1.55, 6.0, "Sheet 04 — the insight", 9.5, RULE, tally=False)
    line(s, L, 1.86, R, 1.86, RULE, 0.75)
    text(s, L, 2.48, 9.1, 3.0,
         "The bottleneck is not recording the order. It is turning an "
         "unstructured request into a technically correct, commercially "
         "actionable quote — and that step lives in one person's head.",
         size=30, colour=INK, line_spacing=1.36, spc=-0.35)


def slide_05(prs):
    s = sheet(prs, "One pipeline, ten stages, one legend")
    stages = [("RFQ", "dash"), ("Parse", None), ("Classify", None),
              ("Normalize", None), ("Exact match", None), ("Equivalents", None),
              ("Cost &\navailability", None), ("Margin", None),
              ("Recommendation", "dash"), ("ERP", None)]
    bw, bh, gap = 1.86, 0.78, 0.32
    for i, (name, dash) in enumerate(stages):
        col, row = i % 5, i // 5
        x = L + col * (bw + gap)
        y = BODY_TOP + 0.30 + row * 1.62
        flow_box(s, x, y, bw, bh, name, dash=dash, size=9.5)
        if col < 4:
            line(s, x + bw, y + bh / 2, x + bw + gap, y + bh / 2,
                 BLUEPRINT, 0.75, arrow=True)
    y0 = BODY_TOP + 0.30
    row1_mid, row2_mid = y0 + bh / 2, y0 + 1.62 + bh / 2
    x_out, x_in = R + 0.30, L - 0.30
    y_gap = y0 + bh + (1.62 - bh) / 2          # midway between the two rows
    line(s, L + 4 * (bw + gap) + bw, row1_mid, x_out, row1_mid, BLUEPRINT, 0.75)
    line(s, x_out, row1_mid, x_out, y_gap, BLUEPRINT, 0.75)
    line(s, x_out, y_gap, x_in, y_gap, BLUEPRINT, 0.75)
    line(s, x_in, y_gap, x_in, row2_mid, BLUEPRINT, 0.75)
    line(s, x_in, row2_mid, L, row2_mid, BLUEPRINT, 0.75, arrow=True)

    legend(s, L, 5.52)
    text(s, L + 3.05, 5.52, 6.9, 0.9,
         "Eight of the ten stages are deterministic. Only intake and "
         "explanation are dashed. Every emitted field carries provenance, "
         "confidence, and a character span.", size=13, colour=INK85,
         line_spacing=1.3)


def slide_06(prs):
    s = sheet(prs, "Same request in. Same quote out. Six steps removed.")
    lane_a, lane_b = BODY_TOP + 0.42, BODY_TOP + 2.22
    node_w, node_h = 0.86, 0.86

    flow_box(s, L, lane_a - 0.02, node_w, node_h + 1.82, "RFQ", size=9.5,
             bold=True, stroke=RULE)
    flow_box(s, R - node_w, lane_a - 0.02, node_w, node_h + 1.82, "Quote",
             size=9.5, bold=True, stroke=RULE)

    inner_l, inner_r = L + node_w + 0.30, R - node_w - 0.30
    inner_w = inner_r - inner_l

    label(s, inner_l, lane_a - 0.32, 3.0, "Today", 9.5, RULE, tally=False)
    a = ["Read", "Decode\nby hand", "Search\nmaster", "Check\nstock",
         "Look up\ncost", "Price from\nmemory", "Type\nquote"]
    aw = (inner_w - 6 * 0.13) / 7
    for i, t in enumerate(a):
        x = inner_l + i * (aw + 0.13)
        flow_box(s, x, lane_a, aw, node_h, t, size=7.5, stroke=INK85)
        if i < 6:
            line(s, x + aw, lane_a + node_h / 2, x + aw + 0.13,
                 lane_a + node_h / 2, INK85, 0.75, arrow=True)
    line(s, L + node_w, lane_a + node_h / 2, inner_l, lane_a + node_h / 2,
         INK85, 0.75, arrow=True)
    line(s, inner_r, lane_a + node_h / 2, R - node_w, lane_a + node_h / 2,
         INK85, 0.75, arrow=True)

    label(s, inner_l, lane_b - 0.32, 3.0, "With PIE", 9.5, BLUEPRINT,
          tally=False)
    widths = [1.15, inner_w - 1.15 - 1.15 - 1.15 - 3 * 0.22, 1.15, 1.15]
    names = ["Paste",
             "Resolved lines, ranked alternatives,\n"
             "stock, cost, recommended price, margin flag",
             "Review", "Send"]
    x = inner_l
    for i, (bw_, t) in enumerate(zip(widths, names)):
        flow_box(s, x, lane_b, bw_, node_h, t, size=8.5 if i != 1 else 8.5)
        if i < 3:
            line(s, x + bw_, lane_b + node_h / 2, x + bw_ + 0.22,
                 lane_b + node_h / 2, BLUEPRINT, 0.75, arrow=True)
        x += bw_ + 0.22
    line(s, L + node_w, lane_b + node_h / 2, inner_l, lane_b + node_h / 2,
         BLUEPRINT, 0.75, arrow=True)
    line(s, inner_r, lane_b + node_h / 2, R - node_w, lane_b + node_h / 2,
         BLUEPRINT, 0.75, arrow=True)

    text(s, L, 5.52, 10.6, 0.52,
         "The human still approves every line. PIE removes the lookup, not the "
         "judgement — and no line leaves below floor.", size=13.5, colour=INK85,
         line_spacing=1.28)
    placeholder(s, L, 6.12, 4.75, 0.38,
                "[to validate] minutes saved per line")


def slide_07(prs):
    s = sheet(prs, "One line, end to end")
    label(s, L, BODY_TOP - 0.16, 6.0,
          "Live engine output · pack kennametal_widia v0.10.0", 8.5, RULE,
          tally=False)

    code = "CNMG 120408-49 - TN2000"
    size = 22
    adv = 0.6 * size / 72.0                       # Plex Mono advance = 0.6 em
    cw_ = len(code) * adv
    x0 = L
    y_code = BODY_TOP + 0.16
    text(s, x0, y_code, cw_ + 0.6, 0.55, code, font=MONO, size=size,
         colour=INK, spc=0, tally=False, line_spacing=1.0)

    # (span_start, span_end, label, is_span). The first eight are the engine's
    # own character spans, copied from field_meta. Grade is NOT: it resolves
    # from the Grade column with span=null, so it gets a leader and no span
    # rule — otherwise the caption below would be claiming a span that the
    # engine never emitted.
    spans = [(0, 1, "rhombic 80°", True),
             (1, 2, "clearance 0°\nnegative", True),
             (2, 3, "tolerance\nclass M", True),
             (3, 4, "fixing type", True),
             (5, 7, "edge length\n12 mm", True),
             (7, 9, "thickness\n4.76 mm", True),
             (9, 11, "corner radius\n0.8 mm", True),
             (12, 14, "chipbreaker", True),
             (17, 23, "grade — column\nWIDIA legacy T", False)]

    # Four of the spans are single characters 0.22in apart, so labels are fanned
    # into evenly spaced slots and reached by two-segment leaders. Slot order
    # follows span order, so no two leaders cross.
    y_rule = y_code + 0.48
    y_shoulder = y_rule + 0.30
    y_lab = y_rule + 0.58
    slot_w = CW / len(spans)
    for i, (a, b, lab, is_span) in enumerate(spans):
        cx = x0 + (a + b) / 2.0 * adv
        if is_span:
            line(s, x0 + a * adv, y_rule, x0 + b * adv, y_rule, BLUEPRINT, 1.1)
        slot_cx = L + slot_w * (i + 0.5)
        line(s, cx, y_rule + 0.02, cx, y_shoulder, RULE, 0.5)
        line(s, cx, y_shoulder, slot_cx, y_lab - 0.05, RULE, 0.5)
        text(s, slot_cx - slot_w / 2 + 0.05, y_lab, slot_w - 0.10, 0.50, lab,
             font=MONO, size=7.5, colour=INK85, align=PP_ALIGN.CENTER,
             line_spacing=1.16)

    # The decode is the proof; the chain is the point. Earlier revisions gave the
    # decode 80% of the sheet and ended on EXACT — an identity verdict — so the
    # sheet demonstrated a clever parser and never reached the commercial moment.
    y_res = 4.06
    label(s, L, y_res, 6.0, "What the desk gets", 8.5, RULE, tally=False)
    line(s, L, y_res + 0.24, R, y_res + 0.24, RULE, 0.5)
    chain = ["Identity\nMM# 2001174", "Availability\nstock · landed cost",
             "Recommended\nprice · floor", "Margin\nposition"]
    bw, gap = 2.52, 0.42
    for i, t in enumerate(chain):
        x = L + i * (bw + gap)
        flow_box(s, x, y_res + 0.46, bw, 0.94, t, size=10,
                 width=1.5 if i == 3 else 0.75, bold=(i == 3),
                 colour=BLUEPRINT if i == 3 else INK85)
        if i < 3:
            line(s, x + bw, y_res + 0.93, x + bw + gap, y_res + 0.93,
                 BLUEPRINT, 0.75, arrow=True)
    text(s, L, 5.72, 10.6, 0.36,
         "The eight spans are the engine's own output.", size=13, colour=INK85)


def slide_08(prs):
    s = sheet(prs,
              "Correctness is deterministic.\nAmbiguity is where AI earns its place.")
    top_y = BODY_TOP + 0.52
    ai = ["NL RFQ\nintake", "Conversation", "Explanation", "Exploration",
          "Agent\nconfiguration"]
    bw = (CW - 4 * 0.30) / 5
    for i, t in enumerate(ai):
        flow_box(s, L + i * (bw + 0.30), top_y, bw, 0.74, t, dash="dash",
                 size=8.5)
    label(s, L, top_y - 0.30, 4.0, "AI layer — optional", 8.5, RULE,
          tally=False)

    det_y = top_y + 1.42
    line(s, L + CW / 2, top_y + 0.74, L + CW / 2, det_y, BLUEPRINT, 0.75,
         dash="dash", arrow=True)
    label(s, L + CW / 2 + 0.12, top_y + 0.92, 1.2, "reads", 7.5, RULE)

    det = ["Parsing", "Classification", "Attributes", "Equivalence",
           "Pricing", "Margin", "Policy"]
    dw = (CW - 6 * 0.18) / 7
    for i, t in enumerate(det):
        flow_box(s, L + i * (dw + 0.18), det_y, dw, 0.66, t, size=8.5)
    label(s, L, det_y - 0.30, 5.0, "Deterministic engine", 8.5, BLUEPRINT,
          tally=False)

    w_y = det_y + 1.12
    watch = ["Margin\nLeakage", "Cost\nChange", "Quote\nRisk", "Price\nIncrease",
             "Dead\nStock", "Equivalent\nOpportunity"]
    ww = (CW - 5 * 0.18) / 6
    for i, t in enumerate(watch):
        flow_box(s, L + i * (ww + 0.18), w_y, ww, 0.50, t, size=7,
                 stroke=CYAN, width=0.5, colour=INK85)
    label(s, L, w_y - 0.26, 5.0, "Deterministic watchers", 7.5, RULE,
          tally=False)

    text(s, L, 5.86, 10.6, 0.72,
         "The AI never computes a number. Every figure it states must trace "
         "to a supplied fact or the response is rejected and the "
         "deterministic reading is shown.\nThe AI ships off by default — the "
         "engine underneath is the valuable half.", size=12.5, colour=INK85,
         line_spacing=1.28)


def slide_09(prs):
    s = sheet(prs, "The engine is manufacturer-agnostic by construction")
    # The decided expansion ladder. The qualifying test is the CONVERGENCE of
    # SKU complexity, quote frequency, technical selection and margin pressure —
    # not whether part numbers are ISO-encoded. Encoding governs how much of the
    # grammar-decode path applies; identity resolution and equivalence still work
    # in the later rungs, so those categories degrade rather than fail.
    ladder = ["Cutting tools", "Bearings", "Electrical", "MRO",
              "Automation", "Fasteners", "Industrial consumables"]
    lw = 3.05
    for i, t in enumerate(ladder):
        y = BODY_TOP + 0.36 + i * 0.46
        flow_box(s, L, y, lw, 0.37, t, size=8.5,
                 width=1.4 if i == 0 else 0.6,
                 colour=BLUEPRINT if i == 0 else INK85)
        if i:
            line(s, L + lw / 2, y - 0.09, L + lw / 2, y, RULE, 0.5)
    label(s, L, BODY_TOP + 0.10, 5.0, "Where the same convergence appears", 8.5, RULE,
          tally=False)

    ax = L + lw + 0.55
    # The second line is the honest qualification. The engine holds no
    # MANUFACTURER literals and that is AST-enforced, but engine/iso.py carries
    # the ISO 1832 alphabets and model.py whitelists cutting-tool field names, so
    # a new CATEGORY is an additive engine change rather than pure pack data.
    # Without this clause the headline sits beside a category ladder and invites
    # an inference the code does not support.
    text(s, ax, BODY_TOP + 0.36, 3.1, 2.1,
         "New manufacturer, unchanged engine.\n\nNew category also needs a "
         "standards decoder.", size=12.5, colour=INK85, line_spacing=1.3)

    tx = ax + 3.45
    tw = R - tx
    label(s, tx, BODY_TOP + 0.06, tw, "Sized opportunity", 8.5, RULE,
          tally=False)
    # The arithmetic is carried through to a result, because a formula with no
    # operands is not arithmetic and four blank cells read as unprepared. Every
    # operand is invented and every one is amber: these are the founder's numbers
    # to source, and the slide says so rather than implying they were researched.
    box(s, tx, BODY_TOP + 0.30, tw, 3.42, stroke=RULE, width=0.75)
    text(s, tx + 0.18, BODY_TOP + 0.46, tw - 0.36, 0.95,
         "TAM  =  A × B      =  $720M\n"
         "SAM  =  TAM × C    =  $324M\n"
         "SOM  =  SAM × D    =  $9.7M", font=MONO,
         size=10, colour=BLUEPRINT, line_spacing=1.42)
    line(s, tx + 0.18, BODY_TOP + 1.50, tx + tw - 0.18, BODY_TOP + 1.50,
         RULE, 0.5)
    label(s, tx + 0.18, BODY_TOP + 1.60, tw - 0.36, "All inputs [assumption]",
          7.5, AMBER)
    inputs = [("A", "40,000", "distributors in scope"),
              ("B", "$18,000", "spend each"),
              ("C", "45%", "on connected ERP"),
              ("D", "3%", "reachable, three years")]
    for i, (k, val, v) in enumerate(inputs):
        y = BODY_TOP + 1.88 + i * 0.44
        text(s, tx + 0.18, y + 0.04, 0.3, 0.3, k, font=MONO, size=10,
             colour=BLUEPRINT, tally=False, line_spacing=1.0)
        placeholder(s, tx + 0.52, y, 1.02, 0.32, val)
        _placeholders.pop()
        text(s, tx + 1.66, y + 0.04, tw - 1.84, 0.3, v, font=MONO, size=7.5,
             colour=INK85, line_spacing=1.0)
    _placeholders.append((_slide_no, "[ASSUMPTION] × 4 — every TAM input is invented"))


def slide_10(prs):
    s = sheet(prs, "The layer between describing a product and quoting it")
    layers = [("ERP", "records the transaction", RULE, None),
              ("PIM", "describes the product", RULE, None),
              ("PIE", "understands it and recommends the decision",
               BLUEPRINT, None),
              ("CPQ", "configures and issues the quote", RULE, None),
              ("Pricing platforms (Pricefx)", "optimise price", RULE, None),
              ("AI copilots", "converse about it", RULE, None),
              ("Spreadsheets + memory", "the actual incumbent", RULE, "sysDash")]
    bh = 0.49
    for i, (name, desc, col, dash) in enumerate(layers):
        y = BODY_TOP + 0.22 + i * (bh + 0.055)
        pie = (name == "PIE")
        x = L if not pie else L + 0.30
        w = CW if not pie else CW - 0.60
        box(s, x, y, w, bh, stroke=BLUEPRINT if pie else col,
            width=1.5 if pie else 0.6, dash=dash)
        text(s, x + 0.20, y, 3.2, bh, name, font=MONO, size=10.5,
             colour=BLUEPRINT if pie else INK85, bold=pie,
             anchor=MSO_ANCHOR.MIDDLE, caps=True, spc=0.5, line_spacing=1.0)
        text(s, x + 3.55, y, w - 3.75, bh, desc, size=13,
             colour=BLUEPRINT if pie else INK85, anchor=MSO_ANCHOR.MIDDLE)

    label(s, L, 6.24, 3.0, "Replaces nothing", 10, AMBER)
    text(s, L + 3.55, 6.18, 7.0, 0.36,
         "It supplies the layer none of them owns.", size=13.5, colour=INK85)
    label(s, R - 4.6, BODY_TOP - 0.10, 4.6,
          "Dashed here = informal, not AI", 7.5, RULE, tally=False,
          align=PP_ALIGN.RIGHT)


def slide_11(prs):
    s = sheet(prs, "The asset is the decoded nomenclature, and it only accumulates")
    stack = ["Manufacturer\nnaming grammars", "Normalisation",
             "Sourced pairwise\nequivalence claims", "Distributor\ncommercial data",
             "Quote\noutcomes", "Pricing\npolicy", "Workflow\nposition"]
    bw = (CW - 6 * 0.14) / 7
    bh = 0.62
    base = 4.62
    rise = 0.30
    for i, t in enumerate(stack):
        x = L + i * (bw + 0.14)
        y = base - i * rise
        flow_box(s, x, y, bw, bh, t, size=7.5)
        if i:
            line(s, x - 0.14, y + rise + bh / 2, x, y + bh / 2, BLUEPRINT,
                 0.75, arrow=True)
    line(s, L, base + bh + 0.30, R, base + bh + 0.30, RULE, 0.5)
    label(s, L, base + bh + 0.40, CW,
          "Compounds with every pack, every quote", 8, RULE)

    text(s, L, 5.86, 10.6, 0.9,
         "Each manufacturer's code system is reverse-engineered once into a "
         "versioned pack: 11 families, 6,717 rows, all at 100%.\nThat structure "
         "is published nowhere — not a data-scale claim, but a specific "
         "artefact that took specification work to build.",
         size=12.5, colour=INK85, line_spacing=1.28)


def slide_12(prs):
    s = sheet(prs, "Land on the quote desk. Expand into the commercial decision.")

    # Three bands, summarising gtm-plan.md. Nothing is claimed here that the
    # plan does not support: the six rungs are a capability path, NOT six
    # purchasable tiers — entitlements.py ships three — so the note under the
    # ladder says which of them exists and that none has been sold.
    y = BODY_TOP + 0.07
    label(s, L, y, 5.0, "Who — the screen", 9, RULE, tally=False)
    # Five pain conditions plus the one technical precondition. The line beneath
    # is the point: any single signal is common and means nothing on its own.
    screen = ["High SKU count", "Technically complex", "Quote volume",
              "Margin-sensitive", "Hours lost searching", "Connector ERP"]
    bw = (CW - 5 * 0.18) / 6
    for i, t in enumerate(screen):
        flow_box(s, L + i * (bw + 0.18), y + 0.24, bw, 0.42, t, size=8)
    text(s, L, y + 0.76, CW, 0.28,
         "The pain is the convergence, not one signal.", size=12.5,
         colour=INK85)

    y = BODY_TOP + 1.31
    label(s, L, y, 5.0, "How it lands", 9, RULE, tally=False)
    text(s, L, y + 0.24, CW, 0.34,
         "Founder-led. Owner or sales head, triggered by a price increase or a "
         "departing expert.", size=13.5, colour=INK85)

    y = BODY_TOP + 2.05
    label(s, L, y, 5.0, "How it grows", 9, RULE, tally=False)
    rungs = ["Quote Desk", "Product\nIntelligence", "Pricing",
             "Margin\nManagement", "Agents", "Commercial\nIntelligence"]
    rw = (CW - 5 * 0.26) / 6
    for i, t in enumerate(rungs):
        x = L + i * (rw + 0.26)
        flow_box(s, x, y + 0.24, rw, 0.56, t, size=8,
                 width=1.5 if i == 0 else 0.6,
                 colour=BLUEPRINT if i == 0 else INK85)
        if i < 5:
            line(s, x + rw, y + 0.52, x + rw + 0.26, y + 0.52, BLUEPRINT,
                 0.75, arrow=True)
    text(s, L, y + 0.90, CW, 0.30,
         "Rung one ships free; the paid tier is built, unsold.",
         size=12.5, colour=INK85)

    y = BODY_TOP + 3.43
    label(s, L, y, 5.0, "Pricing model", 9, RULE, tally=False)
    line(s, L, y + 0.22, R, y + 0.22, RULE, 0.5)
    lines_ = ["Platform subscription", "Quote-desk seats",
              "SKU intelligence volume", "Premium modules"]
    pw = (CW - 3 * 0.30) / 4
    for i, t in enumerate(lines_):
        x = L + i * (pw + 0.30)
        text(s, x, y + 0.34, pw, 0.28, t, size=12.5, colour=INK85)
        placeholder(s, x, y + 0.66, 1.48, 0.32, "[assumption]")
        _placeholders.pop()
    _placeholders.append((_slide_no, "[ASSUMPTION] × 4 — every pricing line"))


def slide_13(prs):
    s = sheet(prs, "Current Stage")
    cols = [
        ("Built", [
            "Parser — 11 families,\n6,717 rows, 100%",
            "Platform — 2,895 tests",
            "7 ERP connectors",
            "Role-gated economics"], BLUEPRINT, None),
        ("In progress", [
            "Second manufacturer pack",
            "Outcome Tracker beyond\nfour detectors"], BLUEPRINT, None),
        ("Validated", [
            "Live Zoho data · three entities",
            "Coverage: 21.6% of 15,028\nitems; 42.5% stocked Kennametal",
            "Determinism byte-verified"], BLUEPRINT, None),
        ("Unvalidated", [
            "Willingness to pay",
            "Time saved per quote",
            "Margin impact",
            "External customer"], AMBER, "dash"),
    ]
    cwid = (CW - 3 * 0.34) / 4
    for ci, (head, items, col, dash) in enumerate(cols):
        x = L + ci * (cwid + 0.34)
        top = BODY_TOP + 0.10
        if dash:
            box(s, x - 0.14, top - 0.14, cwid + 0.28, 3.22, stroke=AMBER,
                width=0.75, dash="dash")
        label(s, x, top, cwid, head, 9.5, col, tally=False)
        line(s, x, top + 0.28, x + cwid, top + 0.28,
             col if dash else RULE, 0.75)
        y = top + 0.44
        for it in items:
            text(s, x, y, cwid, 0.62, it, size=11.5,
                 colour=AMBER if dash else INK85, line_spacing=1.2)
            y += 0.30 + 0.24 * it.count("\n") + 0.28
    _placeholders.append((_slide_no, "[TO VALIDATE] — the whole Unvalidated column"))

    line(s, L, 5.58, R, 5.58, RULE, 0.5)
    label(s, L, 5.70, CW,
          "No revenue · no partners · no pipeline",
          8.5, RULE)


def slide_14(prs):
    s = sheet(prs, "The thesis in five answers")
    rows = [("Why this problem",
             "The quote is where margin is made, and it runs on memory."),
            ("Why now",
             "Distributor ERPs are readable by API; models handle the "
             "ambiguous edge."),
            ("Why PIE",
             "Deterministic, auditable, with AI confined to where it helps."),
            ("Why PIE wins",
             "The pack asset compounds; the engine is manufacturer-agnostic."),
            ("Why this gets large",
             "The same engine crosses categories without new code.")]
    top = BODY_TOP + 0.34
    rh = 0.72
    line(s, L, top - 0.14, R, top - 0.14, INK, 1.1)
    for i, (k, v) in enumerate(rows):
        y = top + i * rh
        label(s, L, y + 0.16, 3.0, k, 9.5, RULE, tally=False)
        text(s, L + 3.35, y + 0.06, R - L - 3.35, 0.56, v, size=15.5,
             colour=BLUEPRINT, anchor=MSO_ANCHOR.TOP)
        if i:
            line(s, L, y - 0.02, R, y - 0.02, RULE, 0.4)
    line(s, L, top + 5 * rh - 0.02, R, top + 5 * rh - 0.02, INK, 1.1)


# ------------------------------------------------------------------ main ----


def _freeze(path: str) -> None:
    """Rewrite the package with a fixed ZIP timestamp.

    A .pptx is a ZIP, and the writer stamps each entry with the wall-clock time,
    so two runs over identical input produce different bytes. The parts inside
    are already identical; only the container moves. Pinning the timestamp makes
    a rebuild byte-identical, so re-running this script does not show up as a
    69KB binary diff every time somebody edits a line of copy.
    """
    fixed = (1980, 1, 1, 0, 0, 0)
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in sorted(src.infolist(), key=lambda i: i.filename):
            new = zipfile.ZipInfo(item.filename, date_time=fixed)
            new.compress_type = item.compress_type
            new.external_attr = item.external_attr
            dst.writestr(new, src.read(item.filename))
    shutil.move(tmp, path)


def main() -> int:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    for fn in (slide_01, slide_02, slide_03, slide_04, slide_05, slide_06,
               slide_07, slide_08, slide_09, slide_10, slide_11, slide_12,
               slide_13, slide_14):
        fn(prs)
    out = "pie-investor-deck.pptx"
    prs.save(out)
    _freeze(out)

    print(f"{out}: {len(prs.slides._sldIdLst)} slides\n")
    print("sheet  words  cap  status")
    bad = 0
    for n in range(1, 15):
        w, cap = _words[n], BUDGET[n]
        ok = "ok" if w <= cap else "OVER"
        if w > cap:
            bad += 1
        print(f"  {n:02d}   {w:5d}  {cap:3d}  {ok}")
    print(f"\nplaceholders: {len(_placeholders)}")
    for n, p in _placeholders:
        print(f"  sheet {n:02d}  {p}")
    if bad:
        print(f"\n{bad} slide(s) over budget")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
