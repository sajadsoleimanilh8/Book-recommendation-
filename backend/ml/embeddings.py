"""Embedding backends for semantic search — section 27.

Choosing a backend
------------------
Section 13 asks what the cheapest thing that works is, and section 8 forbids
infrastructure added on speculation. Two facts decided this:

* `torch` is already installed on this machine — but by an unrelated project
  that is *actively training* in ~25 processes (F-33). Installing
  `sentence-transformers` means resolving dependencies inside a shared global
  Python (F-24) while that job runs, which can change `numpy` or `torch`
  underneath it. A better search ranking is not worth breaking somebody
  else's training run, so that install waits for the owner (OI-9).
* scikit-learn is already a first-class dependency, and TF-IDF + SVD (LSA)
  produces dense vectors that capture co-occurrence structure. It is weaker
  than a transformer on the harder queries in section 27 — "something like The
  Alchemist but darker" needs more than co-occurrence — but it is real
  retrieval, it costs nothing new, and it is honest about what it is.

So the backend is an interface with the cheap implementation behind it. When
the transformer is approved, `EMBEDDING_BACKEND=minilm` switches it and
`embed_pass --redo` refills the column; nothing else changes. That is the
whole point of the seam.

Every backend returns L2-normalised vectors, so cosine similarity is a dot
product and pgvector's `<=>` operator stays comparable across backends.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

log = logging.getLogger(__name__)

# parents[1], not parent: this module moved from backend/embeddings.py
# into backend/ml/, and MODEL_DIR must keep resolving to the directory
# holding the fitted artefacts. An unadjusted `parent` would point at
# backend/ml/model_artifacts, which does not exist, and a fitted model
# would silently fail to load rather than error (RESTRUCTURE-NOTES B-6).
#
# Named model_artifacts/ rather than models/ because a `backend/models/`
# directory sits in the same namespace as `backend/models.py`, the schema
# shim that 27 call sites import. It does not shadow it today only because
# the directory has no __init__.py; adding one would silently redirect
# every `from models import Book` to an empty package. Same hazard as
# RESTRUCTURE-NOTES 3.2, removed rather than documented.
MODEL_DIR = Path(__file__).resolve().parents[1] / "model_artifacts"
# MiniLM since 2026-08-30 (OI-9), measured rather than assumed: on the same
# 4,000 chunks, same-book@10 went 50.7% -> 79.3%. LSA matches on word
# co-occurrence, which is why "a book about grief and losing someone" used to
# return a C++ textbook — a confident wrong answer, worse than none.
#
# Overridable, and switching back is self-healing: `embed_pass` re-embeds any
# row carrying another backend's vector (F-39), so `EMBEDDING_BACKEND=lsa`
# plus one pass reverts it. Requires the venv — see scripts/keepup.sh.
DEFAULT_BACKEND = os.getenv("EMBEDDING_BACKEND", "minilm").strip().lower()


def _normalise(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise rows, leaving all-zero rows alone rather than dividing."""
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return a (len(texts), dim) float32 array of unit vectors."""
        ...


class HashingBackend:
    """Deterministic, dependency-free vectors. For tests, never for serving.

    Exists so the retrieval path — chunking, storage, the authorization
    filter, ranking, the endpoint — can be tested exhaustively and fast
    without a model, a fitted corpus, or a download. Its vectors carry real
    lexical signal (shared words land in shared dimensions), which is enough
    to assert that ranking puts the right chunk first, and no more than that.
    """

    name = "hashing"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in str(text).lower().split():
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % self.dim
                sign = 1.0 if digest[4] % 2 else -1.0
                out[row, index] += sign
        return _normalise(out)


class LsaBackend:
    """TF-IDF + TruncatedSVD, fitted on the chunk corpus and persisted.

    Must be fitted before use: LSA has no meaning independent of a corpus,
    unlike a pretrained model. `fit()` is a separate step for that reason, and
    `encode()` refuses to guess if it has not run.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self._pipeline = None
        self._fitted_components = 0
        self._fingerprint = ""

    @property
    def name(self) -> str:
        """Identifies the *fitted space*, not just the algorithm.

        F-35 stops a query encoded by one backend being ranked against
        vectors from another, by comparing `embedding_model`. That guard had a
        hole: refitting LSA on a larger corpus produces a completely different
        vector space that would still have been called "lsa", so the mixed
        vectors would have sailed straight through the check that exists to
        catch exactly this. Unlike a pretrained model, an LSA space is defined
        by the corpus it was fitted on — so the corpus has to be part of the
        identity.
        """
        return f"lsa:{self._fingerprint}" if self._fingerprint else "lsa"

    @property
    def path(self) -> Path:
        return MODEL_DIR / f"lsa_{self.dim}.joblib"

    def fit(self, corpus: Sequence[str]) -> "LsaBackend":
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import make_pipeline

        if len(corpus) < 2:
            raise ValueError("LSA needs a corpus, not a sample")

        # min_df=3 drops rare terms, which in scanned book text are
        # overwhelmingly OCR noise and proper nouns that help no query.
        vectoriser = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            stop_words="english",
            min_df=3,
            max_df=0.4,
            # Vocabulary size drives the artefact directly: the SVD component
            # matrix is n_features x n_components. 200k features with bigrams
            # produced a 594 MB file, which is not a model, it is a liability
            # — too big to load cheaply and too big to version. Unigrams at
            # 50k features carry the same retrieval signal at ~75 MB.
            max_features=50_000,
            ngram_range=(1, 1),
        )
        # F-15's lesson: n_components must fit the vocabulary actually found,
        # or the fit raises and takes the whole pass down with it.
        try:
            matrix = vectoriser.fit_transform(corpus)
        except ValueError as exc:
            # `max_df` prunes terms that appear in most documents. On a small
            # or homogeneous corpus that can remove every term, and sklearn's
            # message ("After pruning, no terms remain") does not say which
            # knob is at fault or that the real problem is the corpus.
            raise ValueError(
                f"LSA could not build a vocabulary from {len(corpus)} document(s): "
                f"{exc}. This backend needs a varied corpus of thousands of "
                "chunks; run `python -m chunk_pass` first."
            ) from exc
        components = max(2, min(self.dim, matrix.shape[1] - 1, len(corpus) - 1))
        if components != self.dim:
            log.warning(
                f"corpus supports only {components} components, not {self.dim}; "
                "vectors will be zero-padded to the stored column width"
            )
        svd = TruncatedSVD(n_components=components, random_state=0)
        svd.fit(matrix)
        # float64 doubles the artefact for precision no retrieval task can
        # use; the vectors are stored as float32 in Postgres regardless.
        svd.components_ = svd.components_.astype("float32")

        self._pipeline = make_pipeline(vectoriser, svd)
        self._fitted_components = components
        # A short digest of the learned components. Two fits over different
        # corpora differ here; refitting the identical corpus does not.
        self._fingerprint = hashlib.blake2b(
            np.ascontiguousarray(svd.components_).tobytes(), digest_size=4
        ).hexdigest()
        log.info(
            f"LSA fitted: {matrix.shape[0]} docs, {matrix.shape[1]} terms, "
            f"{components} components, "
            f"{svd.explained_variance_ratio_.sum():.1%} variance retained"
        )
        return self

    def save(self) -> Path:
        import joblib

        MODEL_DIR.mkdir(exist_ok=True)
        joblib.dump(
            {
                "pipeline": self._pipeline,
                "components": self._fitted_components,
                "fingerprint": self._fingerprint,
            },
            self.path,
        )
        return self.path

    def load(self) -> "LsaBackend":
        import joblib

        if not self.path.exists():
            raise FileNotFoundError(
                f"no fitted model at {self.path} — run `python -m embed_pass --fit`"
            )
        blob = joblib.load(self.path)
        self._pipeline = blob["pipeline"]
        self._fitted_components = blob["components"]
        self._fingerprint = blob.get("fingerprint", "")
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self._pipeline is None:
            raise RuntimeError("LsaBackend is not fitted; call fit() or load()")
        dense = self._pipeline.transform(list(texts))
        # The stored column is a fixed width. A corpus too small to support
        # `dim` components is padded rather than stored at a second width,
        # which would make the vectors silently incomparable.
        if dense.shape[1] < self.dim:
            padded = np.zeros((dense.shape[0], self.dim), dtype=np.float32)
            padded[:, : dense.shape[1]] = dense
            dense = padded
        return _normalise(dense)


