"""Isolated, resource-limited PDF child. Reads bytes from stdin, returns bounded safe JSON."""

import io
import json
import logging
import resource
import sys
from importlib.metadata import version

MAX_CHARS = 200000


def main() -> None:
    # Apply before importing the parser or touching untrusted input. Fail closed off POSIX.
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
    logging.disable(logging.CRITICAL)
    try:
        from pypdf import PdfReader

        content = sys.stdin.buffer.read(1_000_001)
        if len(content) > 1_000_000:
            result: dict[str, object] = {"error": "source_too_large"}
        elif not content.startswith(b"%PDF-"):
            result = {"error": "malformed_pdf"}
        else:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                result = {"error": "encrypted_pdf"}
            elif not 1 <= len(reader.pages) <= int(sys.argv[1]):
                result = {"error": "page_limit"}
            else:
                pages: list[str] = []
                count = 0
                ocr_required = False
                for page in reader.pages:
                    # pypdf preserves some layout whitespace, without a reliable structure tree.
                    text = page.extract_text(
                        extraction_mode="layout", layout_mode_space_vertically=False
                    )
                    resources = page.get("/Resources", {})
                    if hasattr(resources, "get_object"):
                        resources = resources.get_object()
                    xobjects = resources.get("/XObject", {}) if resources else {}
                    if hasattr(xobjects, "get_object"):
                        xobjects = xobjects.get_object()
                    # Do not decode image pixels. Any nontext XObject on an unreadable page
                    # is conservatively marked OCR-required (including forms containing images).
                    contents = page.get_contents() if not text.strip() else None
                    inline_image = bool(
                        contents is not None
                        and any(operator == b"INLINE IMAGE" for _, operator in contents.operations)
                    )
                    if not text.strip() and (xobjects or inline_image):
                        ocr_required = True
                    count += len(text) + (1 if pages else 0)
                    if count > MAX_CHARS:
                        raise OverflowError
                    pages.append(text)
                if ocr_required:
                    result = {"error": "ocr_required"}
                elif not any(text.strip() for text in pages):
                    result = {"error": "empty_pdf"}
                else:
                    result = {"pages": pages, "parser_revision": version("pypdf")}
    except OverflowError:
        result = {"error": "text_limit"}
    except MemoryError:
        result = {"error": "parser_resource_limit"}
    except Exception:
        result = {"error": "malformed_pdf"}
    # No parser exception, input text, attachments, credentials or internal paths in errors.
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
