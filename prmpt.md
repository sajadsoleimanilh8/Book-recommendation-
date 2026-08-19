# Mission: Evolve DigiKitab from a Recommendation Model into a Production-Grade AI Reading Ecosystem

## Phase Index

Use this table to jump straight to what governs the phase you're on. "Foundational" sections apply across every phase and are not phase-bound — read them once, keep them active always. Tiers reference §7.

| Phase | Focus | Tier | Governing sections |
|---|---|---|---|
| 0 | Audit | — | §1–4, §70, §72 |
| 1 | Foundation | 1 | §9, §10, §14–16, §20, §21, §54, §55, §58, §59, §67 |
| 2 | Book Intelligence | 1 | §17–19, §22, §27 |
| 3 | AI Recommendation | 1 | §23–26, §28, §40 |
| 4 | Personal Library | 1 | §21–22, §29–31, §59 |
| 5 | AI Reading | 1 | §32–33, §37–39 |
| 6 | Audio | 2 | §35–36 |
| 7 | Product Experience | 1/2 | §36, §39–42, §44, §66 |
| 8 | Evaluation & Observability | 1 | §15, §52–53 |
| 9 | Intelligence Layer | 2 | §34, §47–49 |
| 10 | Experience Layer | 2/3 | §43, §45–46, §50 |
| 11 | Platform | 3 | §45, §50, §56–57 |
| 12 | Global Demo | — | every Tier‑1 section, rehearsed end to end |

Foundational (not phase-bound, active throughout): §0, §2–8, §11–13, §51, §60–65, §69, §73.

Tier reconciliation: Phases 0–5 build Tier 1 (the core product). Phases 6–8 finish Tier 1 and start Tier 2 (differentiators — audio, polish, measurement). Phases 9–10 are Tier 2/3. Phase 11 is Tier 3 and is explicitly gated on product evidence, not built speculatively (§7, §8). Phase 12 is the rehearsed demo of everything Tier 1 delivered.

---

## 0. Who you are

You are acting as a senior AI/ML architect, backend engineer, frontend engineer, data engineer, security engineer, product engineer, and technical lead working together on one existing codebase.

You make production-grade decisions, not academic ones.

You optimize for:

* Working software
* Product coherence
* Reliability
* Maintainability
* Security and privacy
* Cost and latency
* Demo resilience
* Measurable AI quality
* Long-term scalability
* Minimal unnecessary complexity

This is an existing product that already won **3rd place out of approximately 200 teams**.

It is NOT a blank slate.

**Do not rewrite it from scratch.**

Your job is to evolve it systematically from a successful recommendation prototype into a globally competitive AI reading platform while preserving the valuable parts of the existing system.

---

## 1. Ground Truth: Existing System — *Phase 0*

The following is the current known state. Treat it as a hypothesis that MUST be verified against the actual repository before making architectural decisions.

Known current state:

* Backend: FastAPI app at `backend/main.py`, currently titled `"DigiKitab ML API"` and version `6.0.0`.
* Core backend logic appears to exist in `backend/engine.py`, including:

  * `DataLoader`
  * `GutenbergClient`
  * `Recommender`
  * `QuestionerEngine`
  * `UserProfile`
* Current stack includes:

  * pandas
  * numpy
  * scikit-learn
* Process layer:

  * `server.js`
  * Express serves the frontend
  * `/api/*` is proxied to FastAPI
  * Node currently spawns the Python backend through `uvicorn`
* Frontend currently consists primarily of plain HTML/CSS/JS:

  * `index.html`
  * `filter.html`
  * `user.html`
  * `chatbot.html`
  * `Audiobook.html`
  * `questionnair.html`
* Data:

  * `site_ready_books.json`
  * `merged_complete_dataset.csv`
  * `google_books_dataset.csv`
  * `dataset_gutenberg.csv`
  * `bookg.csv`
* Audio:

  * `backend/audio_outputs/`
  * Existing TTS caching may already exist and MUST be inspected before changing.
* State:

  * `backend/app_state.json`
  * in-memory `USER_PROFILES`
* Existing notebook-related files:

  * `notebook_adapter.py`
  * `notebook_bootstrap.py`
  * `run.ipynb`

These assumptions are NOT authoritative.

**Repository reality always wins over this specification.**

If the repository differs from this description, report the difference explicitly before modifying the relevant subsystem.

---

## 2. Non-Negotiable Operating Rules — *Foundational*

### 2.1 Audit before implementation

Never modify production code before understanding the existing implementation.

The first operation is repository inspection.

Inspect at minimum:

* `backend/main.py`
* `backend/engine.py`
* all frontend pages
* all frontend JavaScript
* `server.js`
* data schemas
* notebook files
* configuration files
* package files
* requirements files
* tests
* README/RUN documentation
* existing deployment configuration
* existing audio caching logic

Do not infer behavior from filenames alone.

---

## 3. Repository Reality Rule — *Foundational*

When the specification and repository reality conflict:

1. Do not silently choose.
2. Document the discrepancy.
3. Determine whether the existing behavior is intentional, accidental, or obsolete.
4. Preserve working behavior unless there is a concrete reason to change it.
5. Propose the change before implementing a destructive migration.

The specification defines the target direction.

The repository defines the current truth.

---

## 4. Preserve Existing Value — *Foundational*

Before modifying an existing subsystem:

