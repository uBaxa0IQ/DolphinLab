"""Local, bounded extraction. Invoked in a disposable subprocess without credentials."""

import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile

from dolphinlab.domain.documents import MAX_CHUNKS, MAX_DOCUMENT_BYTES, MAX_TEXT


def extract(path, name):
    """Return text and source coordinates; never invent PDF/DOCX structure."""
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds 20 MiB")
    suffix = Path(name).suffix.lower()
    elements, warnings = [], []
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8-sig")
        if len(text) > MAX_TEXT:
            raise ValueError("Extracted document exceeds text limit")
        headings, pending, start, fenced = [], [], 1, False

        def flush(end):
            if pending:
                elements.append(
                    (
                        "\n".join(pending),
                        {
                            "headings": list(headings),
                            "line_start": start,
                            "line_end": end,
                            "kind": "text",
                        },
                    )
                )
                pending.clear()

        lines = text.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            number = index + 1
            heading = (
                re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
                if suffix == ".md" and not fenced
                else None
            )
            if heading:
                flush(number - 1)
                depth = len(heading[1])
                headings[depth - 1 :] = [heading[2]]
            elif (
                suffix == ".md"
                and not fenced
                and index + 1 < len(lines)
                and "|" in line
                and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])
            ):
                flush(number - 1)
                rows = [line]
                index += 2  # delimiter is syntax, not a table row
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    rows.append(lines[index])
                    index += 1
                elements.append(
                    (
                        rows,
                        {
                            "headings": list(headings),
                            "line_start": number,
                            "line_end": index,
                            "kind": "table",
                        },
                    )
                )
                continue
            elif not line.strip() and not fenced:
                flush(number - 1)
            else:
                if not pending:
                    start = number
                pending.append(line)
                if line.lstrip().startswith(("```", "~~~")) and suffix == ".md":
                    fenced = not fenced
            index += 1
        flush(len(lines))
    elif suffix == ".docx":
        from docx import Document
        from docx.table import Table

        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 30 * 1024 * 1024:
                raise ValueError("DOCX expanded size exceeds limit")
            if any(e.file_size > max(1024 * 1024, e.compress_size * 500) for e in entries):
                raise ValueError("DOCX compression ratio exceeds limit")
        doc, headings = Document(path), []
        for index, block in enumerate(doc.iter_inner_content()):
            locator = {"block": index, "headings": list(headings), "kind": "text"}
            if isinstance(block, Table):
                rows = [" | ".join(cell.text for cell in row.cells) for row in block.rows]
                locator["kind"] = "table"
                if rows:
                    elements.append((rows, locator))
            else:
                heading = re.match(r"Heading (\d+)", block.style.name or "")
                if heading:
                    headings[int(heading[1]) - 1 :] = [block.text]
                elif block.text.strip():
                    elements.append((block.text, locator))
        warnings.append(
            "DOCX coordinates are body block/row indices, not rendered page numbers. Headers, footers and text boxes are not indexed."
        )
    elif suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF is unsupported")
        if len(reader.pages) > 500:
            raise ValueError("PDF exceeds 500 pages")
        blank = []
        total = 0
        for index, page in enumerate(reader.pages):
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 10 * 1024 * 1024:
                raise ValueError("PDF content stream exceeds limit")
            text = (page.extract_text(extraction_mode="layout") or "") if stream is not None else ""
            total += len(text)
            if total > MAX_TEXT:
                raise ValueError("Extracted document exceeds text limit")
            if text.strip():
                elements.append((text, {"page": index + 1, "kind": "text", "headings": []}))
            else:
                blank.append(index + 1)
        warnings.append(
            "PDF text/layout extraction only: table cells, reading order and headings are not guaranteed; inspect the cited original page before interpreting a table."
        )
        if blank:
            warnings.append(
                "Pages without extracted text (OCR not run): " + ",".join(map(str, blank[:50]))
            )
    else:
        raise ValueError("Unsupported document format")
    if not elements:
        raise ValueError("No text extracted; scanned PDF requires OCR, which is not supported")
    if sum(len(str(value)) for value, _ in elements) > MAX_TEXT:
        raise ValueError("Extracted document exceeds text limit")
    return elements, warnings


def parse_document(path, name):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    elements, warnings = extract(Path(path), name)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=2400,
        chunk_overlap=160,
        add_start_index=True,
        separators=["\n\n", "\n", ". ", "! ", "? ", "; ", " ", ""],
    )
    chunks = []
    groups = []
    for element, locator in elements:
        if (
            isinstance(element, str)
            and groups
            and isinstance(groups[-1][-1][0], str)
            and all(locator.get(k) == groups[-1][-1][1].get(k) for k in ("headings", "page"))
        ):
            groups[-1].append((element, locator))
        else:
            groups.append([(element, locator)])
    for group in groups:
        element, locator = group[0]
        spans, offset = [], 0
        if isinstance(element, str):
            for value, location in group:
                spans.append((offset, offset + len(value), location))
                offset += len(value) + 2
            element = "\n\n".join(value for value, _ in group)
        heading = " / ".join(locator["headings"])
        if len(heading) > 2000:
            raise ValueError("Heading path exceeds limit")
        parts = []
        if isinstance(element, list):
            header = element[0]
            if len(header) > 2000:
                raise ValueError("Table header exceeds limit; use a structured dataset")
            for row_index, row in enumerate(element[1:] or [""]):
                for piece in splitter.create_documents([row or header]):
                    parts.append(
                        (
                            header + "\n" + piece.page_content,
                            {"row": row_index + 1, "row_char_start": piece.metadata["start_index"]},
                        )
                    )
        else:
            parts = [
                (
                    p.page_content,
                    {
                        "char_start": p.metadata["start_index"],
                        "char_end": p.metadata["start_index"] + len(p.page_content),
                    },
                )
                for p in splitter.create_documents([element])
            ]
        for text, position in parts:
            source_locator = locator
            if spans:
                relevant = [
                    loc
                    for start, end, loc in spans
                    if start < position["char_end"] and end > position["char_start"]
                ]
                source_locator = {
                    **relevant[0],
                    "elements": [
                        {k: v for k, v in loc.items() if k != "headings"} for loc in relevant
                    ],
                }
                if "line_end" in relevant[-1]:
                    source_locator["line_end"] = relevant[-1]["line_end"]
            text = (heading + "\n" if heading else "") + text
            if not text.strip():
                continue
            chunks.append(
                {
                    "text": text,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "locator": {**source_locator, **position},
                }
            )
            if len(chunks) > MAX_CHUNKS:
                raise ValueError("Document exceeds chunk limit")
    if not chunks:
        raise ValueError("No searchable text extracted")
    return {"chunks": chunks, "warnings": warnings}


def main():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    request = json.loads(sys.stdin.read(4096))
    result = parse_document(request["path"], request["name"])
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
