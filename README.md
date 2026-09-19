# ESG Agent and Benchmark

```
ESGAgent-and-Benchmark/
├── src/
│   ├── agents/                 # Agent implementations
│   ├── environments/           # Specialized environments
│   ├── tools/                  # Comprehensive tool system
│   │   ├── default_tools/      # Core utilities (web, file, bash, etc.)
│   │   ├── esg_tools/      	# ESG-oriented tools (plot, rag, etc.)
│   │   ├── workflow_tools/     # Advanced workflows (research, analysis)
│   │   ├── other_tools/        # Other tools used in agent
│   ├── infrastructures/        # Core infrastructure
│   │   ├── models/             # Model management (OpenAI, Anthropic, Google)
│   │   └── memory/             # Memory and state management
│   ├── transformation/         # Protocol transformations
│   ├── logger/                 # Logging system
│   └── utils/                  # Common utilities
├── configs/                    # Configuration files
├── examples/                   # Usage examples and demos
├── datasets/                   # ESG Benchmark
│   ├── validation/             # Validation set
│   ├── test/            		# Test
└   └── ESG.py                  # Manage the data loading logic
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Git
- API keys for your preferred LLM providers

### Installation

1. **Install dependencies:**

```bash
pip install -r requirements.txt
cd libs/
git clone git@github.com:browser-use/browser-use.git
cd browser-use
pip install -e .
pip install playwright
playwright install
```

2. **Set up environment variables:**

```bash
# Setup `.env` in your project root path, the keys should be like:
PYTHONWARNINGS=ignore # ignore warnings
ANONYMIZED_TELEMETRY=false # disable telemetry

# OpenAI API Key
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=abcabcabc
# Anthropic API Key
ANTHROPIC_API_BASE=https://api.anthropic.com
ANTHROPIC_API_KEY=abcabcabc
# DeepSeek (OpenAI-compatible API)
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_API_KEY=abcabcabc
# Google
GOOGLE_API_BASE="xxx"
GOOGLE_API_KEY="xxx"

# HuggingFace (Recommended - free and no auth required for basic models)
HF_TOKEN=hf_xxxxxxxxxxxx
# HF_MODEL=gpt2  # Optional, defaults to gpt2
# HF_API_BASE=...  # Optional, custom HuggingFace endpoint

# In-house Gemma (optional)
# Set GEMMA_API_BASE to your local/in-house gemma server (e.g. http://localhost:8080)
GEMMA_API_BASE="http://localhost:8080"
# If your in-house endpoint requires an API key, set GEMMA_API_KEY
GEMMA_API_KEY="optional-api-key-if-required"

# Search API
BRAVE_SEARCH_API_KEY=abcabcabc
FIRECRAWL_API_KEY=abcabcabc
# Github
GITHUB_TOKEN=abcabcabc
GITHUB_USERNAME=your-github-username
```

## 🎯 Basic Usage

### Available Examples

The `examples/` directory contains ready-to-run demonstrations:

1. **`simple_model_test.py`** - Test model initialization and list available models
   ```bash
   python examples/simple_model_test.py
   ```

2. **`run_esg_agent.py`** - Run the ESG Agent with sample queries
   ```bash
   python examples/run_esg_agent.py
   ```

3. **`run_esg_api.py`** - Start a REST API server for the ESG Agent
   ```bash
   pip install fastapi uvicorn  # One-time setup
   python examples/run_esg_api.py
   ```

4. **`test_models.py`** - Test all configured LLM providers
   ```bash
   python examples/test_models.py
   ```

For detailed instructions and troubleshooting, see [examples/README.md](examples/README.md).

### Supported LLM Providers

Models are automatically initialized based on available API keys (in order of preference):

1. **HuggingFace** (Recommended) - `huggingface/default`
   - Requires: `HF_TOKEN`
   - Free models available without authentication

2. **OpenAI** - `openai/gpt-4o`, `openai/gpt-5`, etc.
   - Requires: `OPENAI_API_KEY`

3. **OpenRouter** - Multi-provider gateway
   - Requires: `OPENROUTER_API_KEY`

4. **Anthropic** - `anthropic/claude-sonnet-4.5`, etc.
   - Requires: `ANTHROPIC_API_KEY`

5. **DeepSeek** - `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner`
   - Requires: `DEEPSEEK_API_KEY`
   - OpenAI-compatible API; default LLM for reviewreports's judgment pillars. Text-only — no vision support.

6. **Google Gemini/Gemma** - Cloud models and in-house Gemma
   - Requires: `GOOGLE_API_KEY` (for Gemini) or `GEMMA_API_BASE` (for in-house Gemma)


