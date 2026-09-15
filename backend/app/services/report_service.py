from __future__ import annotations

import csv
import io
import json
import tempfile
import threading
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import PortalContour
from app.db.models import ArtifactResult, Operation
from app.domain.bundle import OperationType
from app.utils.logging import redact_log_text

CSV_COLUMNS = (
    "operation_id",
    "delivery_id",
    "source_delivery_id",
    "operation_type",
    "operation_status",
    "actor",
    "started_at",
    "finished_at",
    "artifact_type",
    "repository",
    "name",
    "reference",
    "version",
    "source_digest",
    "target_digest",
    "artifact_result",
    "error_code",
    "error_message",
    "operation_comment",
    "bundle_sha256",
    "source_project",
    "source_repository",
    "source_reference",
    "source_version",
    "target_project",
    "target_repository",
    "target_reference",
    "destination_plan_id",
    "destination_plan_hash",
    "overwrite_approved",
)

_REPORT_SPOOL_LIMIT = 2 * 1024 * 1024
_FONT_REGULAR = "HTPDejaVuSans"
_FONT_BOLD = "HTPDejaVuSans-Bold"
_FONT_LOCK = threading.Lock()
_REGULAR_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)
_BOLD_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
)


def report_filename(operation_id: int, extension: str) -> str:
    normalized = extension.strip().lower().lstrip(".")
    if normalized not in {"csv", "pdf"}:
        raise ValueError("unsupported report extension")
    return f"operation-{operation_id}.{normalized}"


def receipt_filename(operation_id: int) -> str:
    return f"import-receipt-{operation_id}.json"


def iter_operation_csv(operation: Operation) -> Iterator[bytes]:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    yield _drain_csv_buffer(buffer)

    artifacts: list[ArtifactResult | None] = [
        *sorted(operation.artifacts, key=lambda item: item.id)
    ]
    if not artifacts:
        artifacts = [None]
    for artifact in artifacts:
        writer.writerow(_csv_row(operation, artifact))
        yield _drain_csv_buffer(buffer)


