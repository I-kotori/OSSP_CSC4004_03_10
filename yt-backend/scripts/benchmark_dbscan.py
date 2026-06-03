"""
DBSCAN 벤치마크 스크립트.

같은 임베딩에 대해 scikit-learn DBSCAN과 직접 구현한 scratch NumPy
DBSCAN의 실행 시간, 군집 수, noise 비율을 비교한다.

예시:
  python scripts/benchmark_dbscan.py --synthetic --n 5000 --dim 768
  python scripts/benchmark_dbscan.py --embeddings benchmark_outputs/amX.npy
  python scripts/benchmark_dbscan.py --video-id amXUKRnUueM --save-embeddings benchmark_outputs/amX.npy
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(ROOT / ".env")

from app.dbscan_scratch import fit_predict_cosine_dbscan
from app.pipeline import clean_comments, embed_comments, fetch_comments, find_best_eps


def choose_min_samples(n: int) -> int:
    if n < 100:
        return max(3, int(n * 0.05))
    if n < 500:
        return max(5, int(n * 0.02))
    if n < 2000:
        return max(10, int(n * 0.01))
    return max(15, min(20, int(n * 0.008)))


def summarize_labels(labels) -> dict:
    labels = np.asarray(labels)
    n = int(labels.size)
    noise_count = int(np.sum(labels == -1))
    cluster_sizes = sorted(
        [int(np.sum(labels == label)) for label in set(labels.tolist()) if label != -1],
        reverse=True,
    )
    return {
        "cluster_count": len(cluster_sizes),
        "noise_count": noise_count,
        "noise_ratio": round(noise_count / n, 6) if n else 0,
        "largest_clusters": cluster_sizes[:8],
    }


def make_synthetic_embeddings(n: int, dim: int, clusters: int, noise_ratio: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    cluster_n = int(n * (1.0 - noise_ratio))
    noise_n = n - cluster_n
    per_cluster = max(1, cluster_n // clusters)

    centers = rng.normal(size=(clusters, dim)).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)

    parts = []
    for idx in range(clusters):
        count = per_cluster if idx < clusters - 1 else cluster_n - per_cluster * (clusters - 1)
        points = centers[idx] + rng.normal(scale=0.08, size=(count, dim)).astype(np.float32)
        parts.append(points)

    if noise_n > 0:
        parts.append(rng.normal(size=(noise_n, dim)).astype(np.float32))

    embeddings = np.vstack(parts)
    rng.shuffle(embeddings, axis=0)
    return embeddings.astype(np.float32)


def load_embeddings(args) -> np.ndarray:
    if args.embeddings:
        return np.load(args.embeddings).astype(np.float32)

    if args.comments_json:
        comments = json.loads(Path(args.comments_json).read_text(encoding="utf-8"))
        texts, meta = clean_comments(comments)
        embedding_texts = [item.get("embedding_text", text) for text, item in zip(texts, meta)]
        embeddings = embed_comments(embedding_texts).astype(np.float32)
        if args.save_embeddings:
            path = Path(args.save_embeddings)
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, embeddings)
        return embeddings

    if args.video_id:
        comments = fetch_comments(args.video_id)
        if args.save_comments:
            path = Path(args.save_comments)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")
        texts, meta = clean_comments(comments)
        embedding_texts = [item.get("embedding_text", text) for text, item in zip(texts, meta)]
        embeddings = embed_comments(embedding_texts).astype(np.float32)
        if args.save_embeddings:
            path = Path(args.save_embeddings)
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, embeddings)
        return embeddings

    return make_synthetic_embeddings(
        n=args.n,
        dim=args.dim,
        clusters=args.synthetic_clusters,
        noise_ratio=args.synthetic_noise_ratio,
        seed=args.seed,
    )


def run_sklearn(embeddings: np.ndarray, eps: float, min_samples: int) -> dict:
    from sklearn.cluster import DBSCAN

    started = time.perf_counter()
    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(embeddings)
    seconds = time.perf_counter() - started
    summary = summarize_labels(labels)
    summary.update({
        "backend": "sklearn",
        "seconds": round(seconds, 4),
        "eps": round(float(eps), 6),
        "min_samples": min_samples,
    })
    return summary


def run_scratch(embeddings: np.ndarray, min_samples: int) -> dict:
    labels, info = fit_predict_cosine_dbscan(embeddings, min_samples=min_samples)
    summary = summarize_labels(labels)
    summary.update({
        "backend": "scratch_numpy",
        "seconds": info.get("seconds"),
        "eps": info.get("eps"),
        "min_samples": min_samples,
        "mode": info.get("mode"),
    })
    return summary


def reduce_with_pca(embeddings: np.ndarray, dim: int) -> tuple[np.ndarray, dict]:
    if dim >= embeddings.shape[1]:
        return embeddings, {
            "pca_dim": embeddings.shape[1],
            "pca_seconds": 0.0,
            "explained_variance_ratio": None,
        }

    from sklearn.decomposition import PCA

    started = time.perf_counter()
    pca = PCA(n_components=dim, svd_solver="randomized", random_state=42)
    reduced = pca.fit_transform(embeddings)
    seconds = time.perf_counter() - started
    return reduced.astype(np.float32), {
        "pca_dim": dim,
        "pca_seconds": round(seconds, 4),
        "explained_variance_ratio": round(float(np.sum(pca.explained_variance_ratio_)), 6),
    }


def print_table(rows: list[dict]) -> None:
    headers = ["dim", "backend", "seconds", "eps", "min_samples", "cluster_count", "noise_ratio", "largest_clusters"]
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(str(row.get(header, ""))))

    print(" | ".join(header.ljust(widths[header]) for header in headers))
    print("-+-".join("-" * widths[header] for header in headers))
    for row in rows:
        print(" | ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare sklearn DBSCAN and scratch NumPy DBSCAN.")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic embeddings. This is the default when no input is given.")
    parser.add_argument("--n", type=int, default=5000, help="Synthetic sample count.")
    parser.add_argument("--dim", type=int, default=768, help="Synthetic embedding dimension.")
    parser.add_argument("--synthetic-clusters", type=int, default=4)
    parser.add_argument("--synthetic-noise-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--embeddings", help="Path to .npy embeddings.")
    parser.add_argument("--comments-json", help="Load saved raw comments JSON instead of calling YouTube API.")
    parser.add_argument("--video-id", help="Fetch comments and embed a real YouTube video.")
    parser.add_argument("--save-comments", help="Save fetched raw comments to JSON.")
    parser.add_argument("--save-embeddings", help="Save real video embeddings to .npy.")
    parser.add_argument("--pca-dims", help="Comma-separated PCA dimensions to benchmark, e.g. 64,128,256,768.")
    parser.add_argument("--json-out", help="Write benchmark result JSON.")
    args = parser.parse_args()

    embeddings = load_embeddings(args)
    n, dim = embeddings.shape

    pca_dims = [dim]
    if args.pca_dims:
        pca_dims = []
        for value in args.pca_dims.split(","):
            value = value.strip()
            if value:
                pca_dims.append(int(value))
        pca_dims = sorted(set(pca_dims))

    all_rows = []
    pca_results = []
    for pca_dim in pca_dims:
        working_embeddings, pca_info = reduce_with_pca(embeddings, pca_dim)
        current_n, current_dim = working_embeddings.shape
        min_samples = choose_min_samples(current_n)

        eps_started = time.perf_counter()
        eps = find_best_eps(working_embeddings, min_samples)
        eps_seconds = time.perf_counter() - eps_started

        rows = [
            run_sklearn(working_embeddings, eps, min_samples),
            run_scratch(working_embeddings, min_samples),
        ]
        for row in rows:
            row["dim"] = current_dim

        pca_result = {
            "dim": current_dim,
            "pca_seconds": pca_info["pca_seconds"],
            "eps_estimation_seconds": round(eps_seconds, 4),
            "rows": rows,
        }
        pca_results.append(pca_result)
        all_rows.extend(rows)

    result = {
        "n": n,
        "original_dim": dim,
        "runs": pca_results,
    }

    print(f"n={n}, original_dim={dim}")
    for pca_result in pca_results:
        print(
            f"dim={pca_result['dim']}, "
            f"pca_seconds={pca_result['pca_seconds']:.4f}, "
            f"eps_estimation_seconds={pca_result['eps_estimation_seconds']:.4f}"
        )
    print_table(all_rows)

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
