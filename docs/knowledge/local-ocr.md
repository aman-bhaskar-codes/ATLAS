# Local documents and OCR

Knowledge path ingestion and uploads enter the same Knowledge Fabric used by
research. Text/Markdown/code is parsed locally. PDF pages use their existing text
layer first; only pages without usable text are rasterized and OCR'd. Single-frame
PNG, JPEG, WebP, GIF, TIFF, and BMP images are OCR'd locally. Multi-frame images
must be converted to a PDF or separate pages; they are never silently truncated.

Install the optional Python support:

```sh
uv sync --extra ocr
```

Scans also require `tesseract` with English language data and Poppler's
`pdftoppm` on the runtime PATH. For example, on macOS:

```sh
brew install tesseract poppler
```

Linux distributions commonly provide these as `tesseract-ocr` and
`poppler-utils`. No OCR model, paid API, cloud job, or remote upload is used.
Text PDFs need the Python extra, but do not require native OCR executables.
A missing dependency returns an explicit unavailable error with setup guidance;
malformed, empty, encrypted, unsupported, or oversized documents are rejected.

The default extraction limits are 20 MiB per file, 100 PDF pages, 20 million
pixels per image, 400,000 output characters, and 60 seconds for the entire
worker. PDF stream decompression is bounded independently of compressed input
size. Native rasterization is limited to 2,400 pixels along the larger dimension.
Parsing and native tools run in a separate process; a timeout or cancellation
terminates its process group. Linux also applies a memory limit; POSIX workers
apply CPU and output-file limits. Limits reject documents rather than silently
indexing incomplete pages.

Local path ingestion resolves symlinks and traversal before enforcing the
manifest read allowlist and credential-directory exclusions. Uploads have the
same byte cap and retain `upload://original-name.ext` provenance rather than a
soon-deleted temporary path. Chunk offsets and page ranges survive SQL persistence,
vector metadata, evidence selection, citation rendering, and research session
citation payloads. Extracted content still passes the existing injection scanner.
OCR output is recognized text, and may contain recognition mistakes; no
placeholder text or generated descriptions are substituted for extraction.

BM25 and dense vectors use the shared hybrid retriever, followed by the shared
feature reranker. Missing embeddings leave lexical retrieval available with
reported degradation. Content dedupe is scoped by source type, retains a bounded
list of source aliases, and reprocesses stale pipeline versions.

Embedding spaces are identified by provider, canonical endpoint, and model.
Switching from an old Qwen embedding model to BGE-M3 opens a separate vector
collection. Existing document, chunk, page-provenance, evidence, and session rows
remain in SQL, and old collections remain intact. The ingestion pipeline's
`reindex_embeddings(limit_documents=10, timeout_s=60)` rebuilds a bounded batch
from those rows and stamps only documents whose chunks all succeeded. Failed or
partial batches leave source data and lexical retrieval intact and remain
retryable; they never claim migration is complete.

The implementation follows the official [pypdf text extraction guidance](https://pypdf.readthedocs.io/en/stable/user/extract-text.html),
[pypdf resource-limit API](https://pypdf.readthedocs.io/en/stable/modules/configuration.html),
[Pillow image validation documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html),
and [Tesseract command-line documentation](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).

Run the focused OCR and ingestion checks with the extra installed:

```sh
uv run --extra ocr pytest tests/knowledge/test_document_ocr.py tests/knowledge/test_rag_ingestion_boundary.py tests/knowledge/test_embedding_reindex.py tests/knowledge/test_document_bounds.py
```

Real OCR tests use committed synthetic fixtures containing known turbine text;
they require local native executables and explicitly skip when those optional
runtimes are absent. Controlled regressions also cover unavailable dependencies,
malformed binaries, decode/pixel/output limits, timed-out workers, upload caps,
source authorization, source-scoped dedupe, migration failures, and page citations.
