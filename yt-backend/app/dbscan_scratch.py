"""
NumPy 기반 cosine DBSCAN 직접 구현.

scikit-learn DBSCAN 의존을 줄이기 위한 백엔드 군집화 모듈이다.
댓글 임베딩은 고차원 문장 벡터이므로 cosine distance를 사용한다.
"""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Iterable

import numpy as np


NOISE = -1
UNASSIGNED = -99


def _normalize_rows(embeddings) -> np.ndarray:
    x = np.asarray(embeddings, dtype=np.float32)
    if x.ndim != 2:
        raise ValueError(f"embeddings must be a 2D array, got shape={x.shape}")

    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return x / norms


def _clamp_eps(raw_eps: float, n: int) -> float:
    if n < 500:
        return max(0.15, min(0.25, raw_eps))
    if n < 2000:
        return max(0.20, min(0.30, raw_eps))
    return max(0.25, min(0.35, raw_eps))


def _estimate_eps_from_k_distances(k_distances: np.ndarray, n: int) -> float:
    if len(k_distances) <= 1:
        return _clamp_eps(0.25, n)

    sorted_distances = np.sort(k_distances)
    elbow_idx = int(np.argmax(np.diff(sorted_distances)))
    return _clamp_eps(float(sorted_distances[elbow_idx]), n)


def _cosine_distance_matrix(x: np.ndarray) -> np.ndarray:
    distances = np.float32(1.0) - (x @ x.T)
    np.clip(distances, 0.0, 2.0, out=distances)
    np.fill_diagonal(distances, 0.0)
    return distances


def _estimate_eps_full_matrix(distances: np.ndarray, min_samples: int) -> float:
    n = distances.shape[0]
    kth_index = min(max(min_samples - 1, 0), n - 1)
    kth_distances = np.partition(distances, kth_index, axis=1)[:, kth_index]
    return _estimate_eps_from_k_distances(kth_distances, n)


def _estimate_eps_blockwise(x: np.ndarray, min_samples: int, block_size: int) -> float:
    n = x.shape[0]
    kth_index = min(max(min_samples - 1, 0), n - 1)
    kth_distances = np.empty(n, dtype=np.float32)

    for start in range(0, n, block_size):
        end = min(start + block_size, n)
        block_distances = np.float32(1.0) - (x[start:end] @ x.T)
        np.clip(block_distances, 0.0, 2.0, out=block_distances)
        rows = np.arange(end - start)
        cols = np.arange(start, end)
        block_distances[rows, cols] = 0.0
        kth_distances[start:end] = np.partition(block_distances, kth_index, axis=1)[:, kth_index]

    return _estimate_eps_from_k_distances(kth_distances, n)


def _neighbors_from_full_matrix(distances: np.ndarray, eps: float) -> list[np.ndarray]:
    return [np.flatnonzero(distances[i] <= eps).astype(np.int32, copy=False) for i in range(distances.shape[0])]


def _neighbors_blockwise(x: np.ndarray, eps: float, block_size: int) -> list[np.ndarray]:
    n = x.shape[0]
    neighbors: list[np.ndarray] = []

    for start in range(0, n, block_size):
        end = min(start + block_size, n)
        block_distances = np.float32(1.0) - (x[start:end] @ x.T)
        np.clip(block_distances, 0.0, 2.0, out=block_distances)
        rows = np.arange(end - start)
        cols = np.arange(start, end)
        block_distances[rows, cols] = 0.0

        for row in range(end - start):
            neighbors.append(np.flatnonzero(block_distances[row] <= eps).astype(np.int32, copy=False))

    return neighbors


def _expand_clusters(neighbors: list[np.ndarray], min_samples: int) -> np.ndarray:
    n = len(neighbors)
    labels = np.full(n, UNASSIGNED, dtype=np.int32)
    visited = np.zeros(n, dtype=bool)
    cluster_id = 0

    for point_idx in range(n):
        if visited[point_idx]:
            continue

        visited[point_idx] = True
        point_neighbors = neighbors[point_idx]

        if len(point_neighbors) < min_samples:
            labels[point_idx] = NOISE
            continue

        labels[point_idx] = cluster_id
        queue = deque(int(i) for i in point_neighbors)
        queued = set(queue)

        while queue:
            current = queue.popleft()

            if not visited[current]:
                visited[current] = True
                current_neighbors = neighbors[current]

                if len(current_neighbors) >= min_samples:
                    for neighbor_idx in current_neighbors:
                        neighbor_idx = int(neighbor_idx)
                        if neighbor_idx not in queued:
                            queued.add(neighbor_idx)
                            queue.append(neighbor_idx)

            if labels[current] in (UNASSIGNED, NOISE):
                labels[current] = cluster_id

        cluster_id += 1

    labels[labels == UNASSIGNED] = NOISE
    return labels


def fit_predict_cosine_dbscan(
    embeddings,
    *,
    eps: float | None = None,
    min_samples: int = 5,
    full_matrix_limit: int | None = None,
    block_size: int | None = None,
) -> tuple[list[int], dict]:
    """
    cosine distance 기반 DBSCAN labels를 반환한다.

    full_matrix_limit 이하에서는 거리 행렬을 한 번만 만든 뒤 eps 탐색과
    DBSCAN 이웃 질의에 재사용한다. 큰 입력은 block 단위로 처리해 메모리
    사용량을 제한한다.
    """

    started = time.perf_counter()
    x = _normalize_rows(embeddings)
    n = x.shape[0]

    if n == 0:
        return [], {"backend": "scratch_numpy", "n": 0, "eps": eps, "min_samples": min_samples}
    if n == 1:
        return [NOISE], {"backend": "scratch_numpy", "n": 1, "eps": eps, "min_samples": min_samples}

    full_matrix_limit = full_matrix_limit or int(os.getenv("DBSCAN_FULL_MATRIX_LIMIT", "10000"))
    block_size = block_size or int(os.getenv("DBSCAN_BLOCK_SIZE", "1024"))

    if n <= full_matrix_limit:
        mode = "full_matrix"
        distances = _cosine_distance_matrix(x)
        if eps is None:
            eps = _estimate_eps_full_matrix(distances, min_samples)
        neighbors = _neighbors_from_full_matrix(distances, eps)
    else:
        mode = "blockwise"
        if eps is None:
            eps = _estimate_eps_blockwise(x, min_samples, block_size)
        neighbors = _neighbors_blockwise(x, eps, block_size)

    labels = _expand_clusters(neighbors, min_samples)
    cluster_count = len({int(label) for label in labels if label != NOISE})
    noise_count = int(np.sum(labels == NOISE))

    info = {
        "backend": "scratch_numpy",
        "mode": mode,
        "n": n,
        "eps": round(float(eps), 6),
        "min_samples": int(min_samples),
        "cluster_count": cluster_count,
        "noise_count": noise_count,
        "noise_ratio": round(noise_count / n, 6),
        "seconds": round(time.perf_counter() - started, 4),
    }
    return labels.astype(int).tolist(), info