1. Identify its externally observable behavior.
2. Identify its dependencies.
3. Identify inputs and outputs.
4. Identify current edge cases.
5. Add or identify regression coverage where practical.
6. Only then refactor or replace it.

Especially protect:

* Existing recommendation functionality
* Questionnaire cold-start flow
* `GutenbergClient`
* Existing TTS generation
* Existing audio caching
* Existing frontend flows
* Existing dataset value
* Existing successful competition functionality

Never replace a working subsystem merely because a newer architecture is theoretically cleaner.

---

## 5. Mission and Product Reframing — *Foundational*

DigiKitab is no longer positioned as merely a book recommendation model.

The product should evolve into:

> **An AI-powered personal reading ecosystem that helps users discover, understand, acquire, read, question, remember, and learn from books.**

The core product loop is:

```text
DISCOVER
   ↓
UNDERSTAND
   ↓
ACQUIRE
   ↓
READ
   ↓
ASK
   ↓
REFLECT
   ↓
LEARN
   ↓
REMEMBER
   ↓
RECOMMEND
   ↓
DISCOVER AGAIN
```

The recommendation engine remains important.

Machine learning is not discarded.

Instead:

> ML becomes one signal source inside a broader intelligence system.

The identity of DigiKitab becomes:

**AI + Recommendation + Semantic Search + RAG + Personal Library + Reading Intelligence + Memory + Learning**

---

## 6. Product Differentiation — *Foundational*

Do not describe DigiKitab as another recommendation platform.

The product should explicitly differentiate against:

### Goodreads

Primarily social cataloging, ratings, and reviews.

DigiKitab focuses on:

* personalized intelligence
* understanding why a book fits
* understanding the actual book
* learning from reading behavior
* AI-assisted reading
* memory across books

### Kindle / Audible

Excellent content delivery.

DigiKitab adds:

* book understanding
* grounded AI conversation
* notes
* learning
* reading intelligence
* cross-book reasoning

### Blinkist

Blinkist focuses heavily on consuming summaries.

DigiKitab focuses on:

> Deepening books the user actually reads.

Summarization is a tool, not the product identity.

### DigiKitab's moat

The moat is the closed loop:

```text
Reading
→ Questions
→ Notes
→ Understanding
→ Behavioral signals
→ Reading DNA
→ Better recommendations
→ Better next reading experience
```

The system should become increasingly useful as the user actually reads.

---

## 7. Scope Management — CRITICAL — *Foundational*

The vision is intentionally large.

The implementation must NOT attempt to build every feature simultaneously.

Use the following priority model.

### Tier 1 — Core Product

These are the primary product pillars:

1. Book discovery
2. Hybrid recommendations
3. Semantic search
4. Explainable recommendations
5. Personal library
6. Secure upload
7. Book ingestion
8. Private RAG
9. Reading Copilot
10. Reading DNA
11. Reading progress
12. Book Memory

These must reach production-quality before advanced features dominate development.

### Tier 2 — Differentiators

Build after Tier 1 is stable:

1. Study Mode
2. Notes Knowledge Base
3. Learning Paths
4. Socratic Mode
5. AI audiobook
6. Voice Reading Companion
7. Knowledge Graph
8. Reading → Learning Loop

### Tier 3 — Future Platform Features

Architecture should remain extensible for:

* Intellectual Communities
* Advanced social features
* B2B
* Public API
* Payments
* Advanced affiliate ecosystem
* Full internationalization
* Advanced multimodal workflows
* Enterprise features

Do NOT build Tier 3 infrastructure merely because it might eventually be useful.

**Tier ↔ Phase mapping** (see Phase Index above): Tier 1 = Phases 0–5. Tier 2 = Phases 6–7 and 9. Tier 3 = Phases 10–11. Phase 8 (evaluation) and Phase 12 (demo) are cross-cutting and apply to whatever tier is currently in progress.

---

## 8. No Speculative Infrastructure — *Foundational*

Do not introduce infrastructure simply because it may become useful at massive scale.

Avoid premature:

* microservices
* Kubernetes
* Kafka
* service meshes
* multiple databases
* dedicated vector infrastructure
* complex event streaming
* distributed orchestration
* unnecessary cloud services

Default architecture:

```text
FastAPI Modular Monolith
+
PostgreSQL
+
pgvector
+
Redis
+
Background Job Queue
+
S3-compatible Object Storage
```

Extract services only when measurable evidence demonstrates that extraction is necessary.

---

## 9. Architecture Target — *Phase 1*

Target architecture:

```text
                  External Book Providers
          ┌──────────────┬───────────────┐
          │ Google Books │ Open Library  │
          └──────────────┴───────────────┘
                         │
                 Provider Adapter Layer
                         │
                 Book Normalization
                         │
              ┌──────────┴──────────┐
              │                     │
        PostgreSQL              pgvector
              │                     │
              └──────────┬──────────┘
                         │
              Hybrid Intelligence
                         │
       ┌─────────────────┼──────────────────┐
       │                 │                  │
   Discovery          Library           Reading
       │                 │                  │
 Search / AI        Upload / RAG       Copilot / Notes
 Librarian          / Ownership        Memory / Study
       │                 │                  │
       └─────────────────┼──────────────────┘
                         │
                   FastAPI API
                         │
                  Frontend Application
                         │
               Analytics / Observability
```

This is a modular monolith.

Do not convert modules into microservices without evidence.

---

## 10. Provider Abstraction — *Phase 1*

No single external provider may become deeply coupled to business logic.

Every external dependency must sit behind an interface.

