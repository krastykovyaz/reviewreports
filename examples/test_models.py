#!/usr/bin/env python3
"""
Simple model testing script.

Tests all available LLM providers configured in your environment.
Useful for verifying that HuggingFace, OpenAI, Anthropic, etc. are working.

Environment variables required (at least one):
    HF_TOKEN              - HuggingFace Inference API token
    OPENAI_API_KEY        - OpenAI API key
    ANTHROPIC_API_KEY     - Anthropic API key
    GOOGLE_API_KEY        - Google Gemini API key
    OPENROUTER_API_KEY    - OpenRouter API key
    GEMMA_API_BASE        - In-house Gemma endpoint (optional)
    GEMMA_API_KEY         - Gemma auth token (if endpoint requires it)

Usage:
    python3 examples/test_models.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.model.manager import ModelManager
from src.message import Message, SystemMessage, HumanMessage


async def main():
    """Test all available models."""
    
    print("=" * 70)
    print("LLM Model Testing")
    print("=" * 70)
    
    # Initialize model manager
    print("\n[*] Initializing model manager...")
    model_manager = ModelManager()
    await model_manager.initialize()
    
    models = model_manager.model_clients
    print(f"[*] Found {len(models)} model(s): {list(models.keys())}\n")
    
    if not models:
        print("[!] No models configured. Set at least one of:")
        print("    - HF_TOKEN (HuggingFace)")
        print("    - OPENAI_API_KEY (OpenAI)")
        print("    - ANTHROPIC_API_KEY (Anthropic)")
        print("    - GOOGLE_API_KEY (Google)")
        print("    - OPENROUTER_API_KEY (OpenRouter)")
        return
    
    # Test each model
    test_prompt = "Explain ESG (Environmental, Social, Governance) in one sentence."
    
    for model_name, client in models.items():
        print("-" * 70)
        print(f"Testing: {model_name}")
        print("-" * 70)
        
        try:
            # Build message
            messages = [
                SystemMessage(content="You are a helpful assistant."),
                HumanMessage(content=test_prompt),
            ]
            
            print(f"Prompt: {test_prompt}\n")
            print("Response:", end=" ", flush=True)
            
            # Call model
            client = model_manager.model_clients[model_name]
            response = await client(messages)
            
            if response and response.message:
                print(response.message[:200] + "..." if len(response.message) > 200 else response.message)
            else:
                print("[Empty response]")
            
            print(f"\n✓ {model_name} works!\n")
            
        except Exception as e:
            print(f"\n✗ {model_name} failed!")
            print(f"  Error: {type(e).__name__}: {e}\n")
    
    print("=" * 70)
    print("Model testing completed")
    print("=" * 70)


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