class SentenceTransformerBackend:
    """all-MiniLM-L6-v2 — the default since OI-9 was approved.

    Lives in the project venv. A process without it fails loudly on the
    import below rather than silently falling back to LSA: a fallback would
    encode queries in a vector space the corpus does not share, and
    `search_books` would then filter every chunk out, so search would answer
    200 with nothing for every query. Failing at startup is the kinder error.
    """

    name = "minilm"
    dim = 384

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str | None = None,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                "sentence-transformers is not installed in this interpreter. "
                "It lives in the project venv — start the app with "
                "scripts/run.ps1, which selects it, or set "
                "EMBEDDING_BACKEND=lsa."
            ) from exc
        import torch

        # Device is chosen explicitly and reported, never inferred silently.
        #
        # This machine ran a full-corpus embed on CPU and reached 100 degrees.
        # sentence-transformers will happily fall back to CPU when CUDA is
        # missing or the wheel is the CPU build, and the only visible symptom
        # is that it takes far longer — which is indistinguishable from "the
        # corpus is big". So an explicit EMBEDDING_DEVICE=cuda is a promise
        # that is *checked*, not a hint.
        requested = (device or os.getenv("EMBEDDING_DEVICE", "auto")).strip().lower()
        available = torch.cuda.is_available()

        if requested == "cuda" and not available:
            raise RuntimeError(
                "EMBEDDING_DEVICE=cuda but torch reports no CUDA device. "
                f"torch {torch.__version__} "
                f"({'CPU-only build' if '+cpu' in torch.__version__ else 'CUDA build'}). "
                "Refusing to fall back to CPU silently — a full-corpus embed "
                "on CPU is what overheated this machine."
            )
        if requested == "auto":
            requested = "cuda" if available else "cpu"

        self.device = requested
        self._model = self._load(model_name)

        if self.device == "cpu":
            # Leave the machine usable, and cooler. torch defaults to every
            # core, which is how an unattended batch job becomes a thermal
            # event. Half the cores costs throughput we are not short of.
            threads = max(1, (os.cpu_count() or 4) // 2)
            torch.set_num_threads(int(os.getenv("EMBEDDING_CPU_THREADS", threads)))
            log.warning(
                f"MiniLM on CPU with {torch.get_num_threads()} threads — "
                "slow and hot. Set EMBEDDING_DEVICE=cuda once a CUDA build "
                "of torch is installed."
            )
        else:
            name = torch.cuda.get_device_name(0)
            log.info(f"MiniLM on {self.device}: {name}")

    def _load(self, model_name: str):
        """Cached-local first, network only as a fallback.

        Every call — not just a first-time download — otherwise checks
        huggingface.co for a newer config before using the cache, per
        `sentence-transformers`' own default. Harmless when the network is
        fine; when it is not, several connection attempts have to time out
        before falling back, and this project now embeds synchronously
        inside a request-triggered background job (Phase 4, section 29),
        not only from an offline maintenance pass where a slow retry cost
        nothing anyone was waiting on. Observed directly: a degraded
        connection here turned a sub-second model load into
        `services.library_ingest`'s embedding step failing outright once
        `jobs.LIBRARY_REGISTRY`'s worker outlived the retry loop.

        `local_files_only=True` is `SentenceTransformer`'s own parameter for
        this — tried first, falling back to a normal (network-permitted)
        load on *any* failure. Not narrowed to one exception type, because
        "the offline load failed" covers both "not cached yet" (a genuinely
        fresh install, which must still be able to download once) and
        library-version differences in what gets raised for that.

        Setting `HF_HUB_OFFLINE=1` instead (tried first) does not work:
        measured directly, it left every one of `sentence-transformers`'
        own per-file HEAD requests unchanged, so it is not this library's
        own offline switch for whatever internally decides to make them.
        """
        from sentence_transformers import SentenceTransformer

        try:
            return SentenceTransformer(model_name, device=self.device, local_files_only=True)
        except Exception:
            log.info(f"{model_name} not available offline; trying the network")
            return SentenceTransformer(model_name, device=self.device)

    def describe(self) -> dict:
        """What is actually running, for /health and for verification."""
        import torch

        info = {
            "backend": self.name,
            "device": self.device,
            "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
        }
        if self.device.startswith("cuda") and torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["gpu_memory_allocated_mb"] = round(
                torch.cuda.memory_allocated() / 1e6, 1
            )
        return info

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return _normalise(
            self._model.encode(
                list(texts),
                batch_size=int(os.getenv("EMBEDDING_BATCH", "64")),
                show_progress_bar=False,
            )
        )


def get_backend(name: str | None = None, *, dim: int = 384) -> EmbeddingBackend:
    name = (name or DEFAULT_BACKEND).lower()
    if name == "hashing":
        return HashingBackend(dim=dim)
    if name == "lsa":
        return LsaBackend(dim=dim)
    if name == "minilm":
        return SentenceTransformerBackend()
    raise ValueError(f"unknown embedding backend: {name!r}")