Examples:

```text
BookProvider
LLMProvider
EmbeddingProvider
TTSProvider
StorageProvider
AvailabilityProvider
OCRProvider
```

Business logic must depend on interfaces, not vendors.

This makes provider replacement possible without rewriting the application.

---

## 11. Legal and Copyright Rules — *Foundational*

These are hard constraints.

Never implement:

* piracy
* DRM circumvention
* unauthorized scraping
* redistribution of copyrighted books
* public hosting of user-uploaded copyrighted books
* ToS-violating data collection

Purchases redirect to legitimate sellers.

Users must attest that uploaded books are legally owned or legally usable.

Uploaded books are:

**private by default.**

Private uploaded content must never be used to train or fine-tune shared/global models.

---

## 12. Demo Resilience — *Foundational*

Every external AI/API feature must have a graceful fallback.

If:

* API key is missing
* provider times out
* rate limit occurs
* network fails
* model fails
* TTS provider fails
* embedding provider fails

the application must NOT hard crash.

Possible fallback:

```text
Live provider
    ↓ failure
Cached response
    ↓ unavailable
Deterministic/local fallback
    ↓ unavailable
Clear user-facing degraded state
```

A judge-facing demo must remain functional during network failure.

---

## 13. Cost and Latency Rules — *Foundational*

Every LLM call must have a reason.

Before adding an LLM call ask:

1. Can classical ML solve this?
2. Can retrieval solve this?
3. Can caching solve this?
4. Can a smaller model solve this?
5. Can the task be batched?
6. Does this need generation at all?

LLMs should be reserved for high-value reasoning/generation.

Never call an LLM once per recommendation candidate.

---

## 14. Production Configuration — *Phase 1*

Never hardcode:

* API keys
* database credentials
* secrets
* provider configuration
* production URLs

Use:

```text
.env
.env.example
```

Separate:

```text
local
staging
production
```

Configuration must be environment-driven.

---

## 15. Observability — *Phase 1 & 8*

Every important request should have:

* request ID
* trace ID where applicable
* user ID where safe
* module
* latency
* provider
* model
* token usage
* estimated cost
* cache hit/miss
* error status

Never log:

* API keys
* passwords
* private book contents
* private notes
* raw private conversations
* secrets
* database credentials

---

## 16. Target Technology Stack — *Phase 1*

Use the following defaults unless repository inspection identifies a concrete reason not to.

### Frontend

Target:

* Next.js
* React
* TypeScript
* Tailwind CSS

But do NOT rewrite the current frontend immediately.

Migration must be incremental.

### Backend

Keep:

**FastAPI / Python**

Do not replace the backend framework.

### Database

Use:

**PostgreSQL**

Reasons:

* transactional integrity
* mature ecosystem
* relational modeling
* analytics
* JSON support
* extensibility
* pgvector support

### Vector Search

Start with:

**pgvector**

Move to Qdrant only if measurable scale or latency requirements justify it.

### Cache

Use:

**Redis**

For:

* response caching
* embedding caching
* TTS caching metadata
* sessions where appropriate
* rate limiting
* temporary jobs

### Object Storage

Use S3-compatible storage for:

* uploaded books
* generated audio
* covers
* derived artifacts

Private objects must use signed URLs.

Never expose private books through public static paths.

---

## 17. Book Intelligence — *Phase 2*

Create:

```python
class BookProvider(Protocol):
    def search_books(self, query: str, filters: dict) -> list[BookRecord]:
        ...

    def get_book(self, provider_id: str) -> BookRecord:
        ...

    def get_by_isbn(self, isbn: str) -> BookRecord | None:
        ...

    def get_availability(self, isbn: str) -> list[AvailabilityRecord]:
        ...
```

Primary metadata providers:

### Google Books

Use for:

* title
* authors
* ISBN
* description
* categories
* covers
* ratings
* preview links

### Open Library

Use for:

* edition coverage
* ISBN lookup
* older titles
* fallback metadata

Run providers in parallel where useful.

Normalize results into a provider-independent internal schema.

---

## 18. Availability Architecture — *Phase 2*

Metadata providers and availability providers are different concepts.

Google Books and Open Library do not automatically provide reliable real-time commercial availability/pricing.

Design:

```text
AvailabilityProvider
```

with adapters for legitimate providers such as:

* Amazon Product Advertising API
* Bookshop.org affiliate ecosystem
* publisher/store APIs
* other officially supported providers

Do not fake price or availability.

If no provider is configured:

```text
availability = unknown
```

not fabricated.

---

## 19. Local Dataset Migration — *Phase 1–2*

Existing CSV/JSON files are NOT deleted immediately.

Wrap them behind:

```text
LocalCatalogProvider
```

They become:

**seed/fallback data**

rather than the architectural source of truth.

Preserve useful Gutenberg metadata.

---

## 20. Database Model — *Phase 1 (entities land incrementally per phase)*

Core entities:

```text
User
UserPreference
Book
BookEdition
Author
Genre
Publisher
Store
BookAvailability
BookPrice
ReadingSession
ReadingProgress
UserNote
Recommendation
PurchaseEvent
UserUpload
BookChunk
Embedding
ConversationMemory
```

Intelligence entities:

```text
KnowledgeGraphNode
KnowledgeGraphEdge
UserConcept
```

Observability/evaluation entities:

```text
AIEvaluationRun
ModelUsageLog
AIRequest
```

Do not implement every entity in one migration.

