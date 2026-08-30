# Printed-email PDF and OCR strategy

Printed or scanned email is source evidence even when no original RFC 5322
bytes survive. OCR output is a derived representation: it must never replace
the source PDF or be described as byte-preserved email.

## Preservation model

1. Read and hash the original PDF without modifying it. Record its source
   volume, path, byte length, modification time, and SHA-256.
2. Write OCR products only below an ignored, operator-selected output
   directory. Record the source hash, tool versions, language models, options,
   and hashes of every derivative.
3. Do not publish OCR-derived text into canonical MBOX. Printed messages need a
   distinct document-derived record type because their original RFC 5322 bytes
   do not exist. Adding original PDFs to the bag would require an explicit
   archive-format decision, such as a new `data/documents/` payload.

## Page classification and OCR

1. Inventory the PDF with Poppler and render every page for structural and
   visual validation. Measure embedded text per page rather than classifying a
   whole PDF as either digital or scanned.
2. Preserve usable embedded text. For image-only or poor-text pages, use
   OCRmyPDF in `skip` mode with rotation and deskew enabled, initially avoiding
   aggressive image cleanup. The searchable PDF is a derivative, not a
   replacement for the input.
3. Extract complete text from the resulting mixed PDF with `pdftotext` because
   OCRmyPDF sidecars contain only pages on which OCR actually ran. Retain
   page-delimited text plus hOCR or TSV coordinates and word confidences from
   Tesseract for layout-aware reconstruction.
4. Record a typed result for every page: embedded text, OCR text, blank,
   failed, or manually excluded. A failed page must remain visible and must not
   disappear from the accounting.

OCRmyPDF documents mixed-PDF handling through its `skip` processing mode and
supports rotation, deskew, archival output, and text sidecars. Tesseract can
emit text, searchable PDF, hOCR, and TSV representations.

## Printed-message reconstruction

Segment page text using layout and repeated header cues such as `From:`, `To:`,
`Date:`, `Subject:`, `Message-ID:`, `Return-Path:`, and page headers or footers.
Keep the exact page-delimited OCR text as evidence. Store extracted fields,
message boundaries, page spans, bounding boxes, confidence, and manual review
state separately from that evidence.

The first usable product should be searchable document text linked back to the
PDF and page. Structured message reconstruction is a second phase. Only
high-confidence boundaries should become message-like records, and the UI must
label them as `printed/OCR`, not as native email. A synthetic MBOX export may be
offered later, but it must be explicitly derivative and must use stable IDs
derived from the PDF SHA-256 and page span.

## Acceptance gates

* Source PDF SHA-256 is unchanged before and after every run.
* Page counts and page dimensions agree between source and derivative.
* Rendered source and searchable derivative are visually compared page by page.
* Every page has a typed outcome and traceable text artifact.
* A representative gold set measures header-field accuracy and message-boundary
  precision/recall, not merely total OCR character count.
* Dates, addresses, and message boundaries below configured confidence remain
  review items and do not silently enter canonical mail.

## Implementation sequence

1. Add read-only `pdf-audit` and derived `pdf-ocr` Make targets with typed JSON
   manifests and disposable outputs.
2. Validate the five current PDFs and compare new page text against the existing
   `.pdf.txt` OCR derivatives without replacing either source.
3. Add page-level search and a review report.
4. Design and approve the document-derived catalog schema and canonical PDF
   payload policy before integrating printed messages into the archive.
