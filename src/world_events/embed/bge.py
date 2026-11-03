"""S4 の埋め込み。bge-m3 をコンテナ内の CPU（sentence-transformers）で、ローカル・本番とも同じ実装で計算する。
環境で実装を変えるとベクトルがずれ、クラスタリングの閾値が合わなくなるため（システム構築案 §4.3）。"""
from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 16):
        self.model_name, self.batch_size, self._model = model_name, batch_size, None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:   # モデルの読み込みは重いので、初めて使うときまで遅らせる
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError("埋め込みには sentence-transformers が必要: uv sync --extra embed") from e
            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True).tolist()
