"""
Proposal Template Registry & PDF Renderers

Provides a centralized template registry and template-specific PDF renderers.
Supports:
- Standard Professional template (native ReportLab enterprise layout)
- Corporate Test Template ("Business Proposal Test Template" with corporate hierarchy and blue/slate styling)
- Alternative Design Template ("Business Proposal Alternative Design" with editorial serif typography, dark slate accents, KPI summary cards, modern grid tables, custom section dividers, and formal acceptance/sign-off section)
- DocxTemplatePdfRenderer (Direct python-docx parser for user-uploaded custom DOCX templates)
"""

import os
import re
import html
import logging
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_ID = "default_whitehats"

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable, Image as RLImage
    )
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    canvas = None
    colors = None
    RLImage = None


def resolve_image_path(src: str) -> Optional[str]:
    """Resolve an HTML image src attribute to an absolute local filesystem path."""
    if not src or not isinstance(src, str):
        return None

    src = src.strip().split('?')[0].split('#')[0]
    if not src:
        return None

    candidates = []

    # 1. Absolute file path
    if os.path.isabs(src) and os.path.isfile(src):
        candidates.append(src)

    # 2. Relative to current working directory or Flask root
    clean_src = src.lstrip('/\\')
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, clean_src))

    if clean_src.startswith('static/') or clean_src.startswith('static\\'):
        no_static = clean_src[7:]
        candidates.append(os.path.join(cwd, 'static', no_static))
        candidates.append(os.path.join(cwd, clean_src))
    else:
        candidates.append(os.path.join(cwd, 'static', clean_src))
        candidates.append(os.path.join(cwd, 'static', 'uploads', 'proposal_images', os.path.basename(clean_src)))

    # Try Flask app static folder if app context available
    try:
        from flask import current_app
        if current_app:
            if hasattr(current_app, 'static_folder') and current_app.static_folder:
                sub_path = clean_src[7:] if clean_src.startswith('static') else clean_src
                candidates.append(os.path.join(current_app.static_folder, sub_path))
            if hasattr(current_app, 'root_path') and current_app.root_path:
                candidates.append(os.path.join(current_app.root_path, clean_src))
    except Exception:
        pass

    for cand in candidates:
        try:
            norm_cand = os.path.normpath(cand)
            if os.path.isfile(norm_cand) and os.path.getsize(norm_cand) > 0:
                return norm_cand
        except Exception:
            continue

    return None


def create_reportlab_image(src: str, max_width: float = 480.0, max_height: float = 350.0) -> Optional[Any]:
    """Load, validate, and scale an image for ReportLab PDF document story."""
    if not REPORTLAB_AVAILABLE or RLImage is None:
        return None

    file_path = resolve_image_path(src)
    if not file_path:
        logger.warning("[PDF IMAGE RENDERER] Image file path could not be resolved for src: %s", src)
        return None

    try:
        from PIL import Image as PILImage
        with PILImage.open(file_path) as img:
            orig_w, orig_h = img.size

        if orig_w <= 0 or orig_h <= 0:
            return None

        scale = min(1.0, max_width / float(orig_w), max_height / float(orig_h))

        final_w = max(20.0, float(orig_w) * scale)
        final_h = max(20.0, float(orig_h) * scale)

        rl_img = RLImage(file_path, width=final_w, height=final_h)
        rl_img.hAlign = 'CENTER'
        return rl_img
    except Exception as exc:
        logger.warning("[PDF IMAGE RENDERER] Failed to load/scale ReportLab Image for '%s': %s", file_path, exc)
        return None


