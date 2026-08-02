import os
import httpx
import asyncio
import json
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

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

async def aget_active_llm():
    global _cached_llm

    if _cached_llm is not None:
        return _cached_llm

    lock = get_llm_lock()
    async with lock:
        if _cached_llm is not None:
            return _cached_llm

        print("Initializing LLM routed through LiteLLM proxy...")
        try:
            # LiteLLM exposes an OpenAI-compatible endpoint. A dummy key is passed 
            # while LiteLLM handles the actual AWS Signature V4 authentication to Bedrock.
            llm = ChatOpenAI(
                base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:4000"),
                api_key="sk-litellm-dummy-key",
                model="agentic-sre-model",
                temperature=0.0
            )
            # Try a quick inference to ensure the gateway is functional
            test_response = await llm.ainvoke("respond with an empty json object {}")
            print("SUCCESS: LiteLLM Bedrock model (agentic-sre-model) is functional and locked in.")
            _cached_llm = llm
            return _cached_llm
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to initialize or communicate with LiteLLM. Reason: {e}")
            raise RuntimeError(f"LiteLLM server unreachable or misconfigured. Error: {e}") from e
