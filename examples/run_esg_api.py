#!/usr/bin/env python3
"""
Simple REST API example for ESG Agent.

This script starts a simple HTTP API server that exposes ESG Agent functionality.
It demonstrates how to run the agent as a service.

Endpoints:
    GET  / → Health check
    POST /chat → Send a message to the ESG Agent

Environment variables (same as run_esg_agent.py):
    HF_TOKEN, OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.

Usage:
    python3 examples/run_esg_api.py
    
Then test with:
    curl http://localhost:8005/
    curl -X POST http://localhost:8005/chat -H "Content-Type: application/json" \\
         -d '{"message": "What is ESG?"}'
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, Any
# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Теперь импорты из src
from src.agent.esg_agent import ESGAgent
from src.model.manager import ModelManager
from src.memory import memory_manager  # добавь сюда

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:
    print("[!] Error: fastapi and uvicorn are required for the API example.")
    print("    Install with: pip install fastapi uvicorn")
    sys.exit(1)

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.agent.esg_agent import ESGAgent
from src.model.manager import ModelManager
from argparse import Namespace
from src.config import config

# Загрузи esg_agent конфиг
config.initialize(config_path="configs/esg_agent.py", args=Namespace())


# Global state
app = FastAPI(title="ESG Agent API", version="1.0.0")
agent: ESGAgent = None
model_manager: ModelManager = None
from mmengine.config import Config

# Загрузи конфиг
cfg = Config.fromfile('configs/esg_agent.py')

from src.tool.server import tcp
from src.prompt.server import prompt_manager

from src.model import model_manager  # глобальный синглтон

@app.on_event("startup")
async def startup_event():
    global agent

    print("[*] Initializing model manager...")
    await model_manager.initialize()  # инициализируй глобальный

    print("[*] Initializing memory manager...")
    await memory_manager.initialize()

    print("[*] Initializing tool manager...")
    await tcp.initialize()

    print("[*] Initializing prompt manager...")
    await prompt_manager.initialize()

    print("[*] Initializing ESG Agent...")
    agent = ESGAgent(
        workdir=cfg.workdir,
        model_name="ollama/qwen3-30b",
        memory_name=cfg.memory_names[0],
    )
    await agent.initialize()

    print("[*] API startup complete!")

@app.get("/")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "ESG Agent API",
        "models": list(model_manager.model_clients.keys()) if model_manager else [],
    }


@app.post("/chat")
async def chat(request: Dict[str, str]) -> Dict[str, Any]:
    """
    Chat endpoint.
    
    Request body:
        {
            "message": "Your question here"
        }
    
    Response:
        {
            "message": "Agent response",
            "model": "model name used",
            "tokens_used": null
        }
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    message = request.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    try:
        response = await agent(message)
        return {
            "message": response,
            "model": list(model_manager.model_clients.keys())[0],
            "tokens_used": None,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Agent error: {str(e)}"
        )


if __name__ == "__main__":
    print("=" * 60)
    print("ESG Agent - REST API Server")
    print("=" * 60)
    print("\nStarting server on http://localhost:8005")
    print("API docs: http://localhost:8005/docs")
    print("\nPress Ctrl+C to stop\n")
    
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8005,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n\n[!] Server stopped")
        sys.exit(0)