def sanitize_paragraph_html(text: str) -> str:
    """
    Sanitize HTML string for ReportLab Paragraph parser.
    Removes <img> tags and unsupported attributes (e.g. alt, style, class)
    while preserving allowed inline formatting (<b>, <i>, <u>, <font color="...">, <a href="...">).
    Escapes unescaped '<' and '&' characters to prevent XML parsing syntax errors.
    """
    if not text or not isinstance(text, str):
        return ""

    # Strip img, script, style tags completely
    text = re.sub(r'<img\s+[^>]*?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(p|div|span|section|h[1-6]|table|tbody|tr|td|th|ul|ol|li)[^>]*>', '', text, flags=re.IGNORECASE)

    # Decode HTML entities first
    text = html.unescape(text)

    # Replace tags with clean allowed ReportLab Paragraph XML tags
    def clean_tag_match(m):
        full_tag = m.group(0)
        tag_name = m.group(1).lower()
        is_closing = full_tag.startswith("</")

        if tag_name in ("b", "strong"):
            return "</b>" if is_closing else "<b>"
        elif tag_name in ("i", "em"):
            return "</i>" if is_closing else "<i>"
        elif tag_name in ("u", "ins"):
            return "</u>" if is_closing else "<u>"
        elif tag_name == "font":
            if is_closing:
                return "</font>"
            color_m = re.search(r'color=["\']([^"\']+)["\']', full_tag, re.IGNORECASE)
            if color_m:
                return f'<font color="{color_m.group(1)}">'
            return ""
        elif tag_name == "a":
            if is_closing:
                return "</a>"
            href_m = re.search(r'href=["\']([^"\']+)["\']', full_tag, re.IGNORECASE)
            if href_m:
                return f'<a href="{html.escape(href_m.group(1))}"> '
            return ""
        return ""

    cleaned = re.sub(r'</?([a-zA-Z0-9]+)[^>]*>', clean_tag_match, text)

    # Escape any remaining < or > or & so ReportLab XML parser never crashes
    res_parts = []
    i = 0
    length = len(cleaned)
    while i < length:
        ch = cleaned[i]
        if ch == '&':
            res_parts.append('&amp;')
            i += 1
        elif ch == '<':
            m = re.match(r'^</?(b|i|u|font|a)(\s+[^>]*>|>)', cleaned[i:], re.IGNORECASE)
            if m:
                tag_str = m.group(0)
                res_parts.append(tag_str)
                i += len(tag_str)
            else:
                res_parts.append('&lt;')
                i += 1
        elif ch == '>':
            res_parts.append('&gt;')
            i += 1
        else:
            res_parts.append(ch)
            i += 1

    return "".join(res_parts).strip()


def parse_html_content_to_flowables(
    raw_content: Any,
    body_style: ParagraphStyle,
    bullet_style: ParagraphStyle,
    max_width: float = 480.0,
    project_type: str = "Cybersecurity Engagement",
    th_style: Optional[ParagraphStyle] = None,
    td_style: Optional[ParagraphStyle] = None,
    td_bold_style: Optional[ParagraphStyle] = None
) -> List[Any]:
    """Parse section content (text, lists, dicts, embedded HTML & images) into ReportLab flowables."""
    flowables = []
    if raw_content is None:
        return flowables

    # Handle Commercials dictionary
    if isinstance(raw_content, dict) and ("amount" in raw_content or "description" in raw_content):
        amount_str = str(raw_content.get("amount") or "").strip()
        desc_str = str(raw_content.get("description") or "").strip()

        _th = th_style or ParagraphStyle('CommTH', parent=body_style, fontName='Helvetica-Bold', textColor=colors.white)
        _td = td_style or ParagraphStyle('CommTD', parent=body_style)
        _td_bold = td_bold_style or ParagraphStyle('CommTDBold', parent=body_style, fontName='Helvetica-Bold')

        comm_table_data = [
            [Paragraph("SERVICE / DELIVERABLE", _th), Paragraph("COMMERCIAL MODEL", _th), Paragraph("TOTAL CONTRIBUTION", _th)],
            [Paragraph(f"<b>{html.escape(project_type)}</b>", _td), Paragraph("Fixed Price Engagement", _td), Paragraph(f"<b>{html.escape(amount_str)}</b>", _td_bold)],
        ]
        comm_table = Table(comm_table_data, colWidths=[max_width * 0.44, max_width * 0.28, max_width * 0.28])
        comm_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f8fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        flowables.append(comm_table)
        flowables.append(Spacer(1, 6))
        if desc_str:
            clean_desc = sanitize_paragraph_html(desc_str)
            if clean_desc:
                flowables.append(Paragraph(clean_desc, body_style))
        return flowables

    # Handle list of items
    if isinstance(raw_content, list):
        for item in raw_content:
            clean_item = sanitize_paragraph_html(str(item))
            if clean_item:
                flowables.append(Paragraph(f"• {clean_item.lstrip('•- ')}", bullet_style))
        return flowables

    raw_str = str(raw_content or "").strip()
    if not raw_str:
        return flowables

    # Extract <img> tags and process block by block
    img_tag_pattern = re.compile(r'(<img\s+[^>]*?>)', re.IGNORECASE)
    img_src_pattern = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)

    parts = img_tag_pattern.split(raw_str)

    for part in parts:
        if not part or not part.strip():
            continue

        if part.lower().startswith("<img"):
            src_m = img_src_pattern.search(part)
            if src_m:
                img_src = src_m.group(1)
                rl_img = create_reportlab_image(img_src, max_width=max_width)
                if rl_img:
                    flowables.append(Spacer(1, 4))
                    flowables.append(rl_img)
                    flowables.append(Spacer(1, 6))
        else:
            text_block = part
            text_block = re.sub(r'<li[^>]*>', '• ', text_block, flags=re.IGNORECASE)
            text_block = re.sub(r'</li>', '\n', text_block, flags=re.IGNORECASE)
            text_block = re.sub(r'<br\s*/?>', '\n', text_block, flags=re.IGNORECASE)
            text_block = re.sub(r'</p>', '\n\n', text_block, flags=re.IGNORECASE)

            lines = text_block.split("\n")
            for line in lines:
                l_str = line.strip()
                if not l_str:
                    continue

                sanitized = sanitize_paragraph_html(l_str)
                if not sanitized:
                    continue

                if l_str.startswith("•") or l_str.startswith("- "):
                    bullet_text = sanitized.lstrip("•- ").strip()
                    if bullet_text:
                        flowables.append(Paragraph(f"• {bullet_text}", bullet_style))
                else:
                    flowables.append(Paragraph(sanitized, body_style))

    return flowables