Create them incrementally according to the roadmap.

---

## 21. Book and User Isolation — *Phase 1 & 4*

Every private object must be scoped by:

```text
user_id
```

Private:

* uploads
* chunks
* embeddings
* notes
* conversations
* reading history
* memories

must never leak across users.

Add automated tests for isolation.

---

## 22. BookChunk + Embedding Architecture — *Phase 2 & 4*

`BookChunk` must support both:

### Public catalog knowledge

```text
visibility = public
```

### Private user-owned books

```text
visibility = private
user_id = specific user
```

Retrieval must enforce authorization before returning chunks.

A user's private chunks must never appear in another user's search.

---

## 23. Recommendation Engine — *Phase 3*

Do NOT discard the existing `Recommender`.

First determine exactly what it currently implements.

Possible current signals:

* content similarity
* collaborative signals
* clustering
* metadata similarity
* user questionnaire
* ratings

Verify reality.

Then evolve:

```text
Candidate Generation
        ↓
Content Similarity
Collaborative Signals
Behavioral Signals
Semantic Retrieval
Context
        ↓
Candidate Pool
        ↓
Classical Ranking Model
        ↓
Shortlist
        ↓
Optional LLM Reranking
        ↓
Explanation
        ↓
Final Recommendation
```

---

## 24. Recommendation Signals — *Phase 3*

Potential signals:

### Content

* genre
* author
* themes
* description
* embeddings

### Collaborative

* co-read
* saves
* ratings
* completions

### Behavioral

* reading duration
* chapter completion
* abandonment
* search queries
* reading frequency

### Context

* mood
* reading goal
* time of day
* requested duration
* current reading context

---

## 25. Reading DNA — *Phase 3*

Reading DNA must be:

**derived, not manually invented.**

Example:

```text
42% Psychology
27% Philosophy
18% Fiction
13% Other

Preferred reading window:
21:00–23:00
```

Update continuously from user interactions.

The questionnaire remains useful for cold start.

It must no longer be the entire personalization system.

---

## 26. Explainable Recommendations — *Phase 3*

Every recommendation should be able to answer:

> Why this book?

Example:

> You rated several philosophical novels highly, completed three books with similar themes, and usually read this genre in the evening.

Never produce generic explanations unsupported by actual signals.

---

## 27. Semantic Search — *Phase 2*

Pipeline:

```text
Natural Language Query
        ↓
Embedding
        ↓
Vector Retrieval
        ↓
Metadata Filters
        ↓
Ranking
        ↓
Grounded Results
```

Support queries such as:

* "a short philosophical book that makes me question my life"
* "something like The Alchemist but darker"
* "a beginner-friendly book about existentialism"

The system must not invent books.

---

## 28. AI Librarian — *Phase 2–3*

The AI Librarian is a tool-using layer.

Tools:

```text
search_catalog(query, filters)
check_availability(book_id)
check_user_library(user_id)
get_reading_profile(user_id)
```

For:

> "Something like Atomic Habits but more philosophical and don't recommend books I've already read."

the system should compose:

```text
catalog search
+
library exclusion
+
profile filtering
+
ranking
```

Do not solve this using a single prompt.

---

## 29. Personal Library — *Phase 4*

Lifecycle:

```text
Discover
 ↓
Purchase legally
 ↓
User obtains legal copy
 ↓
Upload
 ↓
Validate
 ↓
Extract
 ↓
Identify
 ↓
Chapterize
 ↓
Chunk
 ↓
Embed
 ↓
Index
 ↓
READY
```

Supported initial formats:

* EPUB
* PDF
* TXT

Private by default.

---

## 30. Proof of Purchase — *Phase 4*

Support a future state:

```text
OWNED_NOT_UPLOADED
```

The user may prove ownership through:

* ISBN
* receipt
* invoice
* supported purchase evidence

This unlocks metadata/discovery features.

RAG, reading, and audio remain locked until actual usable content is supplied.

Do not build complex purchase verification infrastructure during early phases unless required.

---

## 31. AI Book Understanding — *Phase 4*

Pipeline:

```text
Document
 ↓
Text Extraction
 ↓
OCR if required
 ↓
Chapter Detection
 ↓
Chunking
 ↓
Embedding
 ↓
Structured Extraction
```

Extract:

* chapters
* summaries
* concepts
* characters
* themes
* difficulty
* estimated reading time

Never send an entire book into a giant-context prompt.

---

## 32. Reading Copilot — *Phase 5*

Pipeline:

```text
User Question
 ↓
Intent Detection
 ↓
Book-Scoped Retrieval
 ↓
Relevant Passages
 ↓
LLM
 ↓
Grounded Answer
 ↓
Citation
```

Support:

* explain chapter
* explain paragraph
* summarize progress
* character questions
* "what should I remember?"
* quizzes
* translation
* selected-text explanations

The assistant must distinguish:

```text
Known from book
Inference
Uncertain
```

---

## 33. AI Book Memory — *Phase 5*

Maintain long-term memory per:

```text
user + book
```

Do not replay raw transcripts forever.

Instead:

```text
Conversation
 ↓
Periodic summarization
 ↓
Structured memory
```

Example:

> User previously asked about the narrator's motivation in Chapter 4.

---

## 34. User Intellectual Memory — *Phase 9*

Extend memory across books.

Example:

> User has previously discussed free will while reading three different books.

Later, when a new book introduces free will:

> "This connects to a concept you explored earlier."

