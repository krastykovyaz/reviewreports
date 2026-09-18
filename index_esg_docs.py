#!/usr/bin/env python3
import asyncio
import os
import sys
from pathlib import Path
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).parent))

from src.config import config
config.initialize(config_path="configs/esg_agent.py", args=Namespace())

from src.tool.esg_tools.lightrag import LightRAG, QueryParam
from src.tool.esg_tools.lightrag.llm.ollama import ollama_embed
from src.tool.esg_tools.lightrag.utils import EmbeddingFunc
from src.tool.esg_tools.lightrag.llm.openai import gpt_4o_mini_complete
from src.tool.esg_tools.lightrag.kg.shared_storage import initialize_pipeline_status
from src.tool.esg_tools.lightrag.llm.ollama import _ollama_model_if_cache, ollama_embed
from src.tool.esg_tools.lightrag.utils import EmbeddingFunc
from src.tool.esg_tools.lightrag.kg.shared_storage import initialize_pipeline_status

async def ollama_llm(prompt, system_prompt=None, history_messages=[], **kwargs):
    kwargs.pop("hashing_kv", None)
    kwargs.pop("keyword_extraction", None)
    return await _ollama_model_if_cache(
        "qwen3:30b",
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        host="http://gpu2.sedan.pro:11434",
        **kwargs,
    )

async def main():
    rag = LightRAG(
        working_dir="workdir/esg_agent/tool/retriever",
        embedding_func=EmbeddingFunc(
            embedding_dim=768,
            max_token_size=8192,
            func=lambda texts, **kwargs: ollama_embed(
                texts,
                embed_model="nomic-embed-text",
                host="http://gpu2.sedan.pro:11434",
            )
        ),
        llm_model_func=ollama_llm,
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()

    for f in Path("workdir/esg_agent/tool/retriever").glob("*.json"):
        f.unlink()
    print("Cleared old data")

    docs_dir = Path("datasets/ESG/test")
    files = list(docs_dir.glob("*.txt")) + list(docs_dir.glob("*.md"))
    print(f"Found {len(files)} files")

    for f in files:
        print(f"Indexing {f.name}...")
        text = f.read_text(encoding="utf-8", errors="ignore")
        if text.strip():
            await rag.ainsert(text)
            print(f"  ✓ Done")

    await rag.finalize_storages()
    print("Indexing complete!")

asyncio.run(main())