"""Synthetic catalogue generation — the DataLoader.

Extracted verbatim from `engine.py` in the Phase C restructure.

Pure model code: numpy, pandas and logging only. Nothing here imports
fastapi, sqlalchemy or anything under `services/`, and nothing may be
added that does — this layer has to stay usable without an app or a
database (RESTRUCTURE-PROMPT, Definition of Done).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


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
