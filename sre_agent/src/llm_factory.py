import os
import httpx
import asyncio
import json
from langchain_ollama import ChatOllama
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

MODELS_CASCADE = [
    "gemma4:12b",
    "qwen2.5-coder:32b-instruct",
    "gemma4:26b",
    "qwen2.5-coder:7b-instruct",
    ]

_cached_llm = None
_cached_embeddings = None
_llm_lock = None
_emb_lock = __import__('threading').Lock()

def get_llm_lock():
    global _llm_lock
    if _llm_lock is None:
        _llm_lock = asyncio.Lock()
    return _llm_lock

def reset_llm_state():
    global _cached_llm, _cached_embeddings
    _cached_llm = None
    _cached_embeddings = None

def get_ollama_base_url():
    # Evaluate env first, default only if empty or omitted
    url = os.getenv("OLLAMA_BASE_URL")
    if not url:
        url = "http://localhost:11434"
    return url

def get_embeddings():
    global _cached_embeddings
    with _emb_lock:
        if _cached_embeddings is None:
            from langchain_ollama import OllamaEmbeddings
            _cached_embeddings = OllamaEmbeddings(model="nomic-embed-text", base_url=get_ollama_base_url())
        return _cached_embeddings

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type((httpx.HTTPError, json.JSONDecodeError)))
async def _probe_ollama(base_url):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
        return response.json()

async def aget_active_llm():
    global _cached_llm

    if _cached_llm is not None:
        return _cached_llm

    lock = get_llm_lock()
    async with lock:
        if _cached_llm is not None:
            return _cached_llm

        base_url = get_ollama_base_url()
        print(f"Checking Ollama endpoint at: {base_url}")

        # Check if server is responding to avoid long timeouts on every model
        try:
            data = await _probe_ollama(base_url)
            available_models = [m['name'] for m in data.get('models', [])]
            print(f"Models available on server: {available_models}")
        except Exception as e:
            print(f"CRITICAL: Cannot reach Ollama API. Connection failed. Error: {e}")
            raise RuntimeError(f"Ollama server unreachable at {base_url}") from e

        for model_name in MODELS_CASCADE:
            print(f"Testing model: {model_name}...")

            try:
                llm = ChatOllama(
                    base_url=base_url,
                    model=model_name,
                    temperature=0.0,
                    format="json"
                )
                # Try a quick inference to ensure it's functional
                test_response = await llm.ainvoke("respond with an empty json object {}")
                print(f"SUCCESS: Model {model_name} is functional and locked in.")
                _cached_llm = llm
                return _cached_llm
            except Exception as e:
                print(f"FAILED to initialize or use {model_name}. Reason: {e}")
                continue

        print("CRITICAL ERROR: No models in the fallback cascade were functional.")
        raise RuntimeError("No functional Ollama models available in the configured cascade.")
