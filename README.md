# Damaged circuit-PDF component extractor

This repository contains a Python solution for the Tally programming challenge. The challenge asks for a program that parses a circuit-diagram PDF into component-name text. The PDF link may not open in a normal browser/PDF viewer, so the solution does **not** depend on opening the PDF visually.

> No PDF or other binary challenge/sample files are committed to this repository. Generate local samples or place downloaded challenge files in a temporary path when you need to run the program.

## What the program does

1. Accepts either a local PDF path or a complete public MEGA/HTTP URL.
2. For MEGA links, downloads through the public file API and decrypts the file with the `#...` decryption key from the copied link, instead of opening the file in a browser.
3. Carves the real PDF body from the first `%PDF-` header to the final `%%EOF` marker.
4. Parses PDF streams directly, including `/FlateDecode` streams, and recovers literal or hex-encoded text strings.
5. Optionally calls installed command-line tools (`pdftotext`, `mutool`, `pdftoppm`, `tesseract`) for extra coverage.
6. Normalizes and filters likely component labels such as `R1`, `C12`, `U3`, `LED1`, `J2`, `TP4`, `GND`, and `VCC`.

## Usage for the challenge file

Copy the **complete** MEGA file link from the Tally page, including the part after `#`, and pass it directly to the extractor:

```bash
python extract_components.py 'https://mega.nz/file/FILE_ID#FILE_KEY' -o components.txt --json components.json
```

If the browser address bar only shows `https://mega.nz/file/FILE_ID` without `#FILE_KEY`, copy the actual link target from the Tally page again. MEGA public files cannot be decrypted without the fragment key.

If you already downloaded the broken PDF bytes another way, run the same extractor on the local file:

```bash
python extract_components.py /tmp/circuit.pdf -o components.txt --json components.json
```

If you also want to save the repaired/carved PDF body for debugging, pass `--keep-repaired`:

```bash
python extract_components.py /tmp/circuit.pdf -o components.txt --json components.json --keep-repaired repaired.pdf
```

If OCR tools are installed and the PDF is image-only:

```bash
python extract_components.py /tmp/circuit.pdf -o components.txt --json components.json --ocr
```

## Local sample run without committed binaries

`make_sample_broken_pdf.py` can generate a deliberately damaged PDF with a valid compressed content stream surrounded by junk bytes. The generated PDF is intentionally ignored by git, so the repository remains source/text only.

```bash
python make_sample_broken_pdf.py /tmp/sample_broken_circuit.pdf
python extract_components.py /tmp/sample_broken_circuit.pdf -o sample_components.txt --json sample_components.json
```

Expected output:

```text
input: /tmp/sample_broken_circuit.pdf
components: 12
C1
C2
D1
GND
J1
LED1
R1
R2
SW1
TP1
U1
VCC
```
