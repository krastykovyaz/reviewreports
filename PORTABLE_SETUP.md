# Portable Setup

This archive contains the project source, configurations, examples, bundled libraries, and ESG dataset. It does not contain local credentials, virtual environments, caches, logs, or runtime workspaces.

## Setup

```bash
cd ESGAgent-portable
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy your API credentials into a new `.env` file in the project root. Do not commit or share that file.

If browser automation is needed:

```bash
cd libs/browser-use
python -m pip install -e .
python -m pip install playwright
playwright install
cd ../..
```

## Run

```bash
python examples/simple_model_test.py
python examples/run_esg_agent.py
python examples/run_esg_api.py
```

The API example listens on the address documented in `examples/README.md`.
