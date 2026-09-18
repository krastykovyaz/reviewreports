#!/usr/bin/env python3
import asyncio
import httpx

OLLAMA_BASE = "http://gpu2.sedan.pro:11434"
MODEL = "gemma4:e2b"

async def test_api_chat():
    print("\n[1] Testing /api/chat...")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            }
        )
        print(f"    Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"    Response: {data['message']['content']}")
        else:
            print(f"    Error: {r.text[:200]}")

async def test_v1_chat():
    print("\n[2] Testing /v1/chat/completions...")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OLLAMA_BASE}/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
                "max_tokens": 100,
            }
        )
        print(f"    Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"    Response: {data['choices'][0]['message']['content']}")
        else:
            print(f"    Error: {r.text[:200]}")

async def test_esg_query():
    print("\n[3] Testing ESG query via /api/chat...")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are a helpful ESG assistant."},
                    {"role": "user", "content": "What are the key environmental metrics for carbon neutrality? Answer in 2 sentences."},
                ],
                "stream": False,
                "options": {"num_ctx": 2048, "num_predict": 200},
            }
        )
        print(f"    Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"    Response: {data['message']['content'][:300]}")
        else:
            print(f"    Error: {r.text[:200]}")

async def main():
    print(f"Testing Ollama at {OLLAMA_BASE} with model {MODEL}")
    await test_api_chat()
    await asyncio.sleep(2)
    await test_v1_chat()
    await asyncio.sleep(2)
    await test_esg_query()

if __name__ == "__main__":
    asyncio.run(main())
