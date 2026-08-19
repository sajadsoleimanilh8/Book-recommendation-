from __future__ import annotations

import logging
import os
import re
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from scipy.sparse import csr_matrix
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MOOD_GENRE_MAP: dict[str, list[str]] = {
    "happy": ["Comedy", "Humor", "Feel-Good", "Young Adult"],
    "sad": ["Drama", "Tragedy", "Poetry", "Literary Fiction"],
    "adventurous": ["Adventure", "Fantasy", "Action", "Sci-Fi"],
    "romantic": ["Romance", "Drama", "Chick Lit"],
    "motivational": ["Self-Help", "Biography", "Personal Development"],
    "dark": ["Horror", "Thriller", "Mystery", "Noir"],
    "relaxing": ["Travel", "Nature", "Philosophy", "Essays"],
    "thoughtful": ["Philosophy", "Science", "History", "Psychology"],
    "curious": ["Science", "Technology", "Mathematics"],
    "nostalgic": ["Classic", "Historical Fiction", "Memoir"],
}

CHATBOT_CORPUS: list[tuple[str, str]] = [
    ("recommend me a book", "recommend"),
    ("suggest something to read", "recommend"),
    ("what should I read next", "recommend"),
    ("I want a book about adventure", "recommend"),
    ("find me a fantasy novel", "recommend"),
    ("I like mystery books", "recommend"),
    ("give me a good thriller", "recommend"),
    ("books like Harry Potter", "recommend"),
    ("something similar to Dune", "recommend"),
    ("romantic novels please", "recommend"),
    ("I feel happy give me a book", "recommend"),
    ("I am sad suggest something", "recommend"),
    ("I want a motivational book", "recommend"),
    ("I want to listen to a book", "audiobook"),
    ("generate an audiobook", "audiobook"),
    ("convert book to audio", "audiobook"),
    ("I prefer listening not reading", "audiobook"),
    ("make me an audiobook for Animal Farm", "audiobook"),
    ("audiobook please", "audiobook"),
    ("can you read this book to me", "audiobook"),
    ("I want to leave a review", "comment"),
    ("add a comment to this book", "comment"),
    ("let me rate this book", "comment"),
    ("I loved this book", "comment"),
    ("this book was terrible", "comment"),
    ("show me the reviews", "comment"),
    ("what do people say about this book", "comment"),
    ("view comments for a book", "comment"),
    ("remind me to read", "reminder"),
    ("set a reading reminder", "reminder"),
    ("notify me about my book progress", "reminder"),
    ("I want reading notifications", "reminder"),
    ("track my reading progress", "reminder"),
    ("how much have I read", "reminder"),
    ("update my progress", "reminder"),
    ("hello", "greeting"),
    ("hi there", "greeting"),
    ("hey", "greeting"),
    ("good morning", "greeting"),
    ("what can you do", "greeting"),
    ("help me", "greeting"),
]

NOTIFY_AT = [25, 50, 75, 100]

POSITIVE_WORDS = [
    "excellent", "amazing", "fantastic", "brilliant", "engaging",
    "enjoyable", "good", "interesting", "loved", "great", "wonderful",
    "captivating", "inspiring", "masterpiece", "outstanding",
]

NEGATIVE_WORDS = [
    "poor", "boring", "terrible", "dull", "bad", "frustrating",
    "weak", "unsatisfying", "hated", "awful", "disappointing",
    "confusing", "slow", "tedious", "mediocre",
]


@dataclass
class UserProfile:
    preferred_genres: list[str] = field(default_factory=list)
    preferred_authors: list[str] = field(default_factory=list)
    mood: str = ""
    rating_history: dict[str, float] = field(default_factory=dict)
    viewed_books: list[str] = field(default_factory=list)
    taste_vector: Optional[np.ndarray] = None
    exploration_rate: float = 0.15
    liked_keywords: list[str] = field(default_factory=list)
    disliked_keywords: list[str] = field(default_factory=list)
    reading_speed_ppm: float = 0.0


@dataclass
class Comment:
    user_id: str
    text: str
    rating: Optional[int]
    sentiment: str
    keywords: list[str]
    timestamp: str
    embedding: Optional[list[float]] = None


@dataclass
class Reminder:
    book_id: int
    title: str
    user_id: str
    enabled: bool
    last_notified: int
    last_message: str
    last_fired_at: str
    created_at: str
    eta_minutes: Optional[float] = None


