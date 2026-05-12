#!/usr/bin/env python3
"""Create a tiny intentionally damaged circuit PDF for local testing."""
from __future__ import annotations

import argparse
import zlib
from pathlib import Path

LABELS = ["R1", "R2", "C1", "C2", "U1", "D1", "LED1", "J1", "SW1", "TP1", "GND", "VCC"]


def pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf() -> bytes:
    commands = ["BT /F1 14 Tf"]
    x, y = 60, 740
    for i, label in enumerate(LABELS):
        commands.append(f"1 0 0 1 {x + (i % 4) * 110} {y - (i // 4) * 70} Tm ({pdf_escape(label)}) Tj")
        commands.append(f"{x + (i % 4) * 110 - 12} {y - (i // 4) * 70 - 10} 80 32 re S")
    commands.append("ET")
    stream = zlib.compress("\n".join(commands).encode("ascii"))

    objects: list[bytes] = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objects.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objects.append(
        b"5 0 obj << /Length "
        + str(len(stream)).encode()
        + b" /Filter /FlateDecode >> stream\n"
        + stream
        + b"\nendstream endobj\n"
    )

    body = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(body))
        body += obj
    xref_at = len(body)
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n".encode())
    trailer = b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    return body + b"".join(xref) + trailer


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an intentionally damaged sample circuit PDF")
    parser.add_argument("output", nargs="?", default="sample_broken_circuit.pdf", help="output PDF path")
    args = parser.parse_args()

    output = Path(args.output)
    output.write_bytes(b"NOT_A_PDF_PREFIX\x00\x01\n" + build_pdf() + b"\nBROKEN_TAIL")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
