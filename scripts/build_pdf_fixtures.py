"""Controlled PDF fixtures authored from literal text. Does not run the extractor."""

import io
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

FIXTURES = Path("evaluation/nlp/source-v1")


def pdf_fixture(
    pages: list[list[str]],
    encrypted: bool = False,
    image_only: bool = False,
    inline_image: bool = False,
) -> bytes:
    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        commands = ["BT /F1 12 Tf 14 TL 50 740 Td"]
        for index, line in enumerate(lines):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(("T* " if index else "") + f"({escaped}) Tj")
        commands.append("ET")
        stream = StreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        if inline_image:
            stream.set_data(
                b"q 100 0 0 100 50 600 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00 EI Q"
            )
        if image_only:
            image = StreamObject()
            image.set_data(b"\xff\x00\x00")
            image.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(1),
                    NameObject("/Height"): NumberObject(1),
                    NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                }
            )
            resources = page[NameObject("/Resources")]
            assert isinstance(resources, DictionaryObject)
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im0"): writer._add_object(image)}
            )
            stream.set_data(b"q 100 0 0 100 50 600 cm /Im0 Do Q")
    if encrypted:
        writer.encrypt("fixture-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cases = {
        "retail.pdf": [
            ["ATLAS Retail", "Table public.orders stores payment totals."],
            ["Pipeline ingestion", "Pipeline ingest consumes payment_events."],
        ],
        "incident.pdf": [
            ["Incident INC-2042", "The ingest pipeline missed a partition."],
            ["Deployment DEP-108", "The payments service was restarted."],
            ["Resolution", "Evidence is insufficient to establish causality."],
        ],
        "table.pdf": [
            [
                "Revenue report",
                "Region          Revenue",
                "EU              120",
                "US              250",
            ]
        ],
    }
    for name, pages in cases.items():
        (FIXTURES / name).write_bytes(pdf_fixture(pages))
    (FIXTURES / "scanned.pdf").write_bytes(pdf_fixture([[]], image_only=True))
    (FIXTURES / "inline-scanned.pdf").write_bytes(pdf_fixture([[]], inline_image=True))
    (FIXTURES / "encrypted.pdf").write_bytes(pdf_fixture([["Private text"]], encrypted=True))
    (FIXTURES / "malformed.pdf").write_bytes(b"%PDF-1.7\nprivate-parser-error-canary")


if __name__ == "__main__":
    main()
