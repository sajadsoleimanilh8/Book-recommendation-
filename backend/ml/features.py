"""Feature engineering — text vectorization and numeric feature assembly.

Extracted verbatim from `engine.py` in the Phase C restructure.

Pure model code: numpy, pandas, re, sklearn and logging only. Nothing
here imports fastapi, sqlalchemy or anything under `services/`.
"""

from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import LabelEncoder, StandardScaler

log = logging.getLogger(__name__)


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
        genre_values = genre_col.astype(str).to_numpy()
        
        df["genre_enc"] = self.le_genre.fit_transform(genre_values)
        df["lang_enc"] = self.le_lang.fit_transform(df["language"].astype(str).to_numpy())
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
        title_str = df["title"].fillna("").astype(str).to_numpy()
        author_str = df["author"].fillna("").astype(str).to_numpy()
        genre_str = df["genre"].fillna("Unknown").astype(str).to_numpy()
        description_str = df["description"].fillna("").astype(str).to_numpy()
        
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

        # F-15: every catalogue description is the identical placeholder
        # "No description available", so the TF-IDF vocabulary collapses to a
        # handful of terms — far fewer than comment_svd's 16 components, which
        # made TruncatedSVD raise and took the whole ML fit down with it.
        #
        # Clamp to the vocabulary actually present. This keeps the engine
        # alive on today's data; it does not make the embeddings useful. Once
        # Phase 2 enrichment lands, n_features rises and the requested
        # component count is used as intended.
        n_features = mat.shape[1]
        if n_features < 2:
            log.warning(
                f"Comment embedder skipped: vocabulary has {n_features} term(s). "
                "Descriptions carry no usable text (F-15)."
            )
            return

        requested = self.comment_svd.n_components
        n_components = max(1, min(requested, n_features - 1))
        if n_components != requested:
            log.warning(
                f"Comment embedder: vocabulary has only {n_features} terms, "
                f"reducing SVD components {requested} -> {n_components}. "
                "Expected until Phase 2 enrichment fills in descriptions (F-15)."
            )
            self.comment_svd = TruncatedSVD(
                n_components=n_components, random_state=42
            )

        self.comment_svd.fit(mat)
        self._comment_fitted = True
        log.info(f"Comment embedder fitted ({n_components} components).")
