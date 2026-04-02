"""
Terminal POS display helpers.

This module is intentionally separate so terminal rendering and PDF export
do not interfere with the core solver logic.
"""

from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path


def export_generated_pos_pdf(
    pos_sections: list[dict],
    output_dir: str | Path | None = None,
    solver_used: str | None = None,
) -> Path:
    """
    Export solver POS sections to a simple PDF file.

    Args:
        pos_sections: Solver output `result["pos"]` list.
        output_dir: Optional output folder. Defaults to Main-Solver/generated_pos_pdf.

    Returns:
        Path to the generated PDF.
    """
    base_dir = Path(__file__).resolve().parent
    target_dir = Path(output_dir) if output_dir else (base_dir / "generated_pos_pdf")
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = target_dir / f"generated_pos_terminal_{timestamp}.pdf"

    # Reuse the existing Django PDF generator logic for consistent formatting.
    django_project = base_dir.parents[1] / "Django-Back-Front"
    try:
        pos_pdf_path = django_project / "pos" / "pos_pdf.py"
        spec = importlib.util.spec_from_file_location("pos.pos_pdf", pos_pdf_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load module spec from: {pos_pdf_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        build_generated_pos_pdf = module.build_generated_pos_pdf
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Unable to import pos_pdf formatter: {e}") from e

    pdf_bytes = build_generated_pos_pdf(
        student_name="TERMINAL USER",
        sr_code="N/A",
        academic_year="A.Y. N/A",
        pos=pos_sections or [],
        eval_date=datetime.now().date(),
        solver_used=solver_used,
        logo_path=str(django_project / "static" / "img" / "BSU_Logo.png"),
        grades_by_section=None,
        boundary_section_index=None,
    )
    with output_path.open("wb") as f:
        f.write(pdf_bytes)

    return output_path

