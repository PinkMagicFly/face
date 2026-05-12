#!/usr/bin/env python3
"""Extract component names from a possibly broken circuit PDF.

The script first tries to recover text embedded in PDF content streams without
needing a PDF viewer. That makes it useful for PDFs that fail to open because of
junk before/after the PDF body, a broken xref table, or a missing trailer. If
system OCR tools are installed, it can also fall back to rendering + tesseract.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Iterable

STRICT_PREFIXES = (
    "CONN",
    "LED",
    "RLY",
    "TVS",
    "RN",
    "RP",
    "FB",
    "JP",
    "TP",
    "SW",
    "IC",
    "BT",
    "BZ",
    "LS",
    "VR",
    "RV",
    "ZD",
    "CN",
    "BR",
    "R",
    "C",
    "L",
    "D",
    "Q",
    "U",
    "J",
    "F",
)
POWER_LABELS = {"GND", "VCC", "AVDD", "DVDD"}
STRICT_COMPONENT_RE = re.compile(rf"^(?:{'|'.join(STRICT_PREFIXES)})(?:[1-9]\d?)(?:[A-Z])?$", re.IGNORECASE)
POWER_RE = re.compile(r"^(?:GND|VCC|AVDD|DVDD|VIN\d{0,2}|VOUT\d{0,2})$", re.IGNORECASE)
TEXTY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+/#-]{0,31}")

MEGA_API = "https://g.api.mega.co.nz/cs?id=0"


def b64url_decode(value: str) -> bytes:
    """Decode MEGA's unpadded base64url strings."""
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)


