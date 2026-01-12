# ESG Agent and Benchmark

```
AgentWorld/
├── src/
│   ├── agents/                 # Agent implementations
│   ├── environments/           # Specialized environments
│   ├── tools/                  # Comprehensive tool system
│   │   ├── default_tools/      # Core utilities (web, file, bash, etc.)
│   │   ├── esg_tools/      		# ESG-oriented tools (plot, rag, etc.)
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
│   ├── test/            			  # Test
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
# Google
GOOGLE_API_BASE="xxx"
GOOGLE_API_KEY="xxx"

#Search API
BRAVE_SEARCH_API_KEY=abcabcabc
FIRECRAWL_API_KEY=abcabcabc
#Github
GITHUB_TOKEN=abcabcabc
GITHUB_USERNAME=your-github-username
```

## 🎯 Basic Usage

```bash
# run ESG Agent
python examples/run_esg_agent.py

# run LLMs like GPT family, Gemini family
python examples/run_esg_api.py
```

