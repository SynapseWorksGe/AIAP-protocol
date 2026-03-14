"""PDF generation service using fpdf2 with Unicode support."""

import logging
import os
from pathlib import Path

from fpdf import FPDF

from app.config import settings

logger = logging.getLogger(__name__)

FONT_DIR = Path(__file__).parent.parent.parent / "fonts"


class PDFService:
    """Generate PDF documents from meeting analysis results."""

    def __init__(self):
        self._font_path = FONT_DIR / "DejaVuSans.ttf"
        self._font_bold_path = FONT_DIR / "DejaVuSans-Bold.ttf"

    def _create_pdf(self) -> FPDF:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        if self._font_path.exists():
            pdf.add_font("DejaVu", "", str(self._font_path), uni=True)
            pdf.add_font("DejaVu", "B", str(self._font_bold_path), uni=True)
            pdf.set_font("DejaVu", size=11)
        else:
            logger.warning("DejaVu font not found at %s, PDF may not render Cyrillic", self._font_path)
            pdf.set_font("Helvetica", size=11)

        return pdf

    def generate_summary_pdf(self, summary: str, tasks: str, transcript: str, output_path: str) -> str:
        """Generate a full meeting report PDF."""
        pdf = self._create_pdf()
        font_family = "DejaVu" if self._font_path.exists() else "Helvetica"

        # Title page
        pdf.add_page()
        pdf.set_font(font_family, "B", 20)
        pdf.cell(0, 20, "Протокол встречи", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(10)

        # Summary section
        pdf.set_font(font_family, "B", 14)
        pdf.cell(0, 10, "Саммари", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font(font_family, "", 11)
        pdf.multi_cell(0, 6, summary)
        pdf.ln(8)

        # Tasks section
        pdf.add_page()
        pdf.set_font(font_family, "B", 14)
        pdf.cell(0, 10, "Список задач", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font(font_family, "", 11)
        pdf.multi_cell(0, 6, tasks)
        pdf.ln(8)

        # Transcript section
        pdf.add_page()
        pdf.set_font(font_family, "B", 14)
        pdf.cell(0, 10, "Расшифровка встречи", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font(font_family, "", 10)
        pdf.multi_cell(0, 5, transcript)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        pdf.output(output_path)
        logger.info("PDF generated: %s", output_path)
        return output_path


pdf_service = PDFService()
