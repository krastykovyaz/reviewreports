#!/usr/bin/env python3
"""
Minimal HuggingFace model test (avoids screenshot dependencies).

This tests basic model manager initialization without loading heavy dependencies.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

async def main():
    """Test model initialization."""
    
    print("=" * 70)
    print("Minimal Model Manager Test")
    print("=" * 70)
    
    # Check environment
    print("\n[*] Checking environment variables...")
    hf_token = os.getenv("HF_TOKEN", "").strip()
    
    if hf_token:
        print(f"    ✓ HF_TOKEN is set (length: {len(hf_token)})")
    else:
        print("    ✗ HF_TOKEN not set - HuggingFace models won't work")
    
    print(f"    • OPENAI_API_KEY: {'SET' if os.getenv('OPENAI_API_KEY') else 'NOT SET'}")
    print(f"    • ANTHROPIC_API_KEY: {'SET' if os.getenv('ANTHROPIC_API_KEY') else 'NOT SET'}")
    print(f"    • GOOGLE_API_KEY: {'SET' if os.getenv('GOOGLE_API_KEY') else 'NOT SET'}")
    
    print("\n[*] Loading model manager (this may take a moment)...")
    try:
        from src.model.manager import ModelManager
        
        model_manager = ModelManager()
        await model_manager.initialize()
        
        models = model_manager.model_clients
        print(f"\n[✓] Model manager initialized successfully!")
        print(f"[*] Available models: {list(models.keys())}")
        
        if models:
            first_model = list(models.keys())[0]
            print(f"[*] First/default model: {first_model}")
        
    except ImportError as e:
        print(f"\n[✗] Failed to import ModelManager: {e}")
        print("    This is expected if system dependencies are missing.")
        return False
    except Exception as e:
        print(f"\n[✗] Error initializing model manager: {type(e).__name__}")
        print(f"    {e}")
        return False
    
    print("\n" + "=" * 70)
    return True


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[!] Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n[!] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