canvas_base = canvas.Canvas if REPORTLAB_AVAILABLE else object


class NumberedCanvas(canvas_base):
    """
    Standard Numbered Canvas: Two-pass canvas to dynamically compute total pages.
    Header appears on pages > 1.
    Footer appears on all pages with dynamic 'Page X of Y' numbering.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()

        # Top Header (pages > 1)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#64748b"))
            self.drawString(54, 802, "BUSINESS PROPOSAL — CONFIDENTIAL")
            self.drawRightString(541, 802, "CONFIDENTIAL")

            self.setStrokeColor(colors.HexColor("#cbd5e1"))
            self.setLineWidth(0.5)
            self.line(54, 794, 541, 794)

        # Bottom Footer (all pages)
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawString(54, 32, "CONFIDENTIAL — PREPARED FOR CLIENT REVIEW")

        page_str = f"Page {self._pageNumber} of {page_count}"
        self.setFont("Helvetica-Bold", 8)
        self.drawRightString(541, 32, page_str)

        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(54, 44, 541, 44)

        self.restoreState()


class CorporateNumberedCanvas(canvas_base):
    """Corporate Template A Canvas: Slate/blue accent styling."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()

        # Header (pages > 1)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#1e293b"))
            self.drawString(54, 802, "CORPORATE PROPOSAL — TEST TEMPLATE A")
            self.drawRightString(541, 802, "CONFIDENTIAL & PROPRIETARY")

            self.setStrokeColor(colors.HexColor("#94a3b8"))
            self.setLineWidth(0.75)
            self.line(54, 794, 541, 794)

        # Footer (all pages)
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#475569"))
        self.drawString(54, 32, "BUSINESS PROPOSAL TEST TEMPLATE — ENTERPRISE EDITION")

        page_str = f"PAGE {self._pageNumber} OF {page_count}"
        self.setFont("Helvetica-Bold", 8)
        self.drawRightString(541, 32, page_str)

        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(54, 44, 541, 44)

        self.restoreState()


