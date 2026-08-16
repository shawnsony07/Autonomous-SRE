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


# Direct REST endpoint — v1beta is the published, stable path for text-embedding-004.
# Using httpx (already in requirements) avoids all SDK version/routing issues.
_EMBED_URL = (
    "https://generativelanguage.googleapis.com"
    "/v1beta/models/text-embedding-004:embedContent"
)


class _GeminiEmbedder:
    """Calls the Gemini embedContent REST API directly via httpx.

    No google-genai / langchain-google-genai SDK involved — just a plain HTTPS
    POST to the published v1beta endpoint.  text-embedding-004 returns 768-dim
    vectors which match the incident_memory VECTOR(768) column exactly.
    """

    def __init__(self, api_key: str):
        import httpx
        self._params = {"key": api_key}
        self._http = httpx.Client(timeout=30.0)

    def _embed(self, text: str) -> list:
        import httpx
        response = self._http.post(
            _EMBED_URL,
            params=self._params,
            json={
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": text}]},
            },
        )
        response.raise_for_status()
        return response.json()["embedding"]["values"]

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

    Pure httpx REST call — zero SDK version constraints.
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
