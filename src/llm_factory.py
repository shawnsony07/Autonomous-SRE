import os
import asyncio
import json
from langchain_openai import ChatOpenAI

_cached_llms = {}
_cached_embeddings = None
_llm_lock = None
_emb_lock = __import__('threading').Lock()

# Timeout (seconds) for the gateway health-probe at LLM init time.
_LLM_INIT_TIMEOUT = float(os.getenv("LLM_INIT_TIMEOUT_S", "30"))

# Timeout (seconds) applied to every individual LLM call in the pipeline.
LLM_CALL_TIMEOUT = float(os.getenv("LLM_CALL_TIMEOUT_S", "120"))


def get_llm_lock():
    global _llm_lock
    if _llm_lock is None:
        _llm_lock = asyncio.Lock()
    return _llm_lock


def reset_llm_state():
    global _cached_llms, _cached_embeddings
    _cached_llms = {}
    _cached_embeddings = None


# Gemini API key read once at module level
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class _GeminiEmbedder:
    """Thin embeddings wrapper using the google-genai SDK pinned to v1 API.

    google-genai is already installed as a transitive dep of langchain-google-genai.
    Forcing api_version='v1' bypasses the v1beta routing that causes 404
    for text-embedding-004.
    """

    def __init__(self, api_key: str, model: str = "text-embedding-004"):
        from google import genai
        self._client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1"},
        )
        self._model = model

    def _embed(self, text: str) -> list:
        result = self._client.models.embed_content(model=self._model, contents=text)
        return result.embeddings[0].values

    def embed_query(self, text: str) -> list:
        return self._embed(text)

    def embed_documents(self, texts: list) -> list:
        return [self._embed(t) for t in texts]

    async def aembed_query(self, text: str) -> list:
        return await asyncio.to_thread(self._embed, text)

    async def aembed_documents(self, texts: list) -> list:
        return await asyncio.to_thread(self.embed_documents, texts)


def get_embeddings():
    """Return a cached _GeminiEmbedder (text-embedding-004, 768-dim).

    Uses google-generativeai directly (v1 API) — no langchain-google-genai
    wrapper, no dependency conflicts, no v1beta routing issues.
    """
    global _cached_embeddings
    with _emb_lock:
        if _cached_embeddings is None:
            _cached_embeddings = _GeminiEmbedder(api_key=_GEMINI_API_KEY)
        return _cached_embeddings


async def aget_active_llm(tier: str = "sre-fast-tier"):
    """Return a cached ChatOpenAI pointing at the LiteLLM proxy for *tier*.

    A short ``asyncio.wait_for`` guard prevents a slow LiteLLM gateway from
    holding the initialisation lock indefinitely and starving the event loop.
    """
    global _cached_llms

    if tier in _cached_llms:
        return _cached_llms[tier]

    lock = get_llm_lock()
    async with lock:
        if tier in _cached_llms:
            return _cached_llms[tier]

        print(f"Initializing LLM via LiteLLM proxy for tier: {tier} …")
        try:
            llm = ChatOpenAI(
                base_url=os.getenv("OPENAI_BASE_URL", "http://litellm:4000"),
                api_key="sk-litellm-dummy-key",
                model=tier,
                temperature=0.0,
                timeout=LLM_CALL_TIMEOUT,
            )
            # Health-probe with a hard wall-clock timeout so a slow/absent
            # gateway does not block the event loop beyond _LLM_INIT_TIMEOUT.
            await asyncio.wait_for(
                llm.ainvoke("respond with an empty json object {}"),
                timeout=_LLM_INIT_TIMEOUT,
            )
            print(f"SUCCESS: LiteLLM tier '{tier}' is functional.")
            _cached_llms[tier] = llm
            return _cached_llms[tier]
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"LiteLLM gateway did not respond within {_LLM_INIT_TIMEOUT}s "
                f"for tier '{tier}'."
            )
        except Exception as e:
            print(f"CRITICAL: LiteLLM init failed for tier '{tier}': {e}")
            raise RuntimeError(
                f"LiteLLM misconfigured for '{tier}'. Error: {e}"
            ) from e