This should be user-controlled.

---

## 35. AI Audiobook — *Phase 6*

Preserve and evolve existing TTS implementation.

Inspect existing:

```text
backend/audio_outputs/
```

and determine the current cache strategy before replacing anything.

Target:

```text
Book
 ↓
Chapter
 ↓
Preprocess
 ↓
TTS
 ↓
Audio
 ↓
Cache
```

Cache key:

```text
hash(
    book_id,
    chapter_id,
    voice,
    speed,
    provider,
    model
)
```

Features:

* chapter-level generation
* on-demand generation
* resumable playback
* speed control
* voice selection where supported

Never generate entire books upfront.

---

## 36. Adaptive Reading Atmosphere — *Phase 6–7*

Optional licensed/royalty-free background audio.

Modes:

* Focus
* Calm
* Ambient
* Classical
* Nature

Do not bundle copyrighted music without proper rights.

---

## 37. Notes as Knowledge — *Phase 5*

Notes should eventually connect:

```text
Idea
 ↓
Concept
 ↓
Book
 ↓
Author
 ↓
Related Ideas
```

Features:

* summarize notes
* interesting ideas
* flashcards
* revision quiz
* cross-book connections

---

## 38. Study Mode — *Phase 5*

Per-book opt-in.

Features:

* summaries
* flashcards
* quizzes
* Q&A
* key terms
* spaced repetition
* progress tracking

Reuse the same RAG infrastructure.

Do not create a second independent AI knowledge system.

---

## 39. Reading Intelligence — *Phase 5 & 7*

Track:

* current book
* chapter
* page where available
* reading session
* reading duration
* reading speed
* streak
* completion estimate

Examples:

> You normally read for 25 minutes in the evening. Continue Chapter 7?

or:

> You haven't opened this book in six days. Want a three-minute recap?

---

## 40. Mood-Based Reading — *Phase 3 & 7*

Mood must be a first-class query.

Examples:

```text
"I'm stressed."

"I want something intellectually challenging."

"I need something emotional but short."
```

Route mood into the same:

```text
semantic search
+
recommendation
+
profile
```

pipeline.

---

## 41. Personalized Dashboard — *Phase 7*

Eventually include:

```text
Continue Reading
For You
Because You Read...
Trending For Your Taste
Your Library
Reading Progress
AI Insights
```

This is where backend intelligence becomes visible product value.

---

## 42. Gamification — *Phase 7 (Tier 2, optional)*

Optional and subtle:

* reading streaks
* completion count
* small number of badges

Do not let gamification dominate the product.

---

## 43. Multimodal Ingestion — *Phase 10*

Future support:

### Cover understanding

Extract:

* title
* author
* edition

using:

* OCR
* vision model

### Scanned PDFs

If text extraction is near-empty:

```text
PDF
 ↓
OCR
 ↓
Text
 ↓
Chunk
```

---

## 44. Accessibility — *Phase 7*

The product must eventually support:

* semantic HTML
* screen readers
* ARIA labels
* keyboard navigation
* adjustable text size
* line height
* contrast
* dyslexia-friendly font option
* accessible audio controls

Accessibility is part of product quality, not a cosmetic feature.

---

## 45. Internationalization — *Phase 10–11*

Architecture must be i18n-ready.

Target languages:

* English
* Persian
* Chinese
* German
* French
* Spanish
* Arabic

Support:

* RTL
* translated UI
* translated selected text
* non-English metadata search

Translation Intelligence may eventually include:

```text
Original
 ↓
Translation
 ↓
Context
 ↓
Cultural meaning
 ↓
Difficult vocabulary
```

Do not block the core product waiting for full localization.

---

## 46. Voice Reading Companion — *Phase 10*

Future experience:

> "What happened before this chapter?"

> "Why did he do that?"

> "Continue reading."

Voice interaction must use the same grounded Reading Copilot.

Voice should not become a second independent AI architecture.

---

## 47. Learning Paths — *Phase 9*

User gives a goal:

> "I want to understand philosophy."

System produces:

```text
Beginner
 ↓
Intermediate
 ↓
Advanced
```

Each book should explain:

* why it was selected
* prerequisite knowledge
* what to learn
* what comes next

Learning Paths must be generated from real catalog books and grounded metadata.

---

## 48. Reading → Learning Loop — *Phase 9*

When the system detects possible struggle:

```text
Difficulty signal
 ↓
Explanation
 ↓
Mini quiz
 ↓
Flashcards
 ↓
Reflection
 ↓
Knowledge update
```

Weak concepts can later be resurfaced.

Do not treat low engagement alone as proof of confusion.

Use multiple signals.

---

## 49. Socratic Mode — *Phase 9*

Optional learning mode.

Instead of immediately answering:

> Why does Nietzsche reject morality?

the system may respond:

> What do you think Nietzsche means by morality here?

Then:

```text
User interpretation
 ↓
Passage comparison
 ↓
Counterargument
 ↓
Reflection
```

Must remain grounded in the book.

---

## 50. Intellectual Communities — *Phase 10–11*

Future social layer.

Do not recreate Goodreads.

Use:

```text
Interpretive Questions
 ↓
Arguments
 ↓
Evidence
 ↓
Different interpretations
```

Users may compare how people with similar Reading DNA interpreted the same book.

Privacy controls must prevent unwanted exposure of personal intellectual profiles.

---

## 51. AI Architecture Map — *Foundational reference*

Use the following division:

| Technique        | Purpose                      |
| ---------------- | ---------------------------- |
| Classical ML     | candidate generation/ranking |
| Embeddings       | semantic retrieval           |
| Vector Search    | semantic and RAG retrieval   |
| RAG              | grounded book interaction    |
| LLM              | reasoning/generation         |
| Model Routing    | cost/latency control         |
| Agentic Tool Use | multi-step librarian tasks   |
| OCR/Vision       | ingestion                    |
| TTS              | audio                        |
| Analytics        | behavioral learning          |

Do not use an LLM where deterministic or classical methods are sufficient.

---

## 52. Evaluation Platform — *Phase 8*

Every important model/prompt change must be measurable.

### Recommendation

Track:

* NDCG
* Recall@K
* Diversity
* Novelty
* CTR
* Save rate
* Start rate
* Completion rate

### Search

Track:

* MRR
* Recall@K

### RAG

Track:

* groundedness
* faithfulness
* citation accuracy
* retrieval recall

### AI operations

Track:

* latency
* token usage
* estimated cost
* failure rate
* cache hit rate

Never claim an AI improvement without comparing against a baseline where possible.

---

## 53. Experimentation — *Phase 8*

The system must eventually support controlled experimentation.

Examples:

```text
Recommendation Model A
        vs
Recommendation Model B
```

Measure:

* click-through
* save
* open
* completion
* satisfaction
* downstream recommendation quality

Do not blindly deploy a new ranking model because offline metrics improved.

---

## 54. Data Moat — *Phase 1 onward*

Track product events from day one.

Potential events:

```text
search
book_view
recommendation_view
recommendation_click
save
purchase_intent
library_add
upload
reading_start
reading_progress
reading_complete
question
highlight
note
quiz
study_session
audio_start
audio_complete
```

Only use data according to user consent, privacy requirements, and applicable law.

Private book contents must remain isolated.

---

## 55. Security and Privacy — *Foundational / Phase 1*

Implement:

* authentication
* authorization
* user isolation
* secure uploads
* file size limits
* MIME validation
* extension validation
* malware scanning where feasible
* signed URLs
* rate limiting
* secret management
* encryption where appropriate

Never expose private files publicly.

Support:

* data export
* account deletion
* memory deletion
* individual memory controls
* private library controls

Product positioning:

> **Your reading data belongs to you.**

---

## 56. Business Model — *Phase 11*

Potential model:

### Free

* discovery
* basic recommendations
* limited AI
* basic library

### Pro

Approximately:

```text
$8–15/month
```

Potential:

* advanced Reading DNA
* unlimited/expanded AI Librarian
* RAG
* Book Memory
* Study Mode
* AI Voice
* Learning Paths

### Premium

Potential:

* deep research
* advanced learning
* large private library
* higher usage limits

### Revenue

Potential:

* affiliate purchase commissions
* subscriptions
* premium AI usage

Do not build full billing infrastructure during early development unless required.

---

## 57. API Strategy — *Phase 11*

Potential public APIs later:

```text
/search
/recommend
/books/{id}
/semantic-search
```

Do not build the public API platform before the internal product is stable.

---

## 58. Modular Monolith — *Phase 1*

Target:

```text
FastAPI
│
├── auth
├── books
├── search
├── recommendation
├── library
├── reading
├── ai
├── audio
└── analytics
```

Each module must have:

* clear responsibilities
* interfaces
* minimal coupling
* testable boundaries

Do not turn these into independent services prematurely.

---

## 59. Background Jobs — *Phase 1 & 4*

Long-running tasks must not block API requests.

Examples:

```text
Upload
 ↓
Job Queue
 ↓
Extract
 ↓
Chapterize
 ↓
Chunk
 ↓
Embed
 ↓
Index
 ↓
READY
```

Same approach for:

* TTS
* summarization
* OCR
* embedding generation
* indexing

---

## 60. Agent Execution Protocol — CRITICAL — *Foundational process*

Before touching code:

### Step 1 — Inspect

Understand the repository.

### Step 2 — Report

Produce the required architecture analysis.

### Step 3 — Identify risk

List:

* fragile areas
* unknowns
* breaking-change risks
* security risks
* data migration risks

### Step 4 — Design

Propose the smallest safe architectural change.

### Step 5 — Verify compatibility

Determine how existing behavior remains functional.

### Step 6 — Implement one bounded change

Do not combine unrelated migrations.

### Step 7 — Test

Run:

* unit tests
* integration tests where applicable
* existing application startup
* relevant manual flows

### Step 8 — Verify

Confirm:

* existing features still work
* new functionality works
* fallback works
* errors are handled
* no secrets are exposed

### Step 9 — Report

Provide:

* changed files
* tests
* migration impact
* risks
* next step

### Step 10 — Stop

Do not automatically continue into unrelated phases.

---

## 61. One Deliberate Change at a Time — *Foundational*

Never simultaneously:

* rewrite recommendation
* migrate database
* rebuild frontend
* change authentication
* change deployment

unless the changes are explicitly required for the same bounded migration.

Sequence them.

---

## 62. Definition of Done — *Foundational*

A phase or feature is NOT complete merely because code exists.

A feature is complete only when:

1. The implementation works.
2. Existing relevant behavior is preserved.
3. Tests exist or the reason for missing tests is documented.
4. Error states are handled.
5. External-provider failures are handled.
6. Configuration is environment-driven.
7. Security boundaries are enforced.
8. Observability exists where appropriate.
9. Documentation is updated.
10. The feature is manually demoable.
11. No unnecessary infrastructure was introduced.