class EditorialNumberedCanvas(canvas_base):
    """Editorial Template B Canvas: Dark slate / warm indigo accent styling."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()

        # Header (pages > 1)
        if self._pageNumber > 1:
            self.setFont("Times-Bold", 8.5)
            self.setFillColor(colors.HexColor("#0f172a"))
            self.drawString(54, 802, "EDITORIAL DESIGN — BUSINESS PROPOSAL")
            self.setFont("Times-Italic", 8.5)
            self.setFillColor(colors.HexColor("#4f46e5"))
            self.drawRightString(541, 802, "Alternative Visual Structure")

            self.setStrokeColor(colors.HexColor("#4f46e5"))
            self.setLineWidth(1)
            self.line(54, 794, 541, 794)

        # Footer (all pages)
        self.setFont("Times-Roman", 8.5)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawString(54, 32, "EDITORIAL EDITION • CONFIDENTIAL PROPOSAL")

        page_str = f"Page {self._pageNumber} of {page_count}"
        self.setFont("Times-Bold", 8.5)
        self.drawRightString(541, 32, page_str)

        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.75)
        self.line(54, 44, 541, 44)

        self.restoreState()


class BaseTemplatePdfRenderer:
    """Base interface for Proposal PDF Renderers."""
    template_id: str = DEFAULT_TEMPLATE_ID
    name: str = "Base Template"
    description: str = "Base PDF Template Renderer"

    def render_pdf(self, context: Dict[str, Any]) -> bytes:
        raise NotImplementedError("render_pdf must be implemented by subclasses")


class StandardProfessionalPdfRenderer(BaseTemplatePdfRenderer):
    """
    Standard Professional Template PDF Renderer.
    Implements a clean, modern enterprise proposal visual design:
    - Enterprise header banner & brand title
    - Document title & client subtitle card
    - 2-column key-value metadata summary box table
    - Primary navy section headers (#1e3a8a) with keepWithNext=True
    - Styled Commercials pricing summary table with dark headers, alternating rows, & borders
    - Native HTML/Rich-Text flowable parser (<p>, <b>, <i>, <u>, <ul>, <li>, <table>)
    - Dynamic headers and footers with Page X of Y numbering
    """

    template_id = "default_whitehats"
    name = "Standard Professional"
    description = "Clean, modern enterprise proposal layout with professional hierarchy, headers, footers, and pagination."

    def render_pdf(self, context: Dict[str, Any]) -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54,
        )

        styles = getSampleStyleSheet()

        brand_style = ParagraphStyle(
            'StandardBrandHeader',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=16,
            leading=20,
            textColor=colors.HexColor('#0f172a'),
            spaceAfter=2,
        )
        brand_sub_style = ParagraphStyle(
            'StandardBrandSub',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#64748b'),
            spaceAfter=12,
        )
        title_style = ParagraphStyle(
            'StandardProposalTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=20,
            leading=24,
            textColor=colors.HexColor('#0f172a'),
            spaceAfter=4,
        )
        subtitle_style = ParagraphStyle(
            'StandardProposalSubtitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=11,
            leading=14,
            textColor=colors.HexColor('#2563eb'),
            spaceAfter=12,
        )
        h1_style = ParagraphStyle(
            'StandardH1',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor('#1e3a8a'),
            spaceBefore=16,
            spaceAfter=6,
            keepWithNext=True,
        )
        body_style = ParagraphStyle(
            'StandardBody',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor('#334155'),
            spaceAfter=6,
        )
        bullet_style = ParagraphStyle(
            'StandardBullet',
            parent=body_style,
            leftIndent=14,
            spaceAfter=4,
        )
        meta_label = ParagraphStyle(
            'StandardMetaLabel',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#64748b'),
        )
        meta_val = ParagraphStyle(
            'StandardMetaVal',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=11,
            textColor=colors.HexColor('#0f172a'),
        )
        th_style = ParagraphStyle(
            'StandardTableHeader',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#ffffff'),
            alignment=0,
        )
        td_style = ParagraphStyle(
            'StandardTableCell',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#0f172a'),
            alignment=0,
        )
        td_bold_style = ParagraphStyle(
            'StandardTableCellBold',
            parent=td_style,
            fontName='Helvetica-Bold',
        )

        story = []

        # Top Decorative Brand Accent Line
        story.append(HRFlowable(width="100%", thickness=4, color=colors.HexColor('#2563eb'), spaceBefore=0, spaceAfter=8))
        vendor_heading = str(context.get("vendor_name") or context.get("company_name") or "BUSINESS PROPOSAL").upper()
        story.append(Paragraph(vendor_heading, brand_style))
        story.append(Paragraph("PROFESSIONAL SERVICES & SOLUTIONS", brand_sub_style))

        # Title & Subtitle
        title_text = str(context.get("title") or "Business Proposal").upper()
        client_name = str(context.get("client") or "Client Organization")
        story.append(Paragraph(title_text, title_style))
        story.append(Paragraph(f"PREPARED FOR {client_name.upper()}", subtitle_style))

        # Metadata Table
        project_type = str(context.get("project_type") or "Project Proposal")
        prepared_by = str(context.get("prepared_by") or "Proposal Team")
        gen_date = str(context.get("generated_at") or "")
        tmpl_info = context.get("template") or {}
        tmpl_display_name = tmpl_info.get("name") or self.name

        meta_data = [
            [
                Paragraph("Client Name:", meta_label), Paragraph(client_name, meta_val),
                Paragraph("Prepared By:", meta_label), Paragraph(prepared_by, meta_val)
            ],
            [
                Paragraph("Project Scope:", meta_label), Paragraph(project_type, meta_val),
                Paragraph("Generated Date:", meta_label), Paragraph(gen_date, meta_val)
            ],
            [
                Paragraph("Document Template:", meta_label), Paragraph(tmpl_display_name, meta_val),
                Paragraph("Proposal Status:", meta_label), Paragraph("Final Proposal", meta_val)
            ],
        ]
        meta_table = Table(meta_data, colWidths=[95, 148, 95, 149])
        meta_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#f1f5f9')),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2563eb'), spaceBefore=0, spaceAfter=14))

        sections = context.get("sections") or []
        for idx, sec in enumerate(sections, start=1):
            sec_title = str(sec.get("title") or f"SECTION {idx}").upper()
            story.append(Paragraph(f"{idx}. {sec_title}", h1_style))
            sec_flowables = parse_html_content_to_flowables(
                sec.get("content") or "",
                body_style,
                bullet_style,
                max_width=487.0,
                project_type=project_type,
                th_style=th_style,
                td_style=td_style,
                td_bold_style=td_bold_style
            )
            for fl in sec_flowables:
                story.append(fl)
            story.append(Spacer(1, 4))

        doc.build(story, canvasmaker=NumberedCanvas)
        return buffer.getvalue()


class CorporateTestTemplatePdfRenderer(BaseTemplatePdfRenderer):
    """
    Template A Renderer: 'Business Proposal Test Template'
    Corporate layout with slate navy accents (#0f172a), corporate header banner,
    clean metadata summary box, and formal section hierarchy.
    """
    template_id = "business_proposal_test_template"
    name = "Business Proposal Test Template"
    description = "Corporate proposal template with blue/slate branding, corporate header banner, and structured section layout."

    def render_pdf(self, context: Dict[str, Any]) -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54,
        )

        styles = getSampleStyleSheet()

        corp_header_style = ParagraphStyle(
            'CorpBanner',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=15,
            leading=18,
            textColor=colors.HexColor('#0f172a'),
            spaceAfter=2,
        )
        corp_sub_style = ParagraphStyle(
            'CorpSubBanner',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#475569'),
            spaceAfter=12,
        )
        title_style = ParagraphStyle(
            'CorpTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=19,
            leading=23,
            textColor=colors.HexColor('#1e293b'),
            spaceAfter=4,
        )
        subtitle_style = ParagraphStyle(
            'CorpSubtitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor('#0284c7'),
            spaceAfter=12,
        )
        h1_style = ParagraphStyle(
            'CorpH1',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=11,
            leading=14,
            textColor=colors.HexColor('#0f172a'),
            spaceBefore=14,
            spaceAfter=6,
            keepWithNext=True,
        )
        body_style = ParagraphStyle(
            'CorpBody',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor('#334155'),
            spaceAfter=6,
        )
        bullet_style = ParagraphStyle('CorpBullet', parent=body_style, leftIndent=14, spaceAfter=4)
        meta_label = ParagraphStyle('CorpMetaLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#475569'))
        meta_val = ParagraphStyle('CorpMetaVal', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#0f172a'))

        story = []

        # Corporate Accent Bar & Header Banner
        story.append(HRFlowable(width="100%", thickness=5, color=colors.HexColor('#0284c7'), spaceBefore=0, spaceAfter=10))
        story.append(Paragraph("CORPORATE PROPOSAL — TEMPLATE A", corp_header_style))
        story.append(Paragraph("ENTERPRISE SOLUTION & TECHNICAL PROPOSAL", corp_sub_style))

        # Title & Subtitle
        title_text = str(context.get("title") or "Business Proposal").upper()
        client_name = str(context.get("client") or "Client Organization")
        story.append(Paragraph(title_text, title_style))
        story.append(Paragraph(f"PREPARED FOR {client_name.upper()}", subtitle_style))

        # Metadata Card
        project_type = str(context.get("project_type") or "Cybersecurity Assessment")
        prepared_by = str(context.get("prepared_by") or "AgentScan")
        gen_date = str(context.get("generated_at") or "")

        meta_data = [
            [Paragraph("Client Name:", meta_label), Paragraph(client_name, meta_val), Paragraph("Prepared By:", meta_label), Paragraph(prepared_by, meta_val)],
            [Paragraph("Project Scope:", meta_label), Paragraph(project_type, meta_val), Paragraph("Generated Date:", meta_label), Paragraph(gen_date, meta_val)],
            [Paragraph("Template:", meta_label), Paragraph(self.name, meta_val), Paragraph("Proposal Status:", meta_label), Paragraph("Final Proposal", meta_val)],
        ]
        meta_table = Table(meta_data, colWidths=[95, 148, 95, 149])
        meta_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f1f5f9')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#0284c7'), spaceBefore=0, spaceAfter=14))

        sections = context.get("sections") or []
        for idx, sec in enumerate(sections, start=1):
            sec_title = str(sec.get("title") or f"SECTION {idx}").upper()
            story.append(Paragraph(f"{idx}. {sec_title}", h1_style))
            sec_flowables = parse_html_content_to_flowables(
                sec.get("content") or "",
                body_style,
                bullet_style,
                max_width=487.0,
                project_type=project_type
            )
            for fl in sec_flowables:
                story.append(fl)
            story.append(Spacer(1, 4))

        doc.build(story, canvasmaker=CorporateNumberedCanvas)
        return buffer.getvalue()


class AlternativeDesignPdfRenderer(BaseTemplatePdfRenderer):
    """
    Template B Renderer: 'Business Proposal Alternative Design'
    Editorial modern layout featuring:
    - Dark slate header banner (#0f172a) with white title text & indigo brand accent (#4f46e5)
    - Editorial serif typography (Times-Bold / Times-Roman) for headings & titles
    - KPI Stat Callout Block (3-column summary cards for Investment, Timeline, Client)
    - Modern section dividers & table formatting
    - Dynamic editorial running headers & footers
    - Formal Acceptance & Sign-off signature block
    """
    template_id = "business_proposal_alternative_design"
    name = "Business Proposal Alternative Design"
    description = "Editorial modern proposal layout featuring dark slate accents, editorial typography, KPI summary cards, custom section hierarchy, modern grid tables, and sign-off block."

    def render_pdf(self, context: Dict[str, Any]) -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=48,
            rightMargin=48,
            topMargin=48,
            bottomMargin=48,
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'EditTitle',
            parent=styles['Normal'],
            fontName='Times-Bold',
            fontSize=22,
            leading=26,
            textColor=colors.HexColor('#0f172a'),
            spaceAfter=4,
        )
        subtitle_style = ParagraphStyle(
            'EditSubtitle',
            parent=styles['Normal'],
            fontName='Times-Italic',
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor('#4f46e5'),
            spaceAfter=14,
        )
        h1_style = ParagraphStyle(
            'EditH1',
            parent=styles['Normal'],
            fontName='Times-Bold',
            fontSize=12,
            leading=16,
            textColor=colors.HexColor('#1e1b4b'),
            spaceBefore=16,
            spaceAfter=6,
            keepWithNext=True,
        )
        body_style = ParagraphStyle(
            'EditBody',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9.5,
            leading=14.5,
            textColor=colors.HexColor('#1e293b'),
            spaceAfter=6,
        )
        bullet_style = ParagraphStyle('EditBullet', parent=body_style, leftIndent=14, spaceAfter=4)
        kpi_label = ParagraphStyle('KPILabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#64748b'), alignment=1)
        kpi_value = ParagraphStyle('KPIValue', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=colors.HexColor('#4f46e5'), alignment=1)
        kpi_sub = ParagraphStyle('KPISub', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#334155'), alignment=1)

        story = []

        # 1. Dark Slate Editorial Header Hero Card
        hero_data = [
            [Paragraph("<font color='#ffffff'><b>EDITORIAL DESIGN — BUSINESS PROPOSAL</b></font>", ParagraphStyle('HeroText', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=14, textColor=colors.white, alignment=0)),
             Paragraph("<font color='#a5b4fc'><b>ALTERNATIVE EDITION</b></font>", ParagraphStyle('HeroRight', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#a5b4fc'), alignment=2))]
        ]
        hero_table = Table(hero_data, colWidths=[330, 169])
        hero_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#0f172a')),
            ('PADDING', (0, 0), (-1, -1), 10),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(hero_table)
        story.append(Spacer(1, 12))

        # 2. Editorial Title & Subtitle
        title_text = str(context.get("title") or "Business Proposal").upper()
        client_name = str(context.get("client") or "Client Organization")
        story.append(Paragraph(title_text, title_style))
        story.append(Paragraph(f"Prepared Exclusively for {client_name}", subtitle_style))

        # 3. KPI Visual Summary Stat Highlight Card
        comm_obj = context.get("pricing") or {}
        raw_amount = (
            context.get("pricing_amount")
            or (comm_obj.get("amount") if isinstance(comm_obj, dict) else None)
            or "Contribution-Based"
        )
        project_type = str(context.get("project_type") or "Cybersecurity Engagement")

        kpi_data = [
            [
                Paragraph("TOTAL CONTRIBUTION", kpi_label),
                Paragraph("PROJECT TIMELINE", kpi_label),
                Paragraph("ENGAGEMENT TYPE", kpi_label)
            ],
            [
                Paragraph(str(raw_amount), kpi_value),
                Paragraph("4–6 Weeks", kpi_value),
                Paragraph("Enterprise Fixed Scope", kpi_value)
            ],
            [
                Paragraph("Fixed Proposal Budget", kpi_sub),
                Paragraph("Target Delivery Window", kpi_sub),
                Paragraph(project_type, kpi_sub)
            ]
        ]
        kpi_table = Table(kpi_data, colWidths=[166, 166, 167])
        kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 14))
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#4f46e5'), spaceBefore=0, spaceAfter=14))

        # 4. Editorial Sections
        sections = context.get("sections") or []
        for idx, sec in enumerate(sections, start=1):
            sec_title = str(sec.get("title") or f"SECTION {idx}").upper()
            story.append(Paragraph(f"{idx}. {sec_title}", h1_style))

            sec_flowables = parse_html_content_to_flowables(
                sec.get("content") or "",
                body_style,
                bullet_style,
                max_width=499.0,
                project_type=project_type
            )
            for fl in sec_flowables:
                story.append(fl)
            story.append(Spacer(1, 6))

        # 5. Editorial Acceptance & Sign-off Block
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#4f46e5'), spaceBefore=6, spaceAfter=12))
        story.append(Paragraph("PROPOSAL ACCEPTANCE & AUTHORIZATION", h1_style))
        story.append(Paragraph("By signing below, the authorized representatives of both organizations accept and approve this proposal in accordance with the terms herein.", body_style))
        story.append(Spacer(1, 8))

        sign_label = ParagraphStyle('SignLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#475569'))
        sign_line = ParagraphStyle('SignLine', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#94a3b8'))

        sign_data = [
            [Paragraph("PREPARED FOR CLIENT:", sign_label), Paragraph("AUTHORIZED VENDOR:", sign_label)],
            [Paragraph("________________________________________", sign_line), Paragraph("________________________________________", sign_line)],
            [Paragraph(f"Authorized Signature ({client_name})", sign_label), Paragraph(f"Authorized Signature ({str(context.get('prepared_by') or 'Vendor')})", sign_label)],
            [Paragraph("Date: ____ / ____ / 2026", sign_label), Paragraph("Date: ____ / ____ / 2026", sign_label)]
        ]
        sign_table = Table(sign_data, colWidths=[240, 259])
        sign_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(KeepTogether([sign_table]))

        doc.build(story, canvasmaker=EditorialNumberedCanvas)
        return buffer.getvalue()


class DocxTemplatePdfRenderer(BaseTemplatePdfRenderer):
    """
    Parser for user-uploaded custom DOCX templates.
    Extracts paragraphs, runs, headings, table cells, and formatting from a python-docx
    document object and translates them into ReportLab flowables, guaranteeing that
    user-uploaded custom DOCX templates render to PDF with full visual structure.
    """

    template_id = "custom_docx_template"
    name = "Custom DOCX Template"
    description = "Renders uploaded DOCX templates into ReportLab PDF matching document formatting."

    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path

    def render_pdf(self, context: Dict[str, Any]) -> bytes:
        import docx
        doc_obj = None

        if context.get("docx_bytes"):
            try:
                doc_obj = docx.Document(BytesIO(context["docx_bytes"]))
            except Exception as exc:
                logger.warning("Failed to open docx bytes: %s", exc)

        if not doc_obj and self.file_path and Path(self.file_path).exists():
            try:
                doc_obj = docx.Document(str(self.file_path))
            except Exception as exc:
                logger.warning("Failed to open docx file %s: %s", self.file_path, exc)

        if not doc_obj:
            return StandardProfessionalPdfRenderer().render_pdf(context)

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54,
        )

        styles = getSampleStyleSheet()
        story = []

        base_body = ParagraphStyle('DocxBody', parent=styles['Normal'], fontName='Helvetica', fontSize=9.5, leading=14, textColor=colors.HexColor('#0f172a'))
        base_h1 = ParagraphStyle('DocxH1', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=colors.HexColor('#0f172a'), spaceBefore=14, spaceAfter=4)
        base_h2 = ParagraphStyle('DocxH2', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, leading=15, textColor=colors.HexColor('#334155'), spaceBefore=10, spaceAfter=3)
        base_bullet = ParagraphStyle('DocxBullet', parent=base_body, leftIndent=14, spaceAfter=4)

        def _get_paragraph_html(p):
            if not p.runs:
                return html.escape(p.text)
            parts = []
            for r in p.runs:
                txt = html.escape(r.text)
                if not txt:
                    continue
                color_hex = None
                if r.font and r.font.color and r.font.color.rgb:
                    color_hex = f"#{r.font.color.rgb}"
                if color_hex:
                    txt = f'<font color="{color_hex}">{txt}</font>'
                if r.bold:
                    txt = f'<b>{txt}</b>'
                if r.italic:
                    txt = f'<i>{txt}</i>'
                if r.underline:
                    txt = f'<u>{txt}</u>'
                parts.append(txt)
            return "".join(parts) if parts else html.escape(p.text)

        def _get_cell_bg(cell):
            try:
                tcPr = cell._tc.get_or_add_tcPr()
                shd = tcPr.find(docx.oxml.ns.qn('w:shd'))
                if shd is not None:
                    val = shd.get(docx.oxml.ns.qn('w:fill'))
                    if val and val.lower() != 'auto':
                        return colors.HexColor(f"#{val}")
            except Exception:
                pass
            return None

        # Build story from paragraphs and tables in order of document layout
        for elem in doc_obj.element.body:
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "p":
                # Find matching paragraph object
                matching_p = None
                for p in doc_obj.paragraphs:
                    if p._element is elem:
                        matching_p = p
                        break
                if matching_p:
                    text = matching_p.text.strip()
                    if not text:
                        story.append(Spacer(1, 4))
                        continue

                    style_name = matching_p.style.name.lower() if matching_p.style else ""
                    html_content = _get_paragraph_html(matching_p)

                    if "heading 1" in style_name or "title" in style_name:
                        story.append(Paragraph(html_content, base_h1))
                    elif "heading 2" in style_name or "heading 3" in style_name:
                        story.append(Paragraph(html_content, base_h2))
                    elif text.startswith("•") or text.startswith("-"):
                        story.append(Paragraph(html_content, base_bullet))
                    else:
                        story.append(Paragraph(html_content, base_body))

            elif tag == "tbl":
                matching_tbl = None
                for tbl in doc_obj.tables:
                    if tbl._element is elem:
                        matching_tbl = tbl
                        break
                if matching_tbl:
                    table_data = []
                    t_styles = [
                        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
                        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                        ('PADDING', (0, 0), (-1, -1), 6),
                    ]
                    for r_idx, row in enumerate(matching_tbl.rows):
                        row_cells = []
                        for c_idx, cell in enumerate(row.cells):
                            c_p_htmls = [_get_paragraph_html(p) for p in cell.paragraphs if p.text.strip()]
                            c_html = "<br/>".join(c_p_htmls) if c_p_htmls else html.escape(cell.text.strip())
                            row_cells.append(Paragraph(c_html, base_body))
                            bg_color = _get_cell_bg(cell)
                            if bg_color:
                                t_styles.append(('BACKGROUND', (c_idx, r_idx), (c_idx, r_idx), bg_color))
                        if row_cells:
                            table_data.append(row_cells)

                    if table_data:
                        num_cols = max(len(r) for r in table_data)
                        col_w = int(484 / num_cols) if num_cols > 0 else 484
                        rl_table = Table(table_data, colWidths=[col_w] * num_cols)
                        rl_table.setStyle(TableStyle(t_styles))
                        story.append(Spacer(1, 6))
                        story.append(rl_table)
                        story.append(Spacer(1, 6))

        doc.build(story, canvasmaker=NumberedCanvas)
        return buffer.getvalue()


class ProposalTemplateRegistry:
    """
    Centralized Proposal Template Registry.
    Registers and resolves template PDF renderers by template ID.
    Supports canonical IDs (default_whitehats, business_proposal_test_template, business_proposal_alternative_design)
    and custom user uploaded templates.
    """
    def __init__(self):
        self._renderers: Dict[str, BaseTemplatePdfRenderer] = {}
        self._default_renderer: BaseTemplatePdfRenderer = StandardProfessionalPdfRenderer()

        # 1. Register Default Standard Professional (with backwards-compatible aliases)
        self.register(StandardProfessionalPdfRenderer())
        self._renderers["whitehats-professional"] = self._default_renderer
        self._renderers["whitehats_professional"] = self._default_renderer
        self._renderers["standard_professional"] = self._default_renderer
        self._renderers["standard-professional"] = self._default_renderer
        self._renderers["default"] = self._default_renderer

        # 2. Register Built-in Corporate Test Template A
        corp_renderer = CorporateTestTemplatePdfRenderer()
        self.register(corp_renderer)
        self._renderers["test_template"] = corp_renderer
        self._renderers["test_template_corporate"] = corp_renderer

        # 3. Register Built-in Alternative Design Template B
        alt_renderer = AlternativeDesignPdfRenderer()
        self.register(alt_renderer)
        self._renderers["alternative_design"] = alt_renderer
        self._renderers["editorial_design"] = alt_renderer

    def register(self, renderer: BaseTemplatePdfRenderer):
        """Register a new template renderer."""
        if not renderer or not getattr(renderer, "template_id", None):
            raise ValueError("Renderer must define a non-empty template_id attribute.")
        self._renderers[renderer.template_id] = renderer
        logger.info("[TEMPLATE REGISTRY] Registered renderer '%s' for template_id '%s'", renderer.name, renderer.template_id)

    def get_renderer(self, template_id: str, file_path: Optional[str] = None) -> BaseTemplatePdfRenderer:
        """
        Resolve template PDF renderer by template ID or file path.
        Falls back safely to StandardProfessionalPdfRenderer for unknown/missing template IDs.
        """
        clean_id = str(template_id or "").strip().lower()
        renderer = self._renderers.get(clean_id)
        if renderer:
            return renderer

        # Check if template is custom docx
        if clean_id.startswith("custom_") or file_path:
            return DocxTemplatePdfRenderer(file_path=file_path)

        logger.info("[TEMPLATE REGISTRY] Template ID '%s' not found. Falling back to default Standard Professional renderer.", template_id)
        return self._default_renderer

    def list_templates(self, include_all: bool = False) -> List[Dict[str, Any]]:
        """List registered built-in system templates."""
        listed = []
        seen = set()
        active_system_ids = {DEFAULT_TEMPLATE_ID}
        for t_id, r in self._renderers.items():
            if (include_all or r.template_id in active_system_ids) and r.template_id not in seen:
                seen.add(r.template_id)
                listed.append({
                    "template_id": r.template_id,
                    "name": r.name,
                    "description": r.description,
                    "type": "system",
                    "is_default": (r.template_id == DEFAULT_TEMPLATE_ID),
                })
        return listed


# Backwards compatibility alias
WhitehatsProfessionalPdfRenderer = StandardProfessionalPdfRenderer

# Global singleton instance of template registry
template_registry = ProposalTemplateRegistry()
