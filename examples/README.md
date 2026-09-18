# ESG Agent Examples

This directory contains example scripts for running the ESG Agent and testing the integrated LLM models.

## Quick Start

### 1. Set up environment variables

Create or update `.env` in the project root with at least one API key:

```bash
# HuggingFace (Primary - recommended)
HF_TOKEN=hf_xxxxxxxxxxxx
# HF_MODEL=gpt2  # Optional, defaults to gpt2
# HF_API_BASE=...  # Optional, custom endpoint

# OpenAI (Optional)
# OPENAI_API_KEY=sk-...

# Anthropic (Optional)
# ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini (Optional)
# GOOGLE_API_KEY=...
# GEMMA_API_BASE=http://localhost:8080  # Optional, in-house Gemma
# GEMMA_API_KEY=...  # Optional if Gemma endpoint requires auth

# OpenRouter (Optional)
# OPENROUTER_API_KEY=...
```

### 2. Install dependencies

```bash
pip install -r ../requirements.txt
```

### 3. Run an example

```bash
python3 simple_model_test.py     # Test model initialization
python3 run_esg_agent.py         # Run the ESG Agent
python3 run_esg_api.py           # Start the REST API server
```

## Available Examples

### `simple_model_test.py`

**Purpose**: Verify that the model manager is initialized and list available models.

**Output**: Shows which models are registered and the primary model.

```bash
python3 simple_model_test.py
```

**Example output**:
```
[✓] Model manager initialized successfully!
[*] Available models: ['huggingface/default', 'openai/gpt-4o', ...]
[*] First/default model: huggingface/default
```

### `run_esg_agent.py`

**Purpose**: Demonstrate the ESG Agent analyzing environmental, social, and governance data.

**Features**:
- Initializes model manager with available providers
- Runs ESG queries through the agent
- Shows agent responses

```bash
python3 run_esg_agent.py
```

**Example queries**:
- "What are the key environmental metrics for carbon neutrality?"
- "Explain ESG importance in corporate governance."

### `run_esg_api.py`

**Purpose**: Start a REST API server with the ESG Agent.

**Endpoints**:
- `GET /` - Health check
- `POST /chat` - Send a message to the agent
- `GET /docs` - Swagger UI documentation

**Requirements**:
```bash
pip install fastapi uvicorn
```

**Usage**:
```bash
python3 run_esg_api.py
```

Then test with curl:
```bash
# Health check
curl http://localhost:8000/

# Chat with agent
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is ESG?"}'
```

## Model Providers

The ESG Agent supports multiple LLM providers. Models are initialized in this order:

1. **HuggingFace** (primary/default)
   - `huggingface/default`
   - Requires: `HF_TOKEN`
   - Fast and free for basic models

2. **OpenAI**
   - `openai/gpt-4o`, `openai/gpt-5`, etc.
   - Requires: `OPENAI_API_KEY`

3. **OpenRouter**
   - `openrouter/gpt-4o`, `openrouter/claude-sonnet-3.5`, etc.
   - Requires: `OPENROUTER_API_KEY`
   - Gateway to many providers

4. **Anthropic**
   - `anthropic/claude-sonnet-4.5`, `anthropic/claude-opus-4.5`, etc.
   - Requires: `ANTHROPIC_API_KEY`

5. **Google Gemini & Gemma**
   - `google/gemini-2.5-flash`, `google/gemini-2.5-pro`, etc.
   - Requires: `GOOGLE_API_KEY`
   - Gemma (in-house): Requires `GEMMA_API_BASE` and optionally `GEMMA_API_KEY`

**Priority**: The first model in the list is used as the default. Set `HF_TOKEN` to use HuggingFace as primary.

## Troubleshooting

### "No models configured"
Set at least one API key in `.env`:
```bash
echo "HF_TOKEN=hf_xxxxxxxxxxxx" > ../.env
```

### "ImportError: No module named..."
Install dependencies:
```bash
pip install -r ../requirements.txt
```

### "OSError: cairo library not found"
This is optional (used for screenshot functionality). The models still work without it.

### Models not initializing
- Check that `.env` is in the project root, not in `examples/`
- Verify API keys are correct and have proper permissions
- Check network connectivity

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HF_TOKEN` | For HuggingFace | - | HuggingFace Inference API token |
| `HF_MODEL` | No | `gpt2` | HuggingFace model name |
| `HF_API_BASE` | No | HF Public API | Custom HuggingFace endpoint |
| `OPENAI_API_KEY` | For OpenAI | - | OpenAI API key |
| `ANTHROPIC_API_KEY` | For Anthropic | - | Anthropic API key |
| `GOOGLE_API_KEY` | For Google | - | Google Gemini API key |
| `OPENROUTER_API_KEY` | For OpenRouter | - | OpenRouter API key |
| `GEMMA_API_BASE` | For in-house Gemma | - | Custom Gemma endpoint URL |
| `GEMMA_API_KEY` | No | - | Auth token for Gemma endpoint |

## Development

To add a new example:

1. Create a new `.py` file in this directory
2. Add it to imports at the top of this README
3. Document the purpose and usage
4. Test with `python3 your_example.py`

Example template:
```python
#!/usr/bin/env python3
"""
Your example description.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.manager import ModelManager

async def main():
    print("Example starting...")
    model_manager = ModelManager()
    await model_manager.initialize()
    print(f"Models: {list(model_manager.model_clients.keys())}")

if __name__ == "__main__":
    asyncio.run(main())
```

## Notes

- All examples use `asyncio` for async/await support
- Models are initialized with sensible defaults
- The agent runs queries through the default model (usually HuggingFace)
- API responses are streamed where supported
- Errors are caught and displayed with helpful context
