from __future__ import annotations

import numpy as np


def pack(vec) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def normalize(vec) -> np.ndarray:
    v = np.asarray(vec, dtype="float32")
    n = np.linalg.norm(v)
    return v / n if n else v
