# reviewreports

An agent framework and service for generating structured **review reports**:
website audits, code reviews, and document reviews (resumes, presentations,
books/manuscripts) — one shared schema, one scoring model, three output
formats (Markdown/HTML/PDF/LaTeX), and full en/ru/fr localization.

The repo began as an ESG (Environmental, Social, Governance) analysis agent.
That agent, its retrieval tools, and the ESG benchmark dataset still exist
(`configs/esg_agent.py`, `src/tool/esg_tools/`, `datasets/ESG/`) but are no
longer the product identity — reviewreports is.

## What it does

Submit a URL, a code repository, or a document, and get back a scored report:

- **Website audit** — ~55 checks across SEO, security headers, accessibility,
  performance, privacy, links, and tech stack. Each finding carries evidence
  (the observed value) and fix guidance. Content tone/clarity and Design & UX
  add an LLM judgment pass when a model is configured.
- **Code review** — repository structure (README/LICENSE/tests/CI, language
  breakdown) plus an LLM review of the largest sampled source files.
- **Document review** — resume, presentation, or book/manuscript, via
  `markitdown` text extraction and a flavor-specific LLM rubric.

Two ways in:
- **JSON API**: `POST /reports {url, kind, model_name, lang}` → job id;
  `GET /reports/{id}` for status; `GET /reports/{id}.md|.html|.pdf|.tex` for
  the rendered report.
- **Web UI**: a submit → progress → report flow with a kind selector and
  file upload, fully localized (`GET /?lang=ru`).
- **Agent tools**: `website_audit`, `code_review`, `document_review` are
  registered tools usable by the agent loop (`configs/reviewreports.py`).

## Project structure

```
reviewreports/
├── src/
│   ├── audit/                  # Website audit: collectors + orchestrator
│   │   └── collectors/         # SEO, security, a11y, performance, privacy, links, tech stack, content, UX
│   ├── review/                 # Code review, document review, kind dispatcher
│   ├── report/                 # Shared Report schema + Markdown/HTML/LaTeX/PDF renderers
│   ├── i18n/                   # en/ru/fr message catalogs (per-collector + chrome/UI)
│   ├── service/                # FastAPI app, job store (SQLite), web templates
│   ├── model/                  # Multi-provider model manager (OpenAI, Anthropic, DeepSeek, Google, HF, Ollama, OpenRouter)
│   ├── agent/, tool/, environment/, memory/, prompt/  # Generic agent framework (also powers the ESG agent)
│   └── tool/esg_tools/, datasets/ESG/                 # Legacy ESG agent + benchmark (kept, not the product)
├── configs/
│   ├── reviewreports.py        # Main agent config: review tools + generic prompt
│   └── esg_agent.py            # Legacy ESG agent config
├── examples/run_website_audit.py   # CLI: evaluate a site, print + write the report
└── tests/                      # pytest suite (collectors, renderers, service, i18n completeness)
```

## Quick start

### Prerequisites

- Python 3.12
- `brew install pango` (or the Debian/Ubuntu equivalent) if you want PDF export — `weasyprint` needs it.

### Install

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Run a website audit from the CLI

```bash
python examples/run_website_audit.py example.com --format all --lang ru
```

Prints the executive summary and writes `.md`/`.html`/`.pdf`/`.tex` to `workdir/reports/`.

### Run the service (API + web UI)

```bash
uvicorn src.service.app:app --port 8010
```

Then open `http://localhost:8010/` for the web UI, or:

```bash
curl -X POST localhost:8010/reports -H 'content-type: application/json' \
  -d '{"url": "example.com", "kind": "website_audit", "lang": "en"}'
```

### Run the agent

```bash
python examples/run_esg_agent.py   # uses configs/esg_agent.py (legacy ESG agent)
```

For the reviewreports agent identity (generic review-tool prompt instead of
the ESG persona), point the same bootstrap at `configs/reviewreports.py`.

### Run the tests

```bash
pytest tests/ -q
```

## Environment variables

Set up `.env` in the project root:

```bash
# DeepSeek (OpenAI-compatible API) — default model for the LLM judgment pillars
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_API_KEY=abcabcabc

# OpenAI
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=abcabcabc
# Anthropic
ANTHROPIC_API_BASE=https://api.anthropic.com
ANTHROPIC_API_KEY=abcabcabc
# Google
GOOGLE_API_BASE="xxx"
GOOGLE_API_KEY="xxx"

# HuggingFace (free tier available, but see note below)
HF_TOKEN=hf_xxxxxxxxxxxx

# Ollama (self-hosted)
OLLAMA_API_BASE=http://localhost:11434

# Search (used by the agent's browser/search tools, not by the review pipelines)
BRAVE_SEARCH_API_KEY=abcabcabc
FIRECRAWL_API_KEY=abcabcabc
```

None of the review kinds require a model — the deterministic checks (the
majority of a website audit's ~55 checks, and all of a repo's structure
checks) run with no LLM configured. A model only adds: Content tone/clarity,
Design & UX (needs vision — DeepSeek does not support it), code quality
commentary, and document review rubrics.

### Supported LLM providers

1. **DeepSeek** — `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner`. OpenAI-protocol-compatible; the default. Text-only, no vision.
2. **OpenAI** — `openai/gpt-4o`, `openai/gpt-5`, etc. Supports vision (needed for Design & UX).
3. **Anthropic** — `anthropic/claude-sonnet-4.5`, etc. Supports vision.
4. **Google Gemini/Gemma** — `GOOGLE_API_KEY` (cloud) or `GEMMA_API_BASE` (in-house).
5. **OpenRouter** — multi-provider gateway, `OPENROUTER_API_KEY`.
6. **Ollama** — self-hosted, `OLLAMA_API_BASE`.
7. **HuggingFace** — reachable with `HF_TOKEN`, but its client does not implement structured output (`response_format`), so it cannot power any of the LLM judgment pillars today.

## Legacy: the ESG agent

`configs/esg_agent.py` still runs the original ESG analysis agent (RAG
retrieval over `datasets/ESG/`, report generation via the same underlying
`report` tool). It's unrelated to review-report generation and kept as-is —
see `examples/run_esg_agent.py` / `examples/run_esg_api.py`.