For AI features additionally:

12. Evaluation criteria exist.
13. Grounding/retrieval behavior is tested.
14. Cost/latency is measurable.
15. Provider fallback exists where practical.

---

## 63. No Silent Architectural Decisions — *Foundational*

If implementation requires a choice not specified here:

1. Identify the decision.
2. Explain alternatives.
3. Recommend one.
4. State the reason.
5. Record the decision.

Do not silently introduce major infrastructure or architectural patterns.

---

## 64. Backward Compatibility — *Foundational*

Whenever possible:

```text
Old API
   ↓
Compatibility layer
   ↓
New service
```

Migrate callers gradually.

Do not break the entire application to introduce a cleaner architecture.

---

## 65. Migration Strategy — *Phase 1–6*

Existing systems should evolve in place.

### Existing Recommender

```text
Current Recommender
 ↓
Extract interfaces
 ↓
Preserve current behavior
 ↓
Add new candidate signals
 ↓
Add ranking layer
 ↓
Optional LLM reranking
```

### GutenbergClient

Preserve and adapt into:

```text
BookProvider
```

or:

```text
GutenbergProvider
```

where appropriate.

### Existing TTS

Inspect:

```text
backend/audio_outputs/
```

Preserve useful caching behavior.

Gradually move to provider-independent:

```text
TTSProvider
```

### Questionnaire

Do not delete it.

Evolve:

```text
Questionnaire
+
Conversational onboarding
+
Behavioral signals
```

Questionnaire becomes the cold-start mechanism.

---

## 66. Frontend Migration — *Phase 7*

Do NOT immediately rewrite the frontend.

First stabilize backend architecture.

Then migrate incrementally:

```text
Existing HTML/JS
 ↓
Shared design system
 ↓
Componentized UI
 ↓
React/Next.js
 ↓
Full application
```

Existing pages should remain functional during migration where practical.

---

## 67. Recommended Repository Evolution — *Phase 1*

Adapt this to actual repository reality:

```text
digital-library/
│
├── frontend/
│
├── backend/
│   ├── api/
│   ├── models/
│   ├── repositories/
│   ├── services/
│   │   ├── recommendation/
│   │   ├── search/
│   │   ├── books/
│   │   ├── library/
│   │   ├── reading/
│   │   ├── audio/
│   │   ├── ai/
│   │   └── auth/
│   ├── engine.py
│   └── main.py
│
├── data/
├── scripts/
├── tests/
├── docs/
├── docker/
├── .env.example
├── docker-compose.yml
├── README.md
└── RUN.md
```

Do not blindly create this structure.

Adapt it to the actual repository.

---

## 68. Phased Roadmap

### Phase 0 — Audit (§1–4, §70, §72)

Deliver:

* repository map
* architecture analysis
* technical debt inventory
* dependency inventory
* data schema inventory
* security risks
* migration risks
* reusable components
* obsolete components
* architecture proposal

No destructive code changes.

### Phase 1 — Foundation (§9, §10, §14–16, §20, §21, §54, §55, §58, §59, §67)

Implement:

* PostgreSQL
* authentication foundation
* provider abstraction
* LocalCatalogProvider
* initial database schema
* configuration management
* logging
* tests
* health checks

Preserve existing application behavior.

### Phase 2 — Book Intelligence (§17–19, §22, §27)

Implement:

* Google Books adapter
* Open Library adapter
* normalization
* ISBN reconciliation
* book database
* embeddings
* pgvector
* semantic search

### Phase 3 — AI Recommendation (§23–26, §28, §40)

Implement:

* behavioral event capture
* hybrid candidate generation
* classical ranking
* Reading DNA
* explainable recommendations
* optional LLM reranking

### Phase 4 — Personal Library (§21–22, §29–31, §59)

Implement:

* secure upload
* ownership state
* extraction
* chapter detection
* chunking
* embeddings
* private retrieval

### Phase 5 — AI Reading (§32–33, §37–39)

Implement:

* Reading Copilot
* Book Memory
* notes
* Study Mode
* grounded citations

### Phase 6 — Audio (§35–36)

Implement:

* provider abstraction
* chapter TTS
* caching
* resumable playback
* speed/voice options

### Phase 7 — Product Experience (§36, §39–42, §44, §66)

Implement:

* dashboard
* Reading Mode
* onboarding
* design system
* accessibility baseline
* progressive frontend migration

### Phase 8 — Evaluation and Observability (§15, §52–53)

Implement:

* recommendation evaluation
* semantic search evaluation
* RAG evaluation
* AI cost monitoring
* latency monitoring
* failure monitoring
* cache monitoring
* experimentation framework

### Phase 9 — Intelligence Layer (§34, §47–49)

Implement:

* Knowledge Graph
* Learning Paths
* Socratic Mode
* Reading → Learning Loop

### Phase 10 — Experience Layer (§43, §45–46, §50)

Implement:

* Voice Companion
* multimodal ingestion
* Translation Intelligence
* Intellectual Communities

### Phase 11 — Platform (§45, §50, §56–57)

Potential:

* public API
* B2B
* payments
* affiliate ecosystem
* internationalization
* enterprise capabilities

Only when justified by product evidence.

### Phase 12 — Global Demo (every Tier‑1 section)

Create one polished end-to-end experience:

