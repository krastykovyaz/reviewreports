#!/usr/bin/env python3
"""
Simple ESG model inference example.

This script demonstrates how to use the ESG models for text generation.
It supports multiple LLM providers:
- HuggingFace (default, requires HF_TOKEN)
- OpenAI (requires OPENAI_API_KEY)
- Anthropic (requires ANTHROPIC_API_KEY)
- Google Gemini/Gemma (requires GOOGLE_API_KEY or custom GEMMA_API_BASE)
- OpenRouter (requires OPENROUTER_API_KEY)

Usage:
    python3 examples/run_esg_agent.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path so we can import from the project
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.model.manager import ModelManager
from src.message import Message, SystemMessage, HumanMessage


async def main():
    """Run ESG-related queries through available models."""
    
    print("=" * 60)
    print("ESG Agent - Environmental, Social, Governance Analysis")
    print("=" * 60)
    
    # Initialize model manager
    print("\n[*] Initializing model manager...")
    model_manager = ModelManager()
    await model_manager.initialize()
    
    # Show available models
    models = model_manager.model_clients
    print(f"[*] Available models: {list(models.keys())[:5]}...")  # Show first 5
    
    if not models:
        print("[!] No models configured. Set at least one API key:")
        print("    HF_TOKEN, OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.")
        return
    default_model = 'ollama/gemma4-e2b'
    print(f"[*] Using default model: {default_model}")
    
    # Example ESG queries
    queries = [
        "What are the key environmental metrics for carbon neutrality?",
        "Explain ESG importance in corporate governance in one sentence.",
    ]
    
    print("\n" + "=" * 60)
    print("Running ESG Analysis")
    print("=" * 60)
    
    for i, query in enumerate(queries, 1):
        print(f"\n[Query {i}] {query}")
        print("-" * 60)
        
        try:
            # Build message
            messages = [
                SystemMessage(content="You are a helpful assistant for ESG (Environmental, Social, Governance) topics."),
                HumanMessage(content=query),
            ]
            
            # Call model
            client = model_manager.model_clients[default_model]
            print("[DEBUG] Before client call")
            response = await client(messages)
            print("[DEBUG] After client call")
                        
            if response and response.message:
                # Truncate long responses for display
                content = response.message
                if len(content) > 300:
                    content = content[:300] + "..."
                print(f"[Response] {content}")
            else:
                print("[Response] (empty)")
            
        except Exception as e:
            print(f"[Error] {type(e).__name__}: {str(e)[:100]}")
    
    print("\n" + "=" * 60)
    print("ESG analysis completed")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n[!] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n[!] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