class DataLoader:
    RENAME = {
        "book_title": "title", "booktitle": "title",
        "authors": "author",
        "categories": "genre",
        "rating": "average_rating",
        "year": "publication_year",
    }

    @classmethod
    def load(cls, files: list[str]) -> pd.DataFrame:
        dfs = []
        for f in files:
            try:
                df = pd.read_csv(f)
                df.columns = df.columns.str.strip().str.lower()
                dfs.append(df)
                log.info(f"Loaded {f} ({len(df)} rows)")
            except FileNotFoundError:
                log.warning(f"Not found: {f}")

        if not dfs:
            log.warning("No CSV files — generating synthetic demo data.")
            return cls._synthetic()

        return cls._clean(pd.concat(dfs, ignore_index=True).rename(columns=cls.RENAME))

    @staticmethod
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        # Remove duplicate columns
        df = df.loc[:, ~df.columns.duplicated()]
        
        # Reset index
        df = df.reset_index(drop=True)
        
        required_cols = [
            "title", "author", "genre", "description",
            "average_rating", "page_count", "list_price",
            "ratings_count", "language"
        ]

        for col in required_cols:
            if col not in df.columns:
                df[col] = np.nan

        # Convert numerical columns
        df["average_rating"] = pd.to_numeric(df["average_rating"], errors="coerce")
        df["page_count"] = pd.to_numeric(df["page_count"], errors="coerce")
        df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce")
        df["ratings_count"] = pd.to_numeric(df["ratings_count"], errors="coerce").fillna(0)

        # Extract year
        if "published_date" in df.columns:
            df["published_year"] = pd.to_numeric(
                df["published_date"].astype(str).str.extract(r"(\d{4})")[0],
                errors="coerce"
            )
        elif "publication_year" in df.columns:
            df["published_year"] = pd.to_numeric(df["publication_year"], errors="coerce")
        else:
            df["published_year"] = np.nan

        # Convert text columns to string
        df["title"] = df["title"].fillna("Unknown").astype(str).str.strip()
        df["author"] = df["author"].fillna("Unknown").astype(str)
        
        # Ensure genre is 1D
        if isinstance(df["genre"], pd.DataFrame):
            df["genre"] = df["genre"].iloc[:, 0]
        df["genre"] = df["genre"].fillna("Unknown").astype(str)
        
        df["description"] = df["description"].fillna("").astype(str)
        df["language"] = df["language"].fillna("en").astype(str)

        # Fill numerical columns with median
        for col in ["average_rating", "page_count", "list_price", "published_year"]:
            if col in df.columns:
                median_val = df[col].median()
                if not pd.isna(median_val):
                    df[col] = df[col].fillna(median_val)
                else:
                    df[col] = df[col].fillna(0)

        # Ensure page_count has value
        if "page_count" in df.columns:
            df["page_count"] = df["page_count"].fillna(200).astype(int)
        else:
            df["page_count"] = 200

        df = df.drop_duplicates(subset=["title", "author"]).reset_index(drop=True)
        log.info(f"Clean dataset: {len(df)} books")

        return df

    @staticmethod
    def _synthetic(n: int = 3000) -> pd.DataFrame:
        rng = np.random.default_rng(42)
        genres = [
            "Fiction", "Mystery", "Science", "History", "Romance",
            "Fantasy", "Self-Help", "Horror", "Biography", "Thriller",
            "Comedy", "Poetry", "Travel", "Philosophy", "Young Adult"
        ]
        authors = [f"Author {i}" for i in range(300)]

        cg = rng.choice(genres, n)

        return pd.DataFrame({
            "title": [f"Book {i:05d}" for i in range(n)],
            "author": rng.choice(authors, n),
            "genre": cg,
            "average_rating": rng.uniform(1.5, 5.0, n).round(2),
            "ratings_count": rng.integers(0, 100_000, n),
            "page_count": rng.integers(80, 1200, n),
            "list_price": rng.uniform(0, 60, n).round(2),
            "published_year": rng.integers(1950, 2024, n),
            "language": "en",
            "description": [f"A compelling {g} book about discovery." for g in cg],
        })


class FeatureEngineer:
    def __init__(self, n_text_components: int = 48):
        self.scaler = StandardScaler()
        self.le_lang = LabelEncoder()
        self.le_genre = LabelEncoder()

        self.tfidf = TfidfVectorizer(
            max_features=1024,
            ngram_range=(1, 2),
            stop_words="english",
            sublinear_tf=True
        )

        self.svd = TruncatedSVD(n_components=n_text_components, random_state=42)

        self.comment_tfidf = TfidfVectorizer(
            max_features=256,
            ngram_range=(1, 2),
            stop_words="english",
            sublinear_tf=True
        )

        self.comment_svd = TruncatedSVD(n_components=16, random_state=42)
        self._comment_fitted = False

        self.numeric_matrix: Optional[np.ndarray] = None
        self.text_matrix: Optional[np.ndarray] = None
        self.combined: Optional[np.ndarray] = None

    def fit_transform(self, df: pd.DataFrame) -> np.ndarray:
        df = df.copy().reset_index(drop=True)

        # Ensure genre is 1D
        genre_col = df["genre"]
        if isinstance(genre_col, pd.DataFrame):
            genre_col = genre_col.iloc[:, 0]
        genre_values = genre_col.astype(str).values.flatten()
        
        df["genre_enc"] = self.le_genre.fit_transform(genre_values)
        df["lang_enc"] = self.le_lang.fit_transform(df["language"].astype(str).values)
        df["rating_popularity"] = df["average_rating"].values * np.log1p(df["ratings_count"].values)

        yr_min = df["published_year"].min()
        yr_range = df["published_year"].max() - yr_min + 1e-9
        df["recency_weight"] = (df["published_year"].values - yr_min) / yr_range

        if "comment_score" not in df.columns:
            df["comment_score"] = 0.0

        num_cols = [
            "average_rating",
            "ratings_count",
            "page_count",
            "list_price",
            "genre_enc",
            "lang_enc",
            "rating_popularity",
            "recency_weight",
            "comment_score",
        ]

        self.numeric_matrix = self.scaler.fit_transform(df[num_cols].fillna(0))

        # Create corpus
        title_str = df["title"].fillna("").astype(str).values
        author_str = df["author"].fillna("").astype(str).values
        genre_str = df["genre"].fillna("Unknown").astype(str).values
        description_str = df["description"].fillna("").astype(str).values
        
        corpus = [f"{t} {a} {g} {d}" for t, a, g, d in zip(title_str, author_str, genre_str, description_str)]

        tfidf_mat = self.tfidf.fit_transform(corpus)
        self.text_matrix = self.svd.fit_transform(tfidf_mat)

        log.info(f"SVD explained variance: {self.svd.explained_variance_ratio_.sum():.2%}")

        self.combined = np.hstack([self.numeric_matrix, self.text_matrix])

        log.info(f"Feature matrix: {self.combined.shape}")

        return self.combined

    def embed_comment(self, text: str) -> Optional[list[float]]:
        try:
            if not self._comment_fitted:
                return None
            vec = self.comment_tfidf.transform([text])
            return self.comment_svd.transform(vec)[0].tolist()
        except Exception:
            return None

    def fit_comment_embedder(self, texts: list[str]):
        # Clean texts - replace NaN/None with empty string
        cleaned_texts = []
        for t in texts:
            if t is None or (isinstance(t, float) and np.isnan(t)):
                cleaned_texts.append("")
            else:
                cleaned_texts.append(str(t))
        
        if len(cleaned_texts) < 2:
            return
        
        mat = self.comment_tfidf.fit_transform(cleaned_texts)
        self.comment_svd.fit(mat)
        self._comment_fitted = True
        log.info("Comment embedder fitted.")


