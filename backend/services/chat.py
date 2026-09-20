"""Intent classification and canned responses — the ChatbotEngine.

Extracted verbatim from `engine.py` in the Phase C restructure, along
with CHATBOT_CORPUS, the module constant only this class uses (grep
confirmed no other reference in engine.py). MOOD_GENRE_MAP is imported
from domain.entities, where it was relocated ahead of this extraction —
see the note there for why (a shared constant that Recommender and
QuestionerEngine, both still in engine.py, also need).

Takes AudiobookEngine and CommentEngine directly (both already
extracted, real imports); Recommender and ReminderEngine via
TYPE_CHECKING, since both still construct or are constructed alongside
this class and a real import would be circular.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline

from domain.entities import MOOD_GENRE_MAP, UserProfile
from services.audio import AudiobookEngine
from services.comments import CommentEngine
from services.librarian import Librarian, default_deps
from services.providers.llm import LLMUnavailable, get_llm_provider

if TYPE_CHECKING:
    from services.recommendation import Recommender
    from services.reading import ReminderEngine

log = logging.getLogger(__name__)

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

        # F-22 Phase C: the LLM tool loop sits in front of the classifier.
        # Set to None to run classifier-only (tests do; so does anyone who
        # wants the pre-F-22 behaviour back).
        self.librarian: Optional[Librarian] = Librarian(get_llm_provider(), default_deps())

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
        account_id: Optional[int] = None,
        history: Optional[list] = None,
        prior_book_ids: Optional[list] = None,
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
            "mode": "classifier",
        }

        if self.librarian is not None:
            try:
                result = self.librarian.answer(
                    user_message, account_id=account_id, profile=profile,
                    history=history, prior_book_ids=prior_book_ids,
                )
            except LLMUnavailable as exc:
                # Section 12: every model in the chain is down. Say so in the
                # log and carry on with the deterministic reply below.
                log.warning(f"librarian unavailable, using classifier fallback: {exc}")
            else:
                response.update(
                    message=result.message,
                    books=result.books,
                    mode="llm",
                    llm={"model": result.model, "steps": result.steps,
                         "grounded": result.grounded},
                )
                return response

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
