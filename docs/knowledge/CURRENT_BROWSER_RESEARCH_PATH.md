# Current Browser Research Path — Audit (Prompt 3, §1)

What the browser can do for research today, and where the path dead-ends.

## 1. Components (`capabilities/browser/research/`)

| File | Component | Behavior |
| --- | --- | --- |
| `crawler.py` | `CrawlerEngine.crawl(session_id, seed_url, depth, budget, cid)` | bounded BFS; frontier sorted by `SourceRanker.score_url`; extracts `Article` per visited page; returns `ResearchResult{seed_url, articles, visited_urls, confidence}` |
| `reader.py` | `Reader` | strips script/style/nav/header/footer, tags → whitespace-normalized text; `extract_markdown` wraps it with an H1 |
| `source_ranker.py` | `SourceRanker` | domain trust map: official allowlist 1.0, .edu/.gov 0.9, .org 0.7, else 0.5 |

Supporting engines: `NavigationEngine` (URL policy enforced — unsafe URLs refused),
`ExtractionEngine` (article/link extraction), `PageManager` (session pages).

## 2. Where it dead-ends

`ResearchResult.articles` are returned to the caller and **never enter the
knowledge pipeline**:

- not normalized into any document model
- not chunked, embedded, or indexed in `KnowledgeStore`
- not citable later, not re-retrievable, not evaluated
- no session record persists (no “continue my research from yesterday”)

The browser platform’s page fetch and the knowledge store are two islands.

## 3. Security posture (exists, partial)

- Navigation policy: `NavigationEngine` blocks unsafe/private schemes (Tier-2
  safety gating on actions; reads are Tier-0/1)
- **Missing**: prompt-injection screening of fetched page text before it enters
  any context; no untrusted-content marking on extracted articles; no document
  `security_status`.

## 4. What Prompt 3 requires of this path (§6–10)

1. Every browsed/fetched page normalizes into the canonical `KnowledgeDocument`
   (`source_type = BROWSER_PAGE` / `WEB_PAGE`)
2. Same ingest pipeline as PDFs/local files: chunk → embed → BM25+vector index
3. `ResearchSession` persistence: goal, visited URLs, evidence gathered,
   open questions — so research can continue across days
4. Injection scan before content reaches any prompt; browsed content is
   UNTRUSTED data, never instructions
5. Browsing policy: READ/SEARCH by default; LOGIN/FORM/EXTERNAL requires the
   existing safety approval path