class ClusteringModel:
    def __init__(self, k_range: range = range(4, 15)):
        self.k_range = k_range
        self.model: Optional[MiniBatchKMeans] = None
        self.best_k: int = 8

    def fit(self, X: np.ndarray) -> np.ndarray:
        self.best_k = self._find_elbow(X)
        self.model = MiniBatchKMeans(n_clusters=self.best_k, random_state=42,
                                     n_init=10, batch_size=2048)
        labels = self.model.fit_predict(X)
        log.info(f"KMeans: best k={self.best_k}")
        return labels

    def _find_elbow(self, X: np.ndarray) -> int:
        rng = np.random.default_rng(0)
        n_samples = min(3000, len(X))
        sample = X[rng.choice(len(X), n_samples, replace=False)]
        inertias = []
        for k in self.k_range:
            m = MiniBatchKMeans(n_clusters=k, random_state=42, n_init=3)
            m.fit(sample)
            inertias.append(m.inertia_)
        d2 = np.diff(np.diff(inertias))
        best = int(np.argmax(d2)) + self.k_range.start + 1
        return int(np.clip(best, self.k_range.start, self.k_range.stop - 1))


class SimilarityEngine:
    def __init__(self, n_neighbors: int = 60):
        self.nn = NearestNeighbors(n_neighbors=n_neighbors, metric="cosine",
                                   algorithm="brute", n_jobs=-1)
        self._X: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray):
        self._X = X
        self.nn.fit(X)
        log.info("ANN similarity engine ready.")

    def query(self, idx: int, k: int = 20) -> tuple[np.ndarray, np.ndarray]:
        dists, indices = self.nn.kneighbors(self._X[idx].reshape(1, -1), n_neighbors=k + 1)
        mask = indices[0] != idx
        return indices[0][mask][:k], 1.0 - dists[0][mask][:k]

    def query_vector(self, vec: np.ndarray, k: int = 20) -> tuple[np.ndarray, np.ndarray]:
        dists, indices = self.nn.kneighbors(vec.reshape(1, -1), n_neighbors=k)
        return indices[0], 1.0 - dists[0]


class CollaborativeFilter:
    def __init__(self, n_factors: int = 15):
        self.svd = TruncatedSVD(n_components=n_factors, random_state=42)
        self.item_factors: Optional[np.ndarray] = None

    def fit(self, df: pd.DataFrame):
        signal = (df["average_rating"].values * np.log1p(df["ratings_count"].values))
        le = LabelEncoder()
        gids = le.fit_transform(df["genre"].astype(str).values)
        mat = csr_matrix((signal, (gids, np.arange(len(df)))),
                         shape=(len(le.classes_), len(df)))
        self.item_factors = self.svd.fit_transform(mat.T)
        log.info(f"CF item factors: {self.item_factors.shape}")

    def similar_items(self, idx: int, k: int = 20) -> np.ndarray:
        if self.item_factors is None:
            raise RuntimeError("CF not fitted.")
        sims = self._cosine_sim_1d(self.item_factors[idx], self.item_factors)
        sims[idx] = -1.0
        return np.argsort(sims)[::-1][:k]

    def user_vector(self, liked_indices: list[int]) -> np.ndarray:
        if not liked_indices or self.item_factors is None:
            return np.zeros(self.svd.n_components)
        return self.item_factors[liked_indices].mean(axis=0)

    @staticmethod
    def _cosine_sim_1d(vec: np.ndarray, mat: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(vec)
        if n == 0:
            return np.zeros(len(mat))
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1e-9
        return (mat @ vec) / (norms * n)


class LearningToRank:
    def __init__(self):
        self.model = GradientBoostingRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            random_state=42
        )
        self.fitted = False

    def _features(self, cands, content_s, cf_s, cluster_id, mood_genres) -> np.ndarray:
        mood_match = cands["genre"].apply(
            lambda g: float(any(m.lower() in str(g).lower() for m in mood_genres))
        ).values

        cluster_match = (cands["cluster"].values == cluster_id).astype(float)
        recency = cands.get("recency_weight", pd.Series(0.5, index=cands.index)).values
        comment_score = cands.get("comment_score", pd.Series(0.0, index=cands.index)).values

        return np.column_stack([
            content_s,
            cf_s,
            cluster_match,
            cands["average_rating"].values / 5.0,
            np.log1p(cands["ratings_count"].values) / 15.0,
            1.0 / (1.0 + cands["list_price"].values),
            recency,
            mood_match,
            comment_score,
            np.log1p(cands["ratings_count"].values),
        ])

    def train(self, df, content_s, cf_s):
        rating_norm = df["average_rating"].values / 5.0
        rating_count_norm = np.log1p(df["ratings_count"].values)
        rating_count_norm = rating_count_norm / (rating_count_norm.max() + 1e-9)
        
        relevance = rating_norm * 0.4 + rating_count_norm * 0.6
        relevance = relevance.flatten()
        
        cs = df.get("comment_score", pd.Series(0.0, index=df.index)).values
        if cs.ndim > 1:
            cs = cs.flatten()
        
        content_s_1d = np.asarray(content_s).flatten()[:len(df)]
        cf_s_1d = np.asarray(cf_s).flatten()[:len(df)]
        
        X = np.column_stack([
            content_s_1d,
            cf_s_1d,
            np.zeros(len(df)),
            df["average_rating"].values / 5.0,
            np.log1p(df["ratings_count"].values) / 15.0,
            1.0 / (1.0 + df["list_price"].values),
            df.get("recency_weight", pd.Series(0.5, index=df.index)).values,
            np.zeros(len(df)),
            cs[:len(df)],
            np.log1p(df["ratings_count"].values),
        ])
        
        X_tr, X_val, y_tr, y_val = train_test_split(X, relevance, test_size=0.15, random_state=42)
        self.model.fit(X_tr, y_tr)
        log.info(f"LTR val R2: {self.model.score(X_val, y_val):.4f}")
        self.fitted = True

    def score(self, cands, content_s, cf_s, cluster_id, mood_genres) -> np.ndarray:
        X = self._features(cands, content_s, cf_s, cluster_id, mood_genres)

        if self.fitted:
            return self.model.predict(X)

        return (
            content_s * 0.4 +
            cf_s * 0.3 +
            cands["average_rating"].values / 5.0 * 0.3
        )


class ContextualBandit:
    def __init__(self, epsilon: float = 0.15):
        self.epsilon = epsilon
        self.rewards: dict[int, list[float]] = {}

    def select(self, ranked_indices, profile, n=10) -> np.ndarray:
        n_explore = max(1, int(n * profile.exploration_rate))
        n_exploit = n - n_explore

        exploit = ranked_indices[:n_exploit]
        pool = ranked_indices[n_exploit:]

        explore = (
            np.random.choice(pool, size=n_explore, replace=False)
            if len(pool) >= n_explore
            else pool
        )

        return np.concatenate([exploit, explore])

    def update(self, item_idx: int, reward: float):
        self.rewards.setdefault(item_idx, []).append(reward)

    def expected_reward(self, item_idx: int) -> float:
        r = self.rewards.get(item_idx, [])
        return float(np.mean(r)) if r else 0.5


