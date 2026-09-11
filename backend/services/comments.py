"""Comment storage, sentiment scoring and keyword extraction — CommentEngine.

Extracted verbatim from `engine.py` in the Phase C restructure, along
with POSITIVE_WORDS/NEGATIVE_WORDS, the two module constants only this
class uses (confirmed by grep — no other reference in engine.py).

Takes a `Recommender` reference to update `comment_score` on its
DataFrame, via a TYPE_CHECKING import — a real import would be circular,
since `Recommender` constructs this class.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from domain.entities import Comment, UserProfile

if TYPE_CHECKING:
    from services.recommendation import Recommender

log = logging.getLogger(__name__)

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