def parse_mega_public_link(url: str) -> tuple[str, str]:
    """Return (file_handle, file_key) from a public MEGA file URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in {"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"}:
        raise ValueError("not a MEGA URL")

    if parsed.path.startswith("/file/"):
        handle = parsed.path.strip("/").split("/", 1)[1]
        key = parsed.fragment
    elif parsed.fragment.startswith("!"):
        parts = parsed.fragment.split("!")
        if len(parts) < 3:
            raise ValueError("old-style MEGA URL is missing handle or key")
        handle, key = parts[1], parts[2]
    else:
        raise ValueError("unsupported MEGA URL format")

    if not handle or not key:
        raise ValueError(
            "MEGA public file links must include the decryption key after '#'; "
            "copy the complete link from Tally, not only the browser address before '#'."
        )
    return handle, key


def mega_key_and_iv(key_text: str) -> tuple[bytes, bytes]:
    """Convert a MEGA file key into AES-128 key bytes and CTR IV bytes."""
    raw_key = b64url_decode(key_text)
    if len(raw_key) != 32:
        raise ValueError(f"expected a 32-byte MEGA file key, got {len(raw_key)} bytes")
    words = struct.unpack(">8I", raw_key)
    aes_words = (words[0] ^ words[4], words[1] ^ words[5], words[2] ^ words[6], words[3] ^ words[7])
    aes_key = struct.pack(">4I", *aes_words)
    iv = struct.pack(">4I", words[4], words[5], 0, 0)
    return aes_key, iv


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def http_post_json(url: str, payload: object) -> object:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def openssl_aes_ctr_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to decrypt MEGA public file downloads")
    completed = subprocess.run(
        ["openssl", "enc", "-aes-128-ctr", "-d", "-K", key.hex(), "-iv", iv.hex()],
        input=ciphertext,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", "ignore") or "openssl AES-CTR decrypt failed")
    return completed.stdout


def download_mega_public_file(url: str) -> bytes:
    """Download and decrypt a public MEGA file link without opening it in a browser."""
    handle, key_text = parse_mega_public_link(url)
    api_response = http_post_json(MEGA_API, [{"a": "g", "g": 1, "p": handle}])
    if not isinstance(api_response, list) or not api_response:
        raise RuntimeError("unexpected MEGA API response")
    file_info = api_response[0]
    if isinstance(file_info, int):
        raise RuntimeError(f"MEGA API returned error code {file_info}")
    if not isinstance(file_info, dict) or "g" not in file_info:
        raise RuntimeError(f"MEGA API did not return a download URL: {file_info!r}")

    encrypted = http_get(str(file_info["g"]))
    aes_key, iv = mega_key_and_iv(key_text)
    return openssl_aes_ctr_decrypt(encrypted, aes_key, iv)


def load_input_bytes(input_ref: str) -> tuple[bytes, str]:
    """Load PDF bytes from a local path, a public MEGA URL, or a normal URL."""
    parsed = urllib.parse.urlparse(input_ref)
    if parsed.scheme in {"http", "https"}:
        if "mega." in parsed.netloc:
            return download_mega_public_file(input_ref), input_ref
        return http_get(input_ref), input_ref
    return Path(input_ref).read_bytes(), input_ref


def carve_pdf(raw: bytes) -> bytes:
    """Return bytes between the first %PDF header and the last %%EOF marker."""
    start = raw.find(b"%PDF-")
    if start == -1:
        return raw
    end = raw.rfind(b"%%EOF")
    if end == -1:
        return raw[start:]
    return raw[start : end + len(b"%%EOF")]


def unescape_pdf_literal(text: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c != 0x5C:  # backslash
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(text):
            break
        esc = text[i]
        table = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        if esc in table:
            out.append(table[esc])
            i += 1
        elif esc in b"()\\":
            out.append(esc)
            i += 1
        elif 48 <= esc <= 55:
            octal = bytes([esc])
            i += 1
            for _ in range(2):
                if i < len(text) and 48 <= text[i] <= 55:
                    octal += bytes([text[i]])
                    i += 1
                else:
                    break
            out.append(int(octal, 8))
        elif esc in b"\r\n":
            while i < len(text) and text[i] in b"\r\n":
                i += 1
        else:
            out.append(esc)
            i += 1
    return out.decode("utf-8", "ignore") or out.decode("latin-1", "ignore")


def decode_hex_pdf_string(hex_bytes: bytes) -> str:
    cleaned = re.sub(rb"\s+", b"", hex_bytes)
    if len(cleaned) % 2:
        cleaned += b"0"
    try:
        data = bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return ""
    # UTF-16BE strings are common in PDFs and often start with BOM FE FF.
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "ignore")
    return data.decode("utf-8", "ignore") or data.decode("latin-1", "ignore")


def decode_stream(stream: bytes, header: bytes) -> bytes:
    # Stream data may start with a newline after the 'stream' keyword.
    stream = stream.lstrip(b"\r\n")
    if b"/FlateDecode" in header or b"/Fl" in header:
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(stream, wbits)
            except zlib.error:
                pass
    return stream


def iter_pdf_text_chunks(pdf_bytes: bytes) -> Iterable[str]:
    """Yield text chunks found in PDF literal/hex strings and content streams."""
    candidates = [pdf_bytes]
    for match in re.finditer(rb"(<<.*?>>)\s*stream\r?\n?(.*?)\r?\n?endstream", pdf_bytes, re.S):
        candidates.append(decode_stream(match.group(2), match.group(1)))

    literal = re.compile(rb"\((?:\\.|[^\\()])*\)")
    hex_string = re.compile(rb"<([0-9A-Fa-f\s]{4,})>")
    for blob in candidates:
        for m in literal.finditer(blob):
            txt = unescape_pdf_literal(m.group(0)[1:-1]).strip()
            if txt:
                yield txt
        for m in hex_string.finditer(blob):
            txt = decode_hex_pdf_string(m.group(1)).strip()
            if txt:
                yield txt


def run_tool(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def external_text(pdf_path: Path) -> str:
    texts: list[str] = []
    if shutil.which("pdftotext"):
        texts.append(run_tool(["pdftotext", "-layout", str(pdf_path), "-"]))
    if shutil.which("mutool"):
        texts.append(run_tool(["mutool", "draw", "-F", "txt", str(pdf_path)]))
    return "\n".join(t for t in texts if t)


def external_ocr(pdf_path: Path, dpi: int) -> str:
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf_path), str(prefix)], check=False)
        texts = []
        for image in sorted(Path(tmp).glob("page-*.png")):
            texts.append(run_tool(["tesseract", str(image), "stdout", "--psm", "6"]))
        return "\n".join(texts)


def normalize_name(name: str) -> str:
    name = name.strip().upper().replace(" ", "")
    name = re.sub(r"[^A-Z0-9_.+/#-]", "", name)
    return name.strip("._+-/#")


def strict_component_name(token: str) -> str | None:
    """Return a clean reference designator, or None for noisy PDF/OCR text.

    The damaged challenge PDF produces thousands of random strings that merely
    look like ``letter + digit``.  Keep only whole-token, conventional reference
    designators such as R12, C3, U1, JP2, TP4, and exact power-net labels.
    """
    name = normalize_name(token)
    if not name:
        return None
    if POWER_RE.fullmatch(name):
        return name
    if not name.isalnum():
        return None
    if STRICT_COMPONENT_RE.fullmatch(name):
        return name
    return None


def component_sort_key(name: str) -> tuple[str, int, str]:
    number = re.search(r"\d+", name)
    prefix = name[: number.start()] if number else name
    return prefix, int(number.group(0)) if number else -1, name


def extract_names(text: str) -> list[str]:
    names = set()
    for token in TEXTY_RE.findall(text):
        name = strict_component_name(token)
        if name:
            names.add(name)
    return sorted(names, key=component_sort_key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract circuit component names from a damaged PDF")
    parser.add_argument("pdf", help="input PDF path or complete public MEGA/HTTP URL, even if browsers cannot open it")
    parser.add_argument("-o", "--output", type=Path, default=Path("components.txt"), help="output text file")
    parser.add_argument("--json", dest="json_output", type=Path, help="optional JSON output path")
    parser.add_argument("--ocr", action="store_true", help="try OCR fallback if pdftoppm+tesseract are installed")
    parser.add_argument("--dpi", type=int, default=300, help="OCR render DPI")
    parser.add_argument(
        "--keep-repaired",
        nargs="?",
        const="",
        metavar="PATH",
        help="write the carved PDF bytes for debugging; defaults to OUTPUT with .repaired.pdf suffix",
    )
    args = parser.parse_args(argv)

    try:
        raw, input_label = load_input_bytes(args.pdf)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    repaired_path: Path | None = None
    temp_repaired: tempfile.NamedTemporaryFile[bytes] | None = None
    if b"%PDF-" not in raw:
        # Also support filtering a previously generated noisy text candidate list.
        # This is useful for the challenge output that contained thousands of
        # random letter+digit strings from binary/PDF noise.
        merged_text = raw.decode("utf-8", "ignore") or raw.decode("latin-1", "ignore")
    else:
        repaired = carve_pdf(raw)
        if args.keep_repaired is not None:
            repaired_path = Path(args.keep_repaired) if args.keep_repaired else args.output.with_suffix(".repaired.pdf")
            repaired_path.write_bytes(repaired)
        else:
            temp_repaired = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            temp_repaired.write(repaired)
            temp_repaired.close()
            repaired_path = Path(temp_repaired.name)

        chunks = list(iter_pdf_text_chunks(repaired))
        text_sources = ["\n".join(chunks), external_text(repaired_path)]
        if args.ocr:
            text_sources.append(external_ocr(repaired_path, args.dpi))
        merged_text = "\n".join(t for t in text_sources if t)

    names = extract_names(merged_text)

    args.output.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
    if args.json_output:
        args.json_output.write_text(json.dumps({"count": len(names), "components": names}, ensure_ascii=False, indent=2), encoding="utf-8")

    if temp_repaired is not None and repaired_path is not None:
        repaired_path.unlink(missing_ok=True)

    print(f"input: {input_label}")
    if args.keep_repaired is not None and repaired_path is not None:
        print(f"repaired_pdf: {repaired_path}")
    print(f"components: {len(names)}")
    for name in names:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