class GutenbergClient:
    BASE = "https://gutendex.com/books"

    @classmethod
    def search(cls, query: str, n: int = 10) -> list[dict]:
        try:
            r = requests.get(cls.BASE, params={"search": query}, timeout=8)
            r.raise_for_status()
            results = r.json().get("results", [])

            return [
                {
                    "title": b.get("title", "Unknown"),
                    "author": ", ".join(a["name"] for a in b.get("authors", [])),
                    "genre": ", ".join(b.get("subjects", [])) or "Unknown",
                    "average_rating": None,
                    "ratings_count": None,
                    "download_count": b.get("download_count", 0),
                    "page_count": None,
                    "list_price": 0.0,
                    "source": "gutenberg",
                    "url": b.get("formats", {}).get("text/plain; charset=utf-8", ""),
                    "final_score": b.get("download_count", 0) / 1_000_000,
                }
                for b in results[:n]
            ]
        except requests.RequestException as e:
            log.warning(f"Gutenberg search failed: {e}")
            return []

    @classmethod
    def get_text(cls, book_name: str) -> str:
        r = requests.get(cls.BASE, params={"search": book_name}, timeout=10)
        r.raise_for_status()
        results = r.json().get("results", [])

        if not results:
            raise ValueError(f"No Gutenberg results for '{book_name}'.")

        text_url = results[0].get("formats", {}).get("text/plain; charset=utf-8", "")

        if not text_url:
            raise ValueError("No plain-text format available.")

        return requests.get(text_url, timeout=60).text


class AudiobookEngine:
    def __init__(self, recommender: "Recommender"):
        self.recommender = recommender

    def generate(
        self,
        book_name: str,
        output_file: str = "audiobook.mp3",
        lang: str = "en",
        chunk_chars: int = 4_000,
    ) -> Dict[str, Any]:
        try:
            from gtts import gTTS
        except ImportError:
            log.error("gTTS library not found. Please install it: pip install gTTS")
            return {"ok": False, "error": "gTTS not installed"}

        log.info(f"Fetching '{book_name}' from Gutenberg…")
        try:
            text = GutenbergClient.get_text(book_name)
        except Exception as e:
            log.error(f"Failed to get text: {e}")
            return {"ok": False, "error": str(e)}

        chunks = self._smart_chunks(text, chunk_chars)
        log.info(f"Converting {len(chunks)} chunks to audio…")

        temp_files = []
        try:
            for i, chunk in enumerate(chunks):
                fname = f"_tmp_{i}.mp3"
                gTTS(text=chunk, lang=lang).save(fname)
                temp_files.append(fname)

            if os.path.exists(output_file):
                os.remove(output_file)

            with open(output_file, "ab") as out:
                for fname in temp_files:
                    if os.path.exists(fname):
                        with open(fname, "rb") as f:
                            out.write(f.read())

            self._boost_book(book_name, boost=0.3)
            log.info(f"Audiobook saved: {output_file}")

            return {
                "ok": True,
                "output_file": output_file,
                "chunks": len(chunks),
                "characters": len(text),
                "message": f"Audiobook saved to {output_file}",
            }
        finally:
            for fname in temp_files:
                try:
                    if os.path.exists(fname):
                        os.remove(fname)
                except Exception:
                    pass

    @staticmethod
    def _smart_chunks(text: str, max_chars: int) -> List[str]:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks: List[str] = []
        current_chunk: str = ""

        for sent in sentences:
            if len(current_chunk) + len(sent) <= max_chars:
                if not current_chunk:
                    current_chunk = sent
                else:
                    current_chunk += " " + sent
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = sent

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks or [text[:max_chars]] if text else []

    def _boost_book(self, book_name: str, boost: float = 0.2):
        try:
            mask = self.recommender.df["title"].str.lower().str.contains(
                book_name.lower(), na=False
            )
            if mask.any():
                self.recommender.df.loc[mask, "comment_score"] += boost
                log.info(f"Boosted '{book_name}' by {boost}")
        except Exception:
            pass


