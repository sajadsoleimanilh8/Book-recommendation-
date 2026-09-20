"""Score fusion and the cold-start questionnaire — Recommender, QuestionerEngine.

Extracted verbatim from `engine.py` in the Phase C restructure — the
last two classes, and the ones every sub-engine holds a reference back
to. Moved together into one module because QuestionerEngine takes a
Recommender directly (not a forward reference): the two were never
going to end up in separate files without introducing exactly the
circularity every other sub-engine extraction in this restructure
avoided with a TYPE_CHECKING import.

Recommender owns the four sub-engines (AudiobookEngine, CommentEngine,
ChatbotEngine, ReminderEngine) and constructs them in .fit() — real
imports, since nothing they define is needed at class-definition time
here, only at instantiation.

The interactive __main__ block that used to follow QuestionerEngine in
engine.py (RESTRUCTURE-NOTES B-2) is not here — it moves to scripts/ in
the commit that reduces engine.py to its final facade, not this one.

Carries F-26 and F-46's fixes and their explanations intact: the LTR
training-set comment block above _relevance/_ltr_training_set, and the
_profile_query encoder-first / positional-fallback logic.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from domain.entities import MOOD_GENRE_MAP, UserProfile
from ml.bandit import ContextualBandit
from ml.clustering import ClusteringModel
from ml.genre_popularity import GenrePopularityFactors
from ml.features import FeatureEngineer
from ml.ranking import LearningToRank
from ml.similarity import SimilarityEngine
from services.audio import AudiobookEngine
from services.catalogue import (
    _safe_float,
    _safe_int,
    infer_format,
    infer_mood,
    normalize_language,
    price_and_availability,
    price_within,
)
from services.chat import ChatbotEngine
from services.comments import CommentEngine
from services.reading import ReminderEngine

log = logging.getLogger(__name__)


def language_mask(column: pd.Series, requested: str) -> pd.Series:
    """Which rows of `column` satisfy a language preference — F-47.

    English by default (`requested="en"`); `"any"` opts out entirely. Prefix
    match against `services.catalogue.normalize_language`'s output, mirroring
    the questionnaire's own filter (`QuestionerEngine._filter_df`) — but NOT
    copying it exactly. That filter excludes a book the instant its language
    is unrecorded, because `"unknown".startswith("en")` is `False`. 32.8% of
    this catalogue (9,842 books, mostly Goodreads) carries no language label
    at all — copying that pattern into a filter that is now *on by default*
    would silently drop a third of the catalogue from every recommendation
    nobody asked to be filtered. That is the exact "unknown is not a negative
    signal" mistake OI-11 (price) and F-29 (provider lookups) already cost
    this project real damage to learn.

    So: exclude a row only when its language is *confirmed* to be something
    else. A row with no language on record passes through un-filtered — we
    simply do not know, and "do not know" must not be read as "not English".

    A free function, not inlined into `recommend_by_profile`, on purpose:
    that method's actual candidate pool is capped at ~150 by the ANN
    shortlist and then further shuffled by the bandit's exploration slots, so
    testing this logic through the full pipeline is unreliable — the same
    profile and filter can appear to include or exclude a given book from one
    call to the next for reasons that have nothing to do with the filter
    itself. Tested directly, against the full column, instead.
    """
    requested = (requested or "").strip().lower()
    if not requested or requested == "any":
        return pd.Series(True, index=column.index)

    normalized = column.astype(str).str.lower()
    matches_requested = normalized.str.startswith(requested, na=False)
    unrecorded = normalized == "unknown"
    return matches_requested | unrecorded


class Recommender:
    WEIGHTS = {"content": 0.28, "genre_pop": 0.24, "ltr": 0.32, "bandit": 0.16}

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self.df["comment_score"] = 0.0

        self.engineer = FeatureEngineer()
        self.cluster = ClusteringModel()
        self.ann = SimilarityEngine()
        self.genre_pop = GenrePopularityFactors()
        self.ltr = LearningToRank()
        self.bandit = ContextualBandit()

        self._fitted = False
        self._X: Optional[np.ndarray] = None

        self.audiobook: Optional[AudiobookEngine] = None
        self.comment: Optional[CommentEngine] = None
        self.chatbot: Optional[ChatbotEngine] = None
        self.reminder: Optional[ReminderEngine] = None

    # Which space content similarity is actually using. Reported by /health,
    # because a silent fallback to the weaker space is indistinguishable from
    # working (the F-41 lesson).
    content_space: str = "tfidf_svd"

    def fit(
        self,
        content_vectors: Optional[np.ndarray] = None,
        query_encoder: Optional[Callable[[list[str]], np.ndarray]] = None,
    ):
        log.info("=== Fitting Recommender ===")

        # F-46: encodes a stated preference into the same space as the book
        # vectors, so a profile that has rated nothing still gets a real
        # content signal. Optional — without it the old behaviour stands.
        self._query_encoder = query_encoder

        self._X = self.engineer.fit_transform(self.df)
        self.df["cluster"] = self.cluster.fit(self._X)
        self.df["recency_weight"] = self.engineer.numeric_matrix[:, -2]

        # Content similarity runs on MiniLM book vectors when the caller
        # supplies them (F-44). Clustering deliberately keeps the TF-IDF
        # space: `cluster_match` is a separate feature, and changing two
        # things at once would make the ranking diff unreadable.
        #
        # The engine takes vectors rather than reading them itself, so it
        # stays free of any database dependency — a failed load degrades
        # content similarity instead of preventing the app from starting
        # (section 12).
        if content_vectors is not None and len(content_vectors) == len(self.df):
            self.ann.fit(np.asarray(content_vectors, dtype=np.float32))
            self.content_space = "minilm"
            log.info(f"content similarity: MiniLM ({content_vectors.shape})")
        else:
            if content_vectors is not None:
                log.warning(
                    f"content vectors were {len(content_vectors)} rows for a "
                    f"{len(self.df)}-row catalogue — ignoring them"
                )
            self.ann.fit(self._X)
            self.content_space = "tfidf_svd"
            log.info("content similarity: TF-IDF + SVD (fallback)")
        self.genre_pop.fit(self.df)

        X_ltr, y_ltr = self._ltr_training_set()
        self.ltr.train(X_ltr, y_ltr)

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
        genre_pop_idx = self.genre_pop.similar_items(seed_idx, k=100)

        cand_list = list(dict.fromkeys(list(ann_idx) + list(genre_pop_idx)))[:150]
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

        mask &= language_mask(self.df["language"], profile.language)

        candidates = self.df[mask]
        if len(candidates) < 5:
            candidates = self.df

        cand_idx = candidates.index.tolist()

        if profile.taste_vector is not None:
            q_idx, q_sims = self.ann.query_vector(profile.taste_vector, k=min(150, len(candidates)))
            q_idx = [i for i in q_idx if i in cand_idx]
            q_sims = q_sims[:len(q_idx)]
        else:
            q_idx, q_sims = self._profile_query(profile, candidates)

        seed_idx = q_idx[0] if q_idx else 0
        return self._rank(seed_idx, q_idx, np.array(q_idx), q_sims, profile, n)

    # ----------------------------------------------------------------
    # F-46. `taste_vector` is never assigned anywhere in this codebase, so
    # the branch above was unreachable and *every* profile request fell to
    # the else — where `content_s` was `linspace(1, 0)` over the first 150
    # candidates in source-file order. A positional prior carrying 0.28 of
    # the final score under the name of a content signal.
    #
    # With per-book MiniLM vectors (F-44) the honest version is cheap: encode
    # what the reader actually said they wanted and ask the ANN. A profile
    # that has rated nothing has still *told us something*, and that is the
    # most common state a recommender ever sees.
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # F-26. Five of the ten LTR features are query-dependent: content_s,
    # genre_pop_s, cluster_match and mood_match only mean anything *relative to a
    # seed*, and comment_score is zero for every book at fit time because no
    # book has a comment yet.
    #
    # Training pointwise over the catalogue left no seed to be relative to,
    # so the old code passed `ones` for two of them and `zeros` for two more.
    # A gradient-boosted tree never splits on a constant column, which is
    # why 7 of 10 features measured exactly 0.000 importance while five of
    # them were live inputs at inference. That is train/serve skew.
    #
    # The fix is to give training the same shape as serving: sample seeds,
    # build each example relative to its seed, and — critically — build it
    # with `self.ltr._features`, *the same function inference calls*. Sharing
    # the function is what makes the skew unable to come back; two parallel
    # implementations would drift again the first time either changed.
    #
    # What this does NOT fix: the target. `relevance` is still
    # 0.4*rating + 0.6*norm(log(ratings_count)), a function of three of the
    # model's own inputs, so the model can still only learn popularity. That
    # needs real interaction data (F-42) and a product decision about what a
    # good recommendation is. Expect this change to remove a defect, not to
    # improve rankings today.
    # ----------------------------------------------------------------

    LTR_SEEDS = 400
    LTR_PER_SEED = 60

    def _relevance(self, cands: pd.DataFrame, count_scale: float) -> np.ndarray:
        """The training target — reading depth, shrunk towards a prior (F-26).

        Decided 2026-09-11: relevance is how far readers get into a book.
        The popularity formula below survives only as the *prior*: what a
        book's relevance is assumed to be before anyone has read it.

            relevance = (n * depth + k * prior) / (n + k)

        With no readers `n` is 0 and this returns the prior exactly — so while
        there is no traffic, training is unchanged and so is every ranking.
        As readers accumulate, the target moves to what they actually did.

        Depth is read here and nowhere else. It is deliberately **not** a
        feature: a column that were both input and target would let the model
        learn "relevance equals this column" and nothing else.
        """
        rating = cands["average_rating"].to_numpy(dtype=float) / 5.0
        counts = np.log1p(cands["ratings_count"].to_numpy(dtype=float))
        # Normalised against the whole catalogue, not the batch, so targets
        # from different seeds are on one scale.
        prior = 0.4 * rating + 0.6 * (counts / count_scale)

        if "reading_depth_n" not in cands.columns:
            return prior

        from services.reading_depth import shrink

        readers = cands["reading_depth_n"].to_numpy(dtype=float)
        depth = cands["reading_depth"].to_numpy(dtype=float)
        return shrink(depth, readers, prior)

    def _ltr_training_set(self) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(42)
        n = len(self.df)
        count_scale = float(np.log1p(self.df["ratings_count"].to_numpy(dtype=float)).max()) + 1e-9

        seeds = rng.choice(n, size=min(self.LTR_SEEDS, n), replace=False)
        moods = list(MOOD_GENRE_MAP) or [None]

        blocks_X: list[np.ndarray] = []
        blocks_y: list[np.ndarray] = []

        for seed in seeds:
            seed = int(seed)
            try:
                idx, sims = self.ann.query(seed, k=self.LTR_PER_SEED)
            except Exception:
                continue
            if len(idx) == 0:
                continue

            genre_pop_top = self.genre_pop.similar_items(seed, k=200)
            genre_pop_rank = {int(j): 1.0 - r / max(len(genre_pop_top), 1) for r, j in enumerate(genre_pop_top)}
            genre_pop_s = np.array([genre_pop_rank.get(int(j), 0.0) for j in idx])

            cands = self.df.loc[list(idx)]
            mood = moods[int(rng.integers(len(moods)))]

            blocks_X.append(
                self.ltr._features(
                    cands,
                    np.asarray(sims, dtype=float),
                    genre_pop_s,
                    int(self.df.loc[seed, "cluster"]),
                    MOOD_GENRE_MAP.get(mood, []) if mood else [],
                )
            )
            blocks_y.append(self._relevance(cands, count_scale))

        if not blocks_X:
            # Nothing to learn from. `score()` already falls back to a fixed
            # blend when unfitted, so returning empty is safe and honest.
            log.warning("LTR: no seed produced candidates; leaving model unfitted")
            return np.empty((0, 10)), np.empty(0)

        return np.vstack(blocks_X), np.concatenate(blocks_y)

    def _profile_text(self, profile: UserProfile) -> str:
        """The reader's stated preference, in the book vectors' own idiom.

        Same shape as `book_vector_pass.build_text` on purpose — "Fiction.
        dark." is compared against "Dune. Frank Herbert. Science Fiction.",
        so the query should read like the documents.
        """
        parts: list[str] = []
        parts.extend(profile.preferred_genres or [])
        parts.extend(profile.preferred_authors or [])
        if profile.mood:
            parts.append(str(profile.mood))
            parts.extend(MOOD_GENRE_MAP.get(profile.mood, []))
        return ". ".join(str(p).strip() for p in parts if str(p).strip())

    def _profile_query(self, profile: UserProfile, candidates: pd.DataFrame):
        """(indices, similarities) for a profile with no taste vector."""
        text = self._profile_text(profile)
        encoder = getattr(self, "_query_encoder", None)

        if encoder is not None and text:
            try:
                vector = np.asarray(encoder([text]), dtype=np.float32).reshape(-1)
                idx, sims = self.ann.query_vector(
                    vector, k=min(150, len(self.df))
                )
                keep = [
                    (i, sc)
                    for i, sc in zip(idx.tolist(), sims.tolist())
                    if i in candidates.index
                ]
                if keep:
                    return [i for i, _ in keep], np.array([sc for _, sc in keep])
                # Every neighbour was filtered out by genre/mood. Falling
                # through is correct: an empty content signal is worse than
                # the positional one it replaces.
            except Exception as exc:
                log.warning(f"profile query encoding failed, using order: {exc}")

        # Unchanged fallback: no encoder, no stated preference, or no
        # neighbour survived the filters.
        idx = candidates.head(150).index.tolist()
        return idx, np.linspace(1, 0, len(idx))

    def _rank(self, seed_idx, cand_list, ann_idx, ann_sims, profile, n) -> list[dict]:
        if not cand_list:
            return []

        cands = self.df.loc[cand_list].copy()
        sim_lookup = dict(zip(ann_idx.tolist(), ann_sims.tolist()))
        content_s = np.array([sim_lookup.get(i, 0.0) for i in cand_list])

        genre_pop_top = self.genre_pop.similar_items(seed_idx, k=200)
        genre_pop_lookup = {idx: 1.0 - rank / len(genre_pop_top) for rank, idx in enumerate(genre_pop_top)}
        genre_pop_s = np.array([genre_pop_lookup.get(i, 0.0) for i in cand_list])

        mood_genres = MOOD_GENRE_MAP.get(profile.mood, [])
        cluster_id = int(self.df.loc[seed_idx, "cluster"])
        ltr_scores = self.ltr.score(cands, content_s, genre_pop_s, cluster_id, mood_genres)
        bandit_s = np.array([self.bandit.expected_reward(i) for i in cand_list])

        ltr_max = ltr_scores.max() + 1e-9
        final_score = (
            self.WEIGHTS["content"] * content_s +
            self.WEIGHTS["genre_pop"] * genre_pop_s +
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

        # F-47, applied here deliberately rather than left at the dataclass
        # default. `recommend_from_answers` passes this profile into
        # `recommend_by_profile`, whose language filter would otherwise
        # silently default to "en" — even when this method's OWN, separately
        # decided `_filter_df` (a few lines below) is applying the
        # questionnaire's actual answer, which defaults to "any". Without
        # this line, a user who answers "any" (or never sees a language
        # question at all) would still have the ML-scored half of this
        # method's results narrowed to English, contradicting the filtered
        # candidate set they were told to draw from. Explicitly mirroring
        # `_filter_df`'s own default keeps the two halves of this method in
        # agreement, and leaves the questionnaire's pre-existing, separately
        # decided behaviour (opt-in language filtering) untouched by F-47 —
        # which is scoped to the direct /api/recommend path, not this one.
        profile.language = answers.get("language", "any").strip().lower() or "any"

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

# Row-shaping and filter glue moved from main.py in the Phase D
# restructure: apply_filters, _ml_to_api, _gutenberg_to_api and
# fuse_and_rank. All four depend only on services.catalogue's
# formatting helpers, so they move cleanly.
#
# fit_ml is NOT here. It mutates main.py's CONTENT_VECTOR_REPORT
# module global via the `global` keyword, which /health reads by name
# (RESTRUCTURE-NOTES B-4 — the same live-module-state contract that
# keeps RECOMMENDER and QUESTIONER in main.py), and it calls
# _get_search_encoder, which still lives in main.py. Moving fit_ml
# alone would either break that contract or reach back into main.py
# for a helper — it moves with the lifespan extraction instead,
# where startup()'s state is addressed as a whole.

def apply_filters(
    data: List[Dict[str, Any]],
    genre: Optional[str] = None,
    rating_min: Optional[float] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    max_price: Optional[float] = None,
    mood: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out = data[:]  # Create a copy
    if genre:
        out = [b for b in out if genre.lower() in b.get("genre", "").lower()]
    if language:
        out = [b for b in out if language.lower() in b.get("language", "").lower()]
    if mood:
        out = [b for b in out if mood.lower() in b.get("mood", "").lower()]
    if rating_min is not None:
        out = [b for b in out if _safe_float(b.get("rating")) >= rating_min]
    if max_price is not None:
        # OI-11. `_safe_float` turned the unknown price every non-Gutenberg row
        # carries into 0.0, so "books under $5" matched the entire catalogue —
        # a filter that silently does nothing is worse than one that is absent.
        #
        # Filter on *known* prices only. A book whose price nobody knows may
        # well cost more than the cap, and we cannot claim otherwise. That
        # leaves the Gutenberg subset, which is verifiably free, so the filter
        # is narrow but every result it returns is true.
        out = [b for b in out if price_within(b, max_price)]
    if q:
        ql = q.lower()
        out = [b for b in out if ql in b.get("title", "").lower() or ql in b.get("author", "").lower() or ql in b.get("description", "").lower()]
    return out

def _ml_to_api(b: Dict[str, Any], rank: int) -> Dict[str, Any]:
    # Same rule as the catalogue path. Two serialisers disagreeing about what
    # a book costs is how F-36 would come back.
    price, availability, inferred_source = price_and_availability(b)
    return {
        "id": b.get("id", rank),
        "title": b.get("title", "Unknown"),
        "author": b.get("author", "Unknown"),
        "genre": b.get("genre", "Unknown"),
        "mood": infer_mood(b.get("genre", ""), b.get("title", ""), b.get("description", "")),
        "language": normalize_language(b.get("language", "en")),
        "format": infer_format(_safe_int(b.get("page_count"))),
        "rating": _safe_float(b.get("average_rating")),
        "ratings_count": _safe_int(b.get("ratings_count")),
        "price": price,
        "availability": availability,
        "pages": _safe_int(b.get("page_count")),
        "audiobook": _safe_int(b.get("page_count")) > 350,
        "description": b.get("description", ""),
        "thumbnail": b.get("thumbnail", ""),
        "published_year": b.get("published_year", ""),
        "cluster": b.get("cluster", -1),
        "comment_score": round(_safe_float(b.get("comment_score")), 3),
        "ml_score": round(_safe_float(b.get("final_score")), 4),
        "content_sim": round(_safe_float(b.get("content_sim")), 3),
        "cf_sim": round(_safe_float(b.get("cf_sim")), 3),
        "source": b.get("source") or inferred_source,
        "url": b.get("url", ""),
        "rank": rank,
    }

def _gutenberg_to_api(g: Dict[str, Any], rank: int) -> Dict[str, Any]:
    return {
        "id": f"gutenberg_{rank}",
        "title": g.get("title", "Unknown"),
        "author": g.get("author", "Unknown"),
        "genre": g.get("genre", "Unknown"),
        "mood": infer_mood(g.get("genre", ""), g.get("title", ""), ""),
        "language": "English",
        "format": "Print",
        "rating": None,
        "ratings_count": None,
        "download_count": g.get("download_count", 0),
        "price": 0.0,
        "pages": None,
        "audiobook": False,
        "description": "",
        "thumbnail": "",
        "published_year": None,
        "cluster": -1,
        "comment_score": 0.0,
        "ml_score": round(g.get("download_count", 0) / 1_000_000, 4),
        "content_sim": None,
        "cf_sim": None,
        "source": "gutenberg",
        "url": g.get("url", ""),
        "rank": rank,
    }

def fuse_and_rank(
    local: List[Dict[str, Any]],
    gutenberg: List[Dict[str, Any]],
    top_n: int = 12,
) -> List[Dict[str, Any]]:
    combined = sorted(
        local + gutenberg,
        key=lambda b: _safe_float(b.get("ml_score")),
        reverse=True,
    )
    for i, b in enumerate(combined[:top_n], 1):
        b["rank"] = i
    return combined[:top_n]

