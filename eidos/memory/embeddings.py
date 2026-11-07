"""Embedding client for local vector representations via Ollama nomic-embed-text."""

import hashlib
import logging
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:latest"
DEFAULT_EMBEDDING_DIM = 768


class OllamaEmbeddingClient:
    """Client for generating text embeddings using local Ollama nomic-embed-text."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = DEFAULT_EMBEDDING_DIM,
        timeout: float = 15.0,
        fallback_to_dummy: bool = False,
        num_gpu: Optional[int] = 0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimension = dimension
        self.timeout = timeout
        self.fallback_to_dummy = fallback_to_dummy
        self.num_gpu = num_gpu

    def _request_options(self) -> Dict[str, Any]:
        if self.num_gpu is None:
            return {}
        return {"num_gpu": self.num_gpu}

    def is_available(self) -> bool:
        """Check if Ollama is reachable and has the requested embedding model installed."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3.0)
            if resp.status_code == 200:
                models = [m.get("name") for m in resp.json().get("models", [])]
                # Match full name or base name before colon
                base_target = self.model.split(":")[0]
                return any(
                    self.model == m or m.startswith(f"{base_target}:") or m == base_target
                    for m in models
                )
        except Exception:
            pass
        return False

    def embed_text(self, text: str) -> List[float]:
        """Generate a single 768-dimensional embedding vector for the given text."""
        cleaned = text.strip()
        if not cleaned:
            # Return zero vector for empty text
            return [0.0] * self.dimension

        vectors = self.embed_batch([cleaned])
        return vectors[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a batch of strings."""
        if not texts:
            return []

        # 1. Try modern Ollama /api/embed endpoint
        try:
            payload: Dict[str, Any] = {
                "model": self.model,
                "input": texts,
                # Drop weights after CPU embed so the chat LLM is not competing in Ollama.
                "keep_alive": 0,
            }
            options = self._request_options()
            if options:
                payload["options"] = options
            resp = requests.post(
                f"{self.base_url}/api/embed",
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings", [])
                if len(embeddings) == len(texts):
                    return embeddings
        except Exception as e:
            logger.debug("Ollama /api/embed failed, trying fallback /api/embeddings: %s", e)

        # 2. Try legacy /api/embeddings endpoint (single prompt per call)
        results: List[List[float]] = []
        try:
            for t in texts:
                body: Dict[str, Any] = {
                    "model": self.model,
                    "prompt": t,
                    "keep_alive": 0,
                }
                options = self._request_options()
                if options:
                    body["options"] = options
                resp = requests.post(
                    f"{self.base_url}/api/embeddings",
                    json=body,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    emb = resp.json().get("embedding")
                    if emb and len(emb) == self.dimension:
                        results.append(emb)
                    else:
                        raise ValueError(f"Unexpected embedding dimension: {len(emb) if emb else 0}")
                else:
                    raise RuntimeError(f"Ollama returned HTTP {resp.status_code}")

            if len(results) == len(texts):
                return results
        except Exception as e:
            logger.warning("Failed to generate embeddings via Ollama (%s): %s", self.model, e)
            if self.fallback_to_dummy:
                logger.info("Using deterministic fallback embedding generator.")
                return [self._pseudo_embed(t) for t in texts]
            raise

        if self.fallback_to_dummy:
            return [self._pseudo_embed(t) for t in texts]
        raise RuntimeError(f"Failed to generate embeddings for batch of size {len(texts)}")

    def _pseudo_embed(self, text: str) -> List[float]:
        """Deterministic pseudo-embedding for testing or offline recovery based on feature hashing."""
        import re
        tokens = re.findall(r"\w+", text.lower())
        vec = [0.0] * self.dimension
        if not tokens:
            return vec
        for tok in tokens:
            h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dimension
            vec[idx] += 1.0
        for i in range(len(text) - 2):
            trigram = text[i:i + 3].lower()
            h = int(hashlib.md5(trigram.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dimension
            vec[idx] += 0.5
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]