class CommentEngine:
    def __init__(self, recommender: "Recommender"):
        self.recommender = recommender
        self._store: Dict[int, List[Comment]] = {}

    @staticmethod
    def _sentiment(text: str) -> str:
        t = text.lower()
        pos_count = sum(w in t for w in POSITIVE_WORDS)
        neg_count = sum(w in t for w in NEGATIVE_WORDS)

        if pos_count > neg_count:
            return "Positive"
        if neg_count > pos_count:
            return "Negative"
        return "Neutral"

    @staticmethod
    def _keywords(text: str, n: int = 5) -> List[str]:
        stop_words = {
            "this", "that", "with", "have", "from", "been", "were",
            "they", "book", "very", "just", "some", "also", "about",
            "what", "when", "where", "who", "why", "how", "all", "and",
            "but", "or", "is", "are", "was", "it", "its", "their", "there"
        }
        return [
            word.strip(".,!?\"'") for word in text.split()
            if len(word) > 4 and word.lower() not in stop_words
        ][:n]

    def add(
        self,
        book_idx: int,
        user_id: str,
        text: str,
        rating: Optional[int] = None,
        profile: Optional[UserProfile] = None,
    ) -> Comment:
        sentiment = self._sentiment(text)
        keywords = self._keywords(text)
        embedding = self.recommender.engineer.embed_comment(text)

        comment = Comment(
            user_id=user_id,
            text=text,
            rating=rating,
            sentiment=sentiment,
            keywords=keywords,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            embedding=embedding,
        )

        self._store.setdefault(book_idx, []).append(comment)

        delta = 0.0
        if sentiment == "Positive":
            delta = 0.15
        elif sentiment == "Negative":
            delta = -0.10

        if rating is not None:
            delta += (rating - 3) * 0.05

        if 0 <= book_idx < len(self.recommender.df):
            current_score = self.recommender.df.loc[book_idx, "comment_score"]
            self.recommender.df.loc[book_idx, "comment_score"] = max(
                -1.0, min(1.0, current_score + delta)
            )

        if profile:
            if sentiment == "Positive":
                profile.liked_keywords.extend(keywords)
            elif sentiment == "Negative":
                profile.disliked_keywords.extend(keywords)

        log.info(f"Comment added for book index {book_idx}")
        return comment

    def get(self, book_idx: int) -> List[Dict[str, Any]]:
        return [
            {
                "user_id": c.user_id,
                "text": c.text,
                "rating": c.rating,
                "sentiment": c.sentiment,
                "keywords": c.keywords,
                "timestamp": c.timestamp,
            }
            for c in self._store.get(book_idx, [])
        ]

    def delete(self, book_idx: int, comment_index: int) -> Optional[Comment]:
        comments = self._store.get(book_idx)
        if not comments or not (0 <= comment_index < len(comments)):
            return None

        removed_comment = comments.pop(comment_index)

        delta = 0.0
        if removed_comment.sentiment == "Positive":
            delta = 0.15
        elif removed_comment.sentiment == "Negative":
            delta = -0.10

        if removed_comment.rating is not None:
            delta += (removed_comment.rating - 3) * 0.05

        if 0 <= book_idx < len(self.recommender.df):
            current_score = self.recommender.df.loc[book_idx, "comment_score"]
            self.recommender.df.loc[book_idx, "comment_score"] = max(
                -1.0, min(1.0, current_score - delta)
            )

        return removed_comment

    def summary(self, book_idx: int) -> Dict[str, Any]:
        comments = self._store.get(book_idx, [])
        if not comments:
            return {"total": 0}

        ratings = [c.rating for c in comments if c.rating is not None]
        total_ratings = len(ratings)
        avg_rating = round(sum(ratings) / total_ratings, 2) if total_ratings > 0 else None

        positive_count = sum(1 for c in comments if c.sentiment == "Positive")
        negative_count = sum(1 for c in comments if c.sentiment == "Negative")
        neutral_count = sum(1 for c in comments if c.sentiment == "Neutral")

        from collections import Counter
        all_keywords = [kw for c in comments for kw in c.keywords]
        keyword_counts = Counter(all_keywords)
        top_keywords = [kw for kw, count in keyword_counts.most_common(10)]

        current_comment_score = 0.0
        if 0 <= book_idx < len(self.recommender.df):
            current_comment_score = self.recommender.df.loc[book_idx, "comment_score"]

        return {
            "total": len(comments),
            "avg_rating": avg_rating,
            "positive": positive_count,
            "negative": negative_count,
            "neutral": neutral_count,
            "top_keywords": top_keywords,
            "comment_score": round(current_comment_score, 3),
        }


class ChatbotEngine:
    GREETINGS = {
        "greeting": (
            "Hi! I'm your DigiKitab assistant. I can:\n"
            " • Recommend books by mood or genre\n"
            " • Generate audiobooks from Gutenberg\n"
            " • Manage your book comments and ratings\n"
            " • Set reading reminders\n\n"
            "What would you like to do?"
        )
    }

    def __init__(self, recommender: "Recommender",
                 audiobook: AudiobookEngine,
                 comment: CommentEngine,
                 reminder: "ReminderEngine"):
        self.recommender = recommender
        self.audiobook = audiobook
        self.comment = comment
        self.reminder = reminder

        self.classifier: Optional[Pipeline] = None
        self._train_classifier()

    def _train_classifier(self):
        texts, labels = zip(*CHATBOT_CORPUS)
        self.classifier = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=500, C=2.0, random_state=42)),
        ])
        self.classifier.fit(texts, labels)
        log.info("Chatbot intent classifier trained.")

    def classify_intent(self, text: str) -> Tuple[str, float]:
        if self.classifier is None:
            return "recommend", 0.5

        proba = self.classifier.predict_proba([text])[0]
        idx = int(np.argmax(proba))
        intent = self.classifier.classes_[idx]
        conf = float(proba[idx])
        return intent, conf

    def _extract_slots(self, text: str) -> Dict[str, Any]:
        text_l = text.lower()
        slots: Dict[str, Any] = {}

        for mood in MOOD_GENRE_MAP:
            if mood in text_l:
                slots["mood"] = mood
                break

        possible_genres = ["science fiction", "fiction", "mystery", "fantasy", "romance",
                           "history", "biography", "horror", "thriller", "poetry",
                           "comedy", "travel", "philosophy", "self-help"]
        for genre in possible_genres:
            if genre in text_l:
                slots["genre"] = genre.capitalize()
                break

        title_patterns = [
            r"(?:like|recommend)\s+(?:a book about|a book called|the book)\s+(.+?)(?:\s*$|\.|\,)",
            r"similar to\s+(.+?)(?:\s*$|\.|\,)",
            r"audiobook for\s+(.+?)(?:\s*$|\.|\,)",
            r"about\s+(.+?)(?:\s*$|\.|\,)",
            r"the book\s+(.+?)(?:\s*$|\.|\,)",
        ]
        for pattern in title_patterns:
            match = re.search(pattern, text_l)
            if match:
                slots["book_title"] = match.group(1).strip().title()
                break

        author_match = re.search(r"by\s+([A-Za-z\s\-]+)(?:\s*$|\.|\,)", text_l)
        if author_match:
            slots["author"] = author_match.group(1).strip().title()

        return slots

    def respond(
        self,
        user_message: str,
        user_id: str = "guest",
        profile: Optional[UserProfile] = None,
        book_idx: Optional[int] = None,
    ) -> Dict[str, Any]:
        intent, confidence = self.classify_intent(user_message)
        slots = self._extract_slots(user_message)

        response: Dict[str, Any] = {
            "intent": intent,
            "confidence": round(confidence, 3),
            "slots": slots,
            "books": [],
            "message": "",
            "action": None,
        }

        if intent == "recommend":
            response["message"] = "I can help with book recommendations!"
            response["action"] = "recommend_books"
        elif intent == "audiobook":
            response["message"] = "I can help you create audiobooks."
            response["action"] = "create_audiobook"
        elif intent == "comment":
            response["message"] = "I can manage book comments and ratings."
            response["action"] = "manage_comments"
        elif intent == "reminder":
            response["message"] = "I can set reading reminders for you."
            response["action"] = "set_reminder"
        else:
            response["message"] = self.GREETINGS.get("greeting", "How can I help you?")

        if "book_title" in slots:
            response["message"] += f" (Regarding '{slots['book_title']}')"

        return response