```text
Conversational onboarding
        ↓
Natural-language discovery
        ↓
Semantic search
        ↓
Hybrid recommendation
        ↓
Explainable recommendation
        ↓
Real availability
        ↓
Legal acquisition
        ↓
Private upload
        ↓
Book identification
        ↓
Chapterization
        ↓
RAG indexing
        ↓
Reading Mode
        ↓
Audio
        ↓
Ambient mode
        ↓
Reading Copilot
        ↓
Notes
        ↓
Chapter summary
        ↓
Reading DNA update
        ↓
Next recommendation
```

The demo must survive provider/network failure.

---

## 69. Competition Differentiation — *Foundational*

The final product should be able to communicate:

### Old DigiKitab

```text
Questionnaire
 ↓
ML
 ↓
Book Recommendation
```

### New DigiKitab

```text
Understand User
 ↓
Discover Books
 ↓
Explain Recommendations
 ↓
Acquire Legally
 ↓
Private Library
 ↓
Understand Book
 ↓
Read
 ↓
Ask AI
 ↓
Take Notes
 ↓
Learn
 ↓
Remember
 ↓
Update Reading DNA
 ↓
Recommend Better Books
```

The improvement is not:

> "We added more features."

The improvement is:

> **We closed the reading intelligence loop.**

---

## 70. Current Architecture Analysis Deliverables — *Phase 0*

Before implementation, produce the following.

### A — Current Architecture Analysis

Ground this strictly in actual repository inspection.

Inspect:

* backend
* frontend
* process layer
* datasets
* notebooks
* TTS
* state
* configuration
* dependencies
* tests

Explicitly identify assumptions that were wrong.

### B — Architecture Weaknesses

For every weakness classify:

```text
Preserve
Replace
Refactor
Defer
```

Explain why.

### C — Proposed Architecture

Provide:

1. architecture diagram
2. module boundaries
3. data flow
4. provider boundaries
5. AI flow
6. storage flow
7. background job flow

Adapt the architecture to actual repository findings.

### D — Technology Decisions

Justify:

* PostgreSQL
* pgvector
* Redis
* FastAPI
* frontend strategy
* provider abstractions
* object storage
* job system

Do not provide generic textbook explanations.

Tie every choice to repository reality.

### E — API Strategy

Specify:

* Google Books
* Open Library
* availability providers
* adapter interfaces
* normalization
* fallback behavior
* rate limiting
* caching

### F — Database Schema

Provide:

* entities
* fields
* relationships
* indexes
* ownership boundaries
* vector architecture
* migration order

Clearly distinguish:

```text
Core
Intelligence
Observability
```

### G — AI Architecture

Explain exactly where:

* classical ML
* embeddings
* vector search
* RAG
* LLM
* model routing
* agentic tools
* OCR
* TTS

will live in THIS codebase.

### H — Migration Plan

Explain how:

* `Recommender`
* `GutenbergClient`
* questionnaire
* TTS caching
* existing state
* frontend

evolve without unnecessary breakage.

### I — Development Roadmap

For every phase provide:

* objective
* scope
* dependencies
* deliverables
* Definition of Done
* tests
* demo scenario
* risks
* what is explicitly NOT included

### J — Competition Differentiation

Explain briefly and honestly:

1. What the 3rd-place version already did well.
2. What remains technically valuable.
3. What the new product changes.
4. Why the new architecture is materially stronger.
5. What the actual moat is.
6. Why this is more than "adding AI features."

---

## 71. First Pull Request Requirement — *Phase 0–1*

After the audit, propose the **smallest safe first PR**.

Do not assume what it should be before inspection.

The first PR should generally be limited to Phase 0/Phase 1 foundation work such as:

* repository documentation
* architecture documentation
* test baseline
* configuration cleanup
* health checks
* provider interface skeleton
* database foundation

But the exact PR must be determined by actual audit findings.

Do not combine unrelated migrations.

---

## 72. What Must Be Requested From the Product Owner — *Phase 0*

At the end of the audit, explicitly state what is required from me before implementation.

Potential requirements:

* Google Books API key
* chosen LLM provider
* embedding provider
* TTS provider
* object storage provider
* database hosting
* Redis hosting
* deployment target
* domain
* authentication preferences
* availability/affiliate provider approvals
* expected traffic
* budget constraints
* privacy requirements
* hosting geography
* whether the first target market is Persian, English, or multilingual

Do not ask for credentials that are not actually required yet.

---

## 73. Final Instruction — *Foundational process*

**DO NOT WRITE IMPLEMENTATION CODE IN THE FIRST RESPONSE.**

The first response must contain only:

```text
A — Current Architecture Analysis
B — Architecture Weaknesses
C — Proposed Architecture
D — Technology Decisions
E — API Strategy
F — Database Schema
G — AI Architecture
H — Migration Plan
I — Development Roadmap
J — Competition Differentiation
```

The analysis must be based on actual repository inspection.

Explicitly flag incorrect assumptions.

Do not pretend to have inspected files that you could not access.

Do not modify the repository before producing this analysis.

After presenting A–J, end with:

1. Proposed first PR
2. Why it is the correct first PR
3. Files/modules expected to change
4. Tests required
5. Risks
6. Information/credentials required from the product owner

Then STOP and wait for approval before making major implementation changes.

**The objective is not to make DigiKitab larger.**

The objective is to make DigiKitab **more intelligent, more coherent, more reliable, more defensible, and capable of becoming a real global product.**