def build_operation_pdf(
    operation: Operation,
    contour: PortalContour,
) -> tempfile.SpooledTemporaryFile[bytes]:
    stream = tempfile.SpooledTemporaryFile(max_size=_REPORT_SPOOL_LIMIT, mode="w+b")
    regular_font, bold_font = _pdf_fonts()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "HTPReportTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )
    body_style = ParagraphStyle(
        "HTPReportBody",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=8.5,
        leading=11,
        wordWrap="CJK",
    )
    heading_style = ParagraphStyle(
        "HTPReportHeading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=11,
        leading=14,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    table_header_style = ParagraphStyle(
        "HTPReportTableHeader",
        parent=body_style,
        fontName=bold_font,
        fontSize=7.5,
        leading=9,
        alignment=TA_CENTER,
    )
    table_cell_style = ParagraphStyle(
        "HTPReportTableCell",
        parent=body_style,
        fontSize=7,
        leading=8.5,
    )

    document = SimpleDocTemplate(
        stream,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Harbor Transfer Portal operation {operation.id}",
        author="Harbor Transfer Portal",
    )

    story = [
        Paragraph("Harbor Transfer Portal — отчёт операции", title_style),
        _metadata_table(operation, contour, body_style, bold_font),
        Spacer(1, 3 * mm),
        Paragraph("Итоги", heading_style),
        _summary_table(operation, body_style, bold_font),
    ]

    source_rows = _source_metadata_rows(operation)
    if source_rows:
        story.extend(
            [
                Paragraph("SOURCE / bundle verification", heading_style),
                _key_value_table(source_rows, body_style, bold_font),
            ]
        )

    story.extend(
        [
            Paragraph("Артефакты", heading_style),
            _artifact_table(operation, table_header_style, table_cell_style),
        ]
    )

    if operation.error_code or operation.error_message:
        story.extend(
            [
                Paragraph("Ошибка операции", heading_style),
                _key_value_table(
                    [
                        ("Код", operation.error_code or "—"),
                        ("Сообщение", operation.error_message or "—"),
                    ],
                    body_style,
                    bold_font,
                ),
            ]
        )

    document.build(story)
    stream.seek(0)
    return stream


def _csv_row(operation: Operation, artifact: ArtifactResult | None) -> dict[str, object]:
    delivery_id = operation.delivery_id or operation.source_delivery_id or ""
    return {
        "operation_id": operation.id,
        "delivery_id": _csv_safe(delivery_id),
        "source_delivery_id": _csv_safe(operation.source_delivery_id),
        "operation_type": operation.type.value,
        "operation_status": operation.status.value,
        "actor": _csv_safe(operation.actor_username),
        "started_at": _format_datetime(operation.started_at),
        "finished_at": _format_datetime(operation.finished_at),
        "artifact_type": _csv_safe(artifact.artifact_type if artifact else None),
        "repository": _csv_safe(artifact.repository if artifact else None),
        "name": _csv_safe(artifact.name if artifact else None),
        "reference": _csv_safe(artifact.reference if artifact else None),
        "version": _csv_safe(artifact.version if artifact else None),
        "source_digest": _csv_safe(artifact.source_digest if artifact else None),
        "target_digest": _csv_safe(artifact.target_digest if artifact else None),
        "artifact_result": artifact.status.value if artifact else "",
        "error_code": _csv_safe(artifact.error_code if artifact else operation.error_code),
        "error_message": _csv_safe(artifact.error_message if artifact else operation.error_message),
        "operation_comment": _csv_safe(operation.comment),
        "bundle_sha256": _csv_safe(operation.bundle_sha256),
        "source_project": _csv_safe(artifact.source_project if artifact else None),
        "source_repository": _csv_safe(artifact.source_repository if artifact else None),
        "source_reference": _csv_safe(artifact.source_reference if artifact else None),
        "source_version": _csv_safe(artifact.source_version if artifact else None),
        "target_project": _csv_safe(artifact.target_project if artifact else None),
        "target_repository": _csv_safe(artifact.target_repository if artifact else None),
        "target_reference": _csv_safe(artifact.target_reference if artifact else None),
        "destination_plan_id": _csv_safe(artifact.destination_plan_id if artifact else None),
        "destination_plan_hash": _csv_safe(
            artifact.destination_plan_hash if artifact else None
        ),
        "overwrite_approved": (
            ""
            if artifact is None or artifact.overwrite_approved is None
            else str(artifact.overwrite_approved).lower()
        ),
    }


def _drain_csv_buffer(buffer: io.StringIO) -> bytes:
    value = buffer.getvalue().encode()
    buffer.seek(0)
    buffer.truncate(0)
    return value


def _csv_safe(value: object | None) -> str:
    text = _safe_text(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _safe_text(value: object | None) -> str:
    if value is None:
        return ""
    return redact_log_text(str(value))


def _format_datetime(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _paragraph(value: object | None, style: ParagraphStyle) -> Paragraph:
    text = _safe_text(value) or "—"
    return Paragraph(escape(text), style)


def _metadata_table(
    operation: Operation,
    contour: PortalContour,
    style: ParagraphStyle,
    bold_font: str,
) -> Table:
    delivery_id = operation.delivery_id or operation.source_delivery_id or "—"
    rows = [
        ("Контур", contour.value),
        ("Operation ID", operation.id),
        ("Delivery ID", delivery_id),
        ("Тип / статус", f"{operation.type.value} / {operation.status.value}"),
        ("Actor", operation.actor_username),
        ("Начало", _format_datetime(operation.started_at) or "—"),
        ("Завершение", _format_datetime(operation.finished_at) or "—"),
        ("Bundle SHA256", operation.bundle_sha256 or "—"),
        ("Комментарий", operation.comment or "—"),
    ]
    return _key_value_table(rows, style, bold_font)


def _summary_table(operation: Operation, style: ParagraphStyle, bold_font: str) -> Table:
    data = [
        ["Всего", "Успешно", "Пропущено", "Конфликты", "Ошибки"],
        [
            str(operation.total_artifacts),
            str(operation.successful_artifacts),
            str(operation.skipped_artifacts),
            str(operation.conflict_artifacts),
            str(operation.failed_artifacts),
        ],
    ]
    table = Table(data, colWidths=[34 * mm] * 5)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), style.fontName),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _key_value_table(
    rows: Sequence[tuple[str, object]],
    style: ParagraphStyle,
    bold_font: str,
) -> Table:
    data = [[_paragraph(key, style), _paragraph(value, style)] for key, value in rows]
    table = Table(data, colWidths=[48 * mm, 205 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), bold_font),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _source_display(artifact: ArtifactResult) -> str:
    repository = artifact.source_repository or artifact.repository
    if artifact.artifact_type == "helm-chart" and artifact.name:
        repository = f"{repository}/{artifact.name}"
    reference = artifact.source_reference or artifact.source_version
    if reference is None:
        reference = artifact.reference or artifact.version
    return f"{repository}:{reference}" if reference else repository


def _target_display(artifact: ArtifactResult) -> str:
    if artifact.target_reference:
        return artifact.target_reference
    if artifact.target_repository:
        reference = artifact.reference or artifact.version
        return (
            f"{artifact.target_repository}:{reference}"
            if reference
            else artifact.target_repository
        )
    return "—"


def _artifact_table(
    operation: Operation,
    header_style: ParagraphStyle,
    cell_style: ParagraphStyle,
) -> LongTable:
    headers = [
        "Тип",
        "SOURCE",
        "TARGET",
        "Source digest",
        "Target digest",
        "Result",
        "Plan",
        "Error",
    ]
    data: list[list[Paragraph]] = [[_paragraph(item, header_style) for item in headers]]
    for artifact in sorted(operation.artifacts, key=lambda item: item.id):
        error = " — ".join(
            part for part in (artifact.error_code, artifact.error_message) if part
        ) or "—"
        data.append(
            [
                _paragraph(artifact.artifact_type, cell_style),
                _paragraph(_source_display(artifact), cell_style),
                _paragraph(_target_display(artifact), cell_style),
                _paragraph(artifact.source_digest or "—", cell_style),
                _paragraph(artifact.target_digest or "—", cell_style),
                _paragraph(artifact.status.value, cell_style),
                _paragraph(artifact.destination_plan_id or "—", cell_style),
                _paragraph(error, cell_style),
            ]
        )
    if len(data) == 1:
        data.append(
            [
                _paragraph("—", cell_style),
                _paragraph("Нет сохранённых artifact results", cell_style),
                *[_paragraph("—", cell_style) for _ in range(6)],
            ]
        )

    table = LongTable(
        data,
        repeatRows=1,
        colWidths=[18 * mm, 46 * mm, 55 * mm, 38 * mm, 38 * mm, 22 * mm, 28 * mm, 32 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _source_metadata_rows(operation: Operation) -> list[tuple[str, object]]:
    if operation.type is not OperationType.IMPORT:
        return []
    preview: dict[str, object] = {}
    if operation.import_preview_json:
        try:
            loaded = json.loads(operation.import_preview_json)
            if isinstance(loaded, dict):
                preview = loaded
        except (TypeError, ValueError):
            preview = {}

    source_delivery = operation.source_delivery_id or preview.get("source_delivery_id") or "—"
    fingerprint = (
        operation.bundle_signing_key_fingerprint
        or preview.get("signing_key_fingerprint")
        or "—"
    )
    rows: list[tuple[str, object]] = [
        ("SOURCE delivery", source_delivery),
        ("Signing key fingerprint", fingerprint),
    ]
    for label, key in (
        ("SOURCE Harbor", "source_harbor"),
        ("SOURCE portal version", "source_portal_version"),
        ("SOURCE created at", "source_created_at"),
        ("SOURCE created by", "source_created_by"),
        ("SOURCE comment", "source_comment"),
    ):
        if preview.get(key) not in (None, ""):
            rows.append((label, preview[key]))
    for label, key in (
        ("Checksum verification", "checksum_verified"),
        ("Signature verification", "signature_verified"),
        ("Schema verification", "schema_verified"),
    ):
        if key in preview:
            rows.append((label, "verified" if preview[key] is True else "not verified"))
    return rows


def _pdf_fonts() -> tuple[str, str]:
    regular_path = next((path for path in _REGULAR_FONT_CANDIDATES if path.is_file()), None)
    bold_path = next((path for path in _BOLD_FONT_CANDIDATES if path.is_file()), None)
    if regular_path is None:
        return "Helvetica", "Helvetica-Bold"

    with _FONT_LOCK:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if _FONT_REGULAR not in registered:
            pdfmetrics.registerFont(TTFont(_FONT_REGULAR, str(regular_path)))
        if bold_path is not None and _FONT_BOLD not in registered:
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(bold_path)))

    if bold_path is not None:
        return _FONT_REGULAR, _FONT_BOLD
    return _FONT_REGULAR, _FONT_REGULAR