class ReminderEngine:
    def __init__(self):
        self._reminders: Dict[str, Dict[int, Reminder]] = {}
        self._progress: Dict[str, Dict[int, float]] = {}
        self._sessions: Dict[str, Dict[int, Tuple[float, float]]] = {}
        self._daemon = threading.Thread(target=self._loop, daemon=True)
        self._daemon.start()
        log.info("Reminder daemon started.")

    def update_progress(
        self,
        user_id: str,
        book_id: int,
        progress: float,
        total_pages: int = 0,
        profile: Optional[UserProfile] = None,
    ) -> Dict[str, Any]:
        progress = max(0.0, min(1.0, progress))
        store = self._progress.setdefault(user_id, {})
        store[book_id] = progress
        pct = int(progress * 100)

        return {
            "user_id": user_id,
            "book_id": book_id,
            "progress": progress,
            "percent": pct,
        }

    def set_reminder(self, user_id: str, book_id: int, title: str,
                     enabled: bool = True) -> Optional[Reminder]:
        store = self._reminders.setdefault(user_id, {})
        if not enabled:
            store.pop(book_id, None)
            return None

        reminder = Reminder(
            book_id=book_id, title=title, user_id=user_id,
            enabled=True, last_notified=0, last_message="",
            last_fired_at="", created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        store[book_id] = reminder
        log.info(f"Reminder set: {user_id} → '{title}'")
        return reminder

    def get_user_reminders(self, user_id: str) -> List[Dict]:
        reminders = self._reminders.get(user_id, {})
        progress = self._progress.get(user_id, {})
        result = []
        for book_id, r in reminders.items():
            pct = int(progress.get(book_id, 0.0) * 100)
            result.append({
                "book_id": book_id,
                "title": r.title,
                "enabled": r.enabled,
                "progress_pct": pct,
                "next_milestone": next((p for p in NOTIFY_AT if p > pct), None),
                "last_message": r.last_message,
                "last_fired_at": r.last_fired_at,
                "created_at": r.created_at,
            })
        return result

    def get_progress(self, user_id: str) -> List[Dict]:
        return [
            {"book_id": bid, "progress": prog, "percent": int(prog * 100)}
            for bid, prog in self._progress.get(user_id, {}).items()
        ]

    def _loop(self):
        while True:
            try:
                self._check_all()
            except Exception as e:
                log.warning(f"Reminder loop error: {e}")
            time.sleep(30)

    def _check_all(self):
        for user_id, reminders in list(self._reminders.items()):
            progress = self._progress.get(user_id, {})
            for book_id, reminder in list(reminders.items()):
                if not reminder.enabled:
                    continue

                pct = int(progress.get(book_id, 0.0) * 100)
                last = reminder.last_notified
                triggered = [p for p in NOTIFY_AT if last < p <= pct]

                if not triggered:
                    continue

                reminder.last_notified = pct

                if pct >= 100:
                    msg = f"You finished '{reminder.title}'! Amazing work!"
                elif pct >= 75:
                    msg = f"'{reminder.title}' — {pct}% done. Almost there!"
                elif pct >= 50:
                    msg = f"'{reminder.title}' — halfway through!"
                else:
                    msg = f"'{reminder.title}' — {pct}% complete. Keep reading!"

                reminder.last_message = msg
                reminder.last_fired_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fire(msg)

    @staticmethod
    def _fire(msg: str):
        log.info(f"[REMINDER] {msg}")
        try:
            from plyer import notification
            notification.notify(title="DigiKitab", message=msg, timeout=6)
        except Exception:
            pass


class Recommender:
    WEIGHTS = {"content": 0.28, "cf": 0.24, "ltr": 0.32, "bandit": 0.16}

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self.df["comment_score"] = 0.0

        self.engineer = FeatureEngineer()
        self.cluster = ClusteringModel()
        self.ann = SimilarityEngine()
        self.cf = CollaborativeFilter()
        self.ltr = LearningToRank()
        self.bandit = ContextualBandit()

        self._fitted = False
        self._X: Optional[np.ndarray] = None

        self.audiobook: Optional[AudiobookEngine] = None
        self.comment: Optional[CommentEngine] = None
        self.chatbot: Optional[ChatbotEngine] = None
        self.reminder: Optional[ReminderEngine] = None

    def fit(self):
        log.info("=== Fitting Recommender ===")

        self._X = self.engineer.fit_transform(self.df)
        self.df["cluster"] = self.cluster.fit(self._X)
        self.df["recency_weight"] = self.engineer.numeric_matrix[:, -2]
        self.ann.fit(self._X)
        self.cf.fit(self.df)

        diag = np.ones(len(self.df))
        self.ltr.train(self.df, diag, diag)

        # Fix: Clean description before passing to comment embedder
        descriptions = self.df["description"].fillna("").astype(str).tolist()
        self.engineer.fit_comment_embedder(descriptions)

        self.audiobook = AudiobookEngine(self)
        self.comment = CommentEngine(self)
        self.reminder = ReminderEngine()
        self.chatbot = ChatbotEngine(self, self.audiobook, self.comment, self.reminder)

        self._fitted = True
        log.info("=== Recommender + all engines ready ===")

    def recommend_by_book(self, title_query: str, profile: UserProfile, n: int = 10) -> list[dict]:
        self._assert_fitted()
        matches = self.df[self.df["title"].str.lower().str.contains(title_query.lower(), na=False)]

        if matches.empty:
            return []

        seed_idx = int(matches.index[0])
        ann_idx, ann_sims = self.ann.query(seed_idx, k=min(100, len(self.df) - 1))
        cf_idx = self.cf.similar_items(seed_idx, k=100)

        cand_list = list(dict.fromkeys(list(ann_idx) + list(cf_idx)))[:150]
        return self._rank(seed_idx, cand_list, ann_idx, ann_sims, profile, n)

    def recommend_by_profile(self, profile: UserProfile, n: int = 10) -> list[dict]:
        self._assert_fitted()
        mood_genres = MOOD_GENRE_MAP.get(profile.mood, [])
        mask = pd.Series(True, index=self.df.index)

        if profile.preferred_genres:
            mask &= self.df["genre"].apply(
                lambda x: any(g.lower() in x.lower() for g in profile.preferred_genres))
        if profile.preferred_authors:
            mask &= self.df["author"].apply(
                lambda x: any(a.lower() in x.lower() for a in profile.preferred_authors))
        if mood_genres:
            mask &= self.df["genre"].apply(
                lambda x: any(m.lower() in x.lower() for m in mood_genres))
        if profile.viewed_books:
            mask &= ~self.df["title"].isin(profile.viewed_books)

        if profile.disliked_keywords:
            mask &= ~self.df["description"].apply(
                lambda d: any(k.lower() in d.lower() for k in profile.disliked_keywords))

        candidates = self.df[mask]
        if len(candidates) < 5:
            candidates = self.df

        cand_idx = candidates.index.tolist()

        if profile.taste_vector is not None:
            q_idx, q_sims = self.ann.query_vector(profile.taste_vector, k=min(150, len(candidates)))
            q_idx = [i for i in q_idx if i in cand_idx]
            q_sims = q_sims[:len(q_idx)]
        else:
            q_idx = candidates.head(150).index.tolist()
            q_sims = np.linspace(1, 0, len(q_idx))

        seed_idx = q_idx[0] if q_idx else 0
        return self._rank(seed_idx, q_idx, np.array(q_idx), q_sims, profile, n)

    def _rank(self, seed_idx, cand_list, ann_idx, ann_sims, profile, n) -> list[dict]:
        if not cand_list:
            return []

        cands = self.df.loc[cand_list].copy()
        sim_lookup = dict(zip(ann_idx.tolist(), ann_sims.tolist()))
        content_s = np.array([sim_lookup.get(i, 0.0) for i in cand_list])

        cf_top = self.cf.similar_items(seed_idx, k=200)
        cf_lookup = {idx: 1.0 - rank / len(cf_top) for rank, idx in enumerate(cf_top)}
        cf_s = np.array([cf_lookup.get(i, 0.0) for i in cand_list])

        mood_genres = MOOD_GENRE_MAP.get(profile.mood, [])
        cluster_id = int(self.df.loc[seed_idx, "cluster"])
        ltr_scores = self.ltr.score(cands, content_s, cf_s, cluster_id, mood_genres)
        bandit_s = np.array([self.bandit.expected_reward(i) for i in cand_list])

        ltr_max = ltr_scores.max() + 1e-9
        final_score = (
            self.WEIGHTS["content"] * content_s +
            self.WEIGHTS["cf"] * cf_s +
            self.WEIGHTS["ltr"] * (ltr_scores / ltr_max) +
            self.WEIGHTS["bandit"] * bandit_s
        )

        ranked_idx = np.argsort(final_score)[::-1]
        ranked_cand = np.array(cand_list)[ranked_idx]
        selected = self.bandit.select(ranked_cand, profile, n)

        score_by_cand = {cand_list[i]: final_score[i] for i in range(len(cand_list))}

        results = []
        for pos, idx in enumerate(selected):
            row = self.df.loc[idx]
            results.append({
                "rank": pos + 1,
                "title": str(row["title"]),
                "author": str(row["author"]),
                "genre": str(row["genre"]),
                "description": str(row.get("description", "")),
                "average_rating": round(float(row["average_rating"]), 2),
                "ratings_count": int(row["ratings_count"]),
                "page_count": int(row.get("page_count", 0)),
                "list_price": round(float(row.get("list_price", 0)), 2),
                "language": str(row.get("language", "en")),
                "published_year": str(row.get("published_year", "")),
                "cluster": int(row["cluster"]),
                "comment_score": round(float(row.get("comment_score", 0.0)), 3),
                "final_score": round(float(score_by_cand.get(idx, 0.0)), 4),
            })
        return results

    def record_feedback(self, item_idx: int, rating: float, profile: UserProfile):
        self.bandit.update(item_idx, rating / 5.0)
        profile.exploration_rate = max(0.05, profile.exploration_rate * 0.95)

    def _assert_fitted(self):
        if not self._fitted:
            raise RuntimeError("Call .fit() first.")


class QuestionerEngine:
    PACE_PAGE_MAP: Dict[str, Tuple[int, int]] = {
        "fast": (0, 350),
        "slow": (350, 9999),
        "any": (0, 9999),
    }

    MOOD_NORMALISE: Dict[str, str] = {
        "dark": "dark",
        "inspiring": "motivational",
        "motivational": "motivational",
        "emotional": "sad",
        "sad": "sad",
        "fast-paced": "adventurous",
        "fast": "adventurous",
        "adventurous": "adventurous",
        "relaxing": "relaxing",
        "happy": "happy",
        "romantic": "romantic",
        "curious": "curious",
        "nostalgic": "nostalgic",
        "thoughtful": "thoughtful",
    }

    def __init__(self, recommender: Recommender):
        self.recommender = recommender

    def run(self, n: int = 10) -> List[Dict[str, Any]]:
        answers = self._ask_questions()
        results = self.recommend_from_answers(answers, n=n)
        self._print_results(results)
        return results

    def recommend_from_answers(self, answers: Dict[str, Any], n: int = 10) -> List[Dict[str, Any]]:
        profile = self._build_profile(answers)
        filtered_df = self._filter_df(answers)

        if filtered_df.empty:
            log.warning("No books matched filters — using full dataset.")
            filtered_df = self.recommender.df

        if self.recommender._fitted and len(filtered_df) >= 5:
            filtered_titles = set(filtered_df["title"].str.lower())
            profile.viewed_books = []
            results = self.recommender.recommend_by_profile(profile, n=n * 2)
            results = [r for r in results if r["title"].lower() in filtered_titles]

            if not results:
                results = self._heuristic_rank(filtered_df, answers, n)
        else:
            results = self._heuristic_rank(filtered_df, answers, n)

        for i, r in enumerate(results[:n], 1):
            r["rank"] = i

        return results[:n]

    @staticmethod
    def _ask(prompt: str, options: Optional[List[str]] = None) -> str:
        if options:
            opts = " | ".join(options)
            print(f"\n Options: {opts}")
            print(" Press [Enter] to skip")
        answer = input(f" {prompt} ").strip().lower()
        return answer

    def _ask_questions(self) -> Dict[str, Any]:
        print()
        print("=" * 55)
        print("      DigiKitab - Book Finder Questionnaire")
        print("=" * 55)

        genre = self._ask("What genre are you in the mood for?",
                          ["fantasy", "romance", "thriller", "science", "history", "any"])
        mood = self._ask("What vibe or mood do you want?",
                         ["dark", "inspiring", "emotional", "adventurous", "relaxing", "any"])
        pace = self._ask("Preferred reading pace? (slow/fast/any)", ["slow", "fast", "any"])
        if pace not in ("slow", "fast", "any"):
            pace = "any"

        language = self._ask("Preferred language?", ["en", "any"])
        language = language.split()[0] if language else "any"

        popularity = self._ask("Popular bestseller or hidden gem?", ["popular", "underrated", "any"])
        if popularity not in ("popular", "underrated", "any"):
            popularity = "any"

        favorite_author = self._ask("Who is your favourite author? (press Enter to skip)", None)

        print("\n Got it! Searching for the perfect books...\n")

        return {
            "genre": genre,
            "mood": mood,
            "pace": pace,
            "language": language,
            "popularity": popularity,
            "favorite_author": favorite_author,
        }

    def _build_profile(self, answers: Dict[str, Any]) -> UserProfile:
        profile = UserProfile()

        genre = answers.get("genre", "").strip()
        if genre and genre != "any":
            profile.preferred_genres = [genre.capitalize()]

        raw_mood = answers.get("mood", "").strip().lower()
        profile.mood = self.MOOD_NORMALISE.get(raw_mood, raw_mood)

        author = answers.get("favorite_author", "").strip()
        if author:
            profile.preferred_authors = [author]

        return profile

    def _filter_df(self, answers: Dict[str, Any]) -> pd.DataFrame:
        df = self.recommender.df.copy()

        genre = answers.get("genre", "").strip()
        if genre and genre != "any":
            mask = df["genre"].str.contains(genre, case=False, na=False)
            if mask.any():
                df = df[mask]

        author = answers.get("favorite_author", "").strip()
        if author:
            mask = df["author"].str.contains(author, case=False, na=False)
            if mask.any():
                df = df[mask]

        lang = answers.get("language", "any").strip().lower()
        if lang and lang != "any":
            mask = df["language"].str.lower().str.startswith(lang, na=False)
            if mask.any():
                df = df[mask]

        pace = answers.get("pace", "any").strip().lower()
        lo, hi = self.PACE_PAGE_MAP.get(pace, (0, 9999))
        if "page_count" in df.columns and pace != "any":
            mask = df["page_count"].between(lo, hi)
            if mask.any():
                df = df[mask]

        pop = answers.get("popularity", "any").strip().lower()
        if "ratings_count" in df.columns:
            if pop == "popular":
                df = df.sort_values("ratings_count", ascending=False)
            elif pop == "underrated":
                df = df.sort_values("ratings_count", ascending=True)

        return df.reset_index(drop=True)

    def _heuristic_rank(self, df: pd.DataFrame, answers: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        df = df.copy()

        max_rc = max(df["ratings_count"].max(), 1) if "ratings_count" in df.columns else 1
        pop = answers.get("popularity", "any").lower()
        pop_w = -1 if pop == "underrated" else 1

        df["_score"] = (
            df["average_rating"].fillna(0) / 5.0 * 0.6 +
            (df["ratings_count"].fillna(0) / max_rc) * 0.4 * pop_w
        ).clip(-0.4, 0.4)

        results = []
        for pos, (_, row) in enumerate(df.nlargest(n, "_score").iterrows(), 1):
            results.append({
                "rank": pos,
                "title": str(row.get("title", "Unknown")),
                "author": str(row.get("author", "Unknown")),
                "genre": str(row.get("genre", "Unknown")),
                "description": str(row.get("description", "")),
                "average_rating": round(float(row.get("average_rating", 0)), 2),
                "ratings_count": int(row.get("ratings_count", 0)),
                "page_count": int(row.get("page_count", 0)),
                "list_price": round(float(row.get("list_price", 0)), 2),
                "language": str(row.get("language", "en")),
                "published_year": str(row.get("published_year", "")),
                "cluster": int(row.get("cluster", -1)),
                "comment_score": round(float(row.get("comment_score", 0.0)), 3),
                "final_score": round(float(row.get("_score", 0.0)), 4),
            })
        return results

    @staticmethod
    def _print_results(books: List[Dict[str, Any]]) -> None:
        if not books:
            print(" No books found - try different answers.\n")
            return

        print("=" * 55)
        print(f"      Found {len(books)} recommendations for you!")
        print("=" * 55)
        print()

        for b in books:
            stars = "★" * round(b.get("average_rating") or 0)
            stars += "☆" * (5 - len(stars))
            price = "Free" if b.get("list_price", 0) == 0 else f"${b['list_price']:.2f}"

            print(f" #{b['rank']:02d} {b['title']}")
            print(f" Author : {b['author']}")
            print(f" Genre  : {b['genre']}")
            print(f" Rating : {stars} {b.get('average_rating', 'N/A')}")
            print(f" Pages  : {b.get('page_count', 'N/A')}")
            print(f" Price  : {price}")
            print("-" * 46)
            print()


if __name__ == "__main__":
    print("\n Loading dataset and fitting ML engine…")
    
    try:
        _df = DataLoader.load([
            "merged_complete_dataset.csv",
            "google_books_dataset.csv",
            "dataset_gutenberg.csv",
            "bookg.csv",
        ])
    except Exception as e:
        print(f"Warning: Could not load CSV files: {e}")
        _df = DataLoader._synthetic()

    if _df is not None:
        try:
            _rec = Recommender(_df)
            _rec.fit()

            qe = QuestionerEngine(_rec)

            while True:
                try:
                    qe.run(n=10)
                    again = input(" Search again? [Enter = yes / n = quit] ").strip().lower()
                    if again == "n":
                        print("\n Thanks for using DigiKitab. Happy reading!\n")
                        break
                except KeyboardInterrupt:
                    print("\n\n Operation cancelled by user.")
                    break
                except Exception as e:
                    print(f"\n An error occurred: {e}")
                    import traceback
                    traceback.print_exc()
                    break

        except Exception as e:
            print(f"\n Fatal Error: {e}")
            import traceback
            traceback.print_exc()