"""Moteur d'embeddings (fastembed local multilingual-e5-small / bge-m3 ou repli n-gram deterministe)."""

from __future__ import annotations

import hashlib
import math
import re


class EmbeddingEngine:
    """Genere des vecteurs normalises L2 pour la recherche semantique et la deduplication."""

    def __init__(
        self,
        model_name: str = "intfloat/multilingual-e5-small",
        dim: int = 128,
        use_fastembed: bool = False,
    ):
        self.model_name = model_name
        self.dim = dim
        self._fastembed_model = None
        if use_fastembed:
            try:
                from fastembed import TextEmbedding  # type: ignore

                self._fastembed_model = TextEmbedding(model_name=model_name)
            except Exception:
                self._fastembed_model = None

    def embed(self, text: str) -> list[float]:
        if self._fastembed_model is not None:
            try:
                vec = list(next(iter(self._fastembed_model.embed([text]))))
                return [float(x) for x in vec]
            except Exception:
                pass
        return self._deterministic_ngram_embed(text, self.dim)

    @staticmethod
    def _deterministic_ngram_embed(text: str, dim: int = 128) -> list[float]:
        vec = [0.0] * dim
        tokens = re.findall(r"[a-zA-Z0-9àâäéèêëîïôöùûüç]+", (text or "").lower())
        if not tokens:
            return vec
        features = list(tokens)
        for tok in tokens:
            if len(tok) >= 3:
                for i in range(len(tok) - 2):
                    features.append(tok[i : i + 3])
        for feat in features:
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)
