# F-20 — persisting the recommender fit: scope

**Written for:** the product owner, to agree an approach before any code is
written. No implementation here, and no repeated fits were run to produce it
— the measurements below come from one instrumented boot.

**Status:** proposal. It ends with four questions that need answering, and
one recommendation that would make most of the work unnecessary.

---

## 1. What actually happens on every boot

One instrumented startup, stages over 0.25s:

| stage | cost | device | persisted today |
|---|---|---|---|
| Load catalogue (29,975 rows from JSON) | 1.9s | CPU | no |
| Read stored vectors from Postgres, (29975, 384) | **16.5s** | CPU / IO | n/a — this *is* the read |
| SentenceTransformer model load (for query encoding, F-46) | 2.8s | GPU | n/a — pretrained |
| Feature matrix (29975, 57) + SVD (29.20% var.) | 0.7s | CPU | no |
| KMeans, k searched, best k=13 | 2.3s | CPU | no |
| LTR train, 24,000 seed-relative rows, val R² 1.0000 | **14.8s** | CPU | no |
| genre-popularity factors (29975, 15), ANN index, comment SVD, chat classifier | ~1.0s | CPU | no |
| **total** | **40.0s** | | |

Two stages are 31.3s of the 40.0s: reading the content vectors out of
Postgres, and training the LTR. Everything else together is under 9s.

**A correction worth recording, because it would have aimed this work at the
wrong thing.** I first read the 19.3s block as the catalogue being
re-encoded, and drafted a recommendation to "read the stored vectors instead
of re-encoding them". That was wrong: `lifespan.fit_ml` already calls
`store.load_content_vectors`, and measuring the two halves separately shows
16.5s of it is the Postgres read itself and 2.8s is loading the MiniLM
weights for query encoding. Nothing re-encodes 29,975 books at boot. The
opportunity is real but it is a different one — see §4.

The original F-20 note said nothing is persisted. That is half right, and the
half that is wrong is the useful half: `ml/embeddings.py` already joblib-dumps
the LSA space to `models/lsa_{dim}.joblib`, and does it correctly. That is the
template for this work, not a thing to design afresh.

## 2. The cost is latency, not correctness — until you persist

Today a boot is slow but always self-consistent: everything is fitted from
whatever the catalogue currently is. The moment an artefact is loaded from
disk, a new failure becomes possible — **serving a ranker fitted to data that
no longer exists**, silently, with no error and plausible-looking output.
That is the whole risk of this change, and it is worse than a 40s boot.

`LsaBackend` already solved this, for exactly this reason (F-35). Its model
identity is not `"lsa"` but `f"lsa:{fingerprint}"`, where the fingerprint is a
blake2b digest of the learned SVD components:

> *"Unlike a pretrained model, an LSA space is defined by the corpus it was
> fitted on — so the corpus has to be part of the identity."*

Any artefact added here must carry the same kind of identity, and the loader
must refuse a mismatch rather than cope with it.

## 3. What a fingerprint has to cover

This is where the correctness lives, so it is worth enumerating rather than
hand-waving. `Recommender.fit()` consumes:

1. **The catalogue rows** — not the row count. Two catalogues of 29,975 rows
   with different descriptions produce different clusters and a different
   feature matrix. A count or an mtime is not enough; it needs a digest of
   the columns actually used.
2. **The content vectors** — their shape *and* which embedding backend
   produced them. `content_space` is already recorded as `"minilm"` or
   `"tfidf_svd"`, and the backend already exposes `name`. Mixing spaces is
   F-35 again.
3. **Comment and reading-depth data** — `comment_score` feeds the LTR
   features, and `reading_depth_n` / `reading_depth` define the LTR *target*
   (§5). Both change without the catalogue changing at all, which makes them
   the easiest invalidation trigger to forget.
4. **Hyperparameters and code** — the k range KMeans searches, SVD
   components, LTR parameters, the feature list, the target formula. A code
   change that alters any of these must invalidate the artefact, and nothing
   about the data will tell you it did.

Items 3 and 4 are the ones that make this more than an afternoon. (4) in
particular has no natural data-derived signal; the honest options are a
hand-maintained version constant that someone must remember to bump, or a
digest over the relevant source, which is accurate but will invalidate on
comment-only edits.

## 4. What should and should not be persisted

Worth persisting — deterministic given their inputs, and slow:

- the KMeans model and the `cluster` column (2.3s)
- the feature-engineer pipeline and its SVD (0.7s)
- the genre-popularity factors and the ANN index (~1s)
- the comment-embedder SVD and the chat intent classifier (~1s)

Worth persisting, and the largest single win:

- **The assembled content-vector matrix (16.5s).** This is the biggest stage
  of the boot, and it is a database read, not a computation: 29,975 rows each
  carrying a 384-float array, reassembled into one (29975, 384) matrix.
  float32 at that shape is ~46 MB, and 16.5s to materialise 46 MB from a
  local Postgres is dominated by per-row deserialisation, not by the data.
  Cached as a single `.npy` beside the LSA artefact it loads in well under a
  second.

  It is also the *easiest* artefact to invalidate correctly, which is
  unusual for the biggest win. `load_content_vectors` is already all-or-
  nothing by design (F-44: a missing vector would be filled with zeros and
  that book would silently never be anyone's neighbour), and its report
  already carries `requested`, `found`, `missing` and `models`. A manifest of
  row count, row-id digest and `models` is sufficient, and all of it is
  already computed.

Not worth persisting:

- **The MiniLM model load (2.8s).** Pretrained weights, needed at runtime to
  encode queries (F-46). Nothing to cache that is not already a file.
- The engine wrappers (`AudiobookEngine`, `CommentEngine`, `ReminderEngine`,
  `ChatbotEngine`) — cheap to construct and they hold live references.

Note that this is the only GPU stage, and it is 2.8s of weight loading rather
than a 30,000-item encode. The GPU-thermal limit is therefore barely engaged
by this work at all.

## 5. The LTR stage, and why I would not persist it

The second-largest stage is 14.8s, and `val R² 1.0000` is not a good sign —
a learned ranker does not legitimately achieve a perfect validation score.
Reading the code explains it exactly.

The target (`_relevance`) is:

```
relevance = (n · depth + k · prior) / (n + k)
prior     = 0.4 · (average_rating / 5) + 0.6 · (log1p(ratings_count) / count_scale)
```

With no readers, `n = 0` and this returns `prior` **exactly**. The current
reading-depth state, from the same boot log, is
`books_with_readers: 0, total_starts: 0`. So the target is the prior today.

And the LTR's own feature matrix (`ml/ranking.py:_features`) includes, as
columns 4 and 10 of 10:

```
cands["average_rating"] / 5.0
np.log1p(cands["ratings_count"])
```

The target is therefore an exact linear combination of two of the model's own
input features. A gradient-boosted tree fits that to arbitrary precision,
which is what R² = 1.0000 is reporting. **The most expensive CPU stage of
every boot is spending 14.8s learning a two-term formula it was handed.**

This is the F-54 shape again ("filling them would have been building a
feature to justify a bug"), and the file already documents the neighbouring
lesson: the F-26 comment in `train()` explains that four features were once
constant and therefore dead weight. This is the same class of problem from
the other direction — not a dead feature, a leaked target.

It resolves itself once real readers exist: `shrink(depth, readers, prior)`
moves the target away from the prior as `n` grows, and then the model has
something to learn. Until then, persisting it preserves a formula in a
24,000-row artefact.

**Recommendation:** do not persist the LTR. Either skip training while
`books_with_readers == 0` and evaluate the prior directly — which removes
14.8s from every boot and changes no ranking, because the model currently
reproduces the prior — or treat the leakage as the bug and remove
`average_rating`/`ratings_count` from the features, which changes rankings
and needs golden-baseline review. The first is a strict improvement and is
cheap; the second is a modelling decision.

### This also touches F-67

The prior is computed from `average_rating` and `ratings_count`. Per F-67,
**64.5% of those values are fabricated** — 13,035 rows carry a rating with
`ratings_count = 0`, and all 6,307 Gutenberg rows carry a flat 4.0/100. So
while there are no readers, the definition of "relevance" driving the ranker
is a formula over largely imputed numbers. F-67 is not only a display
problem; it is in the training target. Whatever is decided for F-67 should be
decided before anyone tunes this.

## 6. Proposed design, if it goes ahead

Follow `LsaBackend` rather than invent a second pattern.

- **Location:** `models/recommender_{version}.joblib`, beside the LSA
  artefact, with `MODEL_DIR` already defined.
- **Contents:** the artefacts from §4, plus a manifest: fingerprint, row
  count, `content_space`, embedding-backend `name`, hyperparameter version,
  and the fitted-at timestamp.
- **Load path:** `load_or_fit()`. Validate the manifest against the live
  inputs; on any mismatch, log *which* field differed and fit. Never
  partially adopt an artefact — load all of it or none.
- **Fail open, not closed.** A corrupt or unreadable artefact must fall back
  to fitting, not prevent startup. Section 12's principle, and the same one
  `fit()` already applies to content vectors ("a failed load degrades content
  similarity instead of preventing the app from starting").
- **An explicit way to refit:** a flag or a small CLI entry, as
  `embed_pass --fit` already is for LSA.

### Testing

The hard part to test is invalidation, and the tests must not each pay a fit:

- Manifest-mismatch cases are pure unit tests — build a manifest, mutate one
  field, assert the loader refuses and names that field. No fitting needed,
  and this is where most of the risk is.
- One real fit-save-load round trip, asserting that a loaded model produces
  **identical** rankings to the freshly fitted one for a fixed set of seeds.
  That is the only test that needs a fit, and it is the one that would catch
  a silently wrong artefact.
- A guard that a stale artefact plus a changed catalogue does not serve the
  stale one — the F-20 failure mode stated as a test.

### The GPU-thermal constraint

Barely engaged. The only GPU work at boot is loading the MiniLM weights
(2.8s), and it is unaffected by any of this. The vector-matrix cache, the
KMeans/feature/ANN artefacts and the LTR decision are all CPU and IO. The
round-trip test in §6 needs one real fit; nothing here needs a repeated GPU
pass.

## 7. What I would do, in order

1. **Skip LTR training while there are no readers** — removes 14.8s, needs
   no artefact and no invalidation at all, and changes no ranking, because
   the model currently reproduces the prior exactly (§5). Strictly the
   cheapest and safest of the three.
2. **Cache the assembled content-vector matrix** — removes ~16s, the largest
   stage. This *is* artefact persistence, but of the one artefact whose
   invalidation inputs are already computed and already all-or-nothing (§4).
3. **Then re-measure, and decide whether the rest of F-20 is worth it.**
   After 1 and 2 the boot is ~9s, of which under 4s is actual fitting
   (feature matrix, KMeans, genre-popularity, comment SVD, chat classifier).
   Persisting *those* means an artefact format, a fingerprint covering
   catalogue content, comment and depth data, and hyperparameters, plus a
   hand-maintained code version — to save under 4s, while adding a way to
   serve a model fitted to data that no longer exists.

My recommendation is to do 1 and 2 and then very probably close F-20 as "no
longer worth doing". That is a legitimate outcome: the finding was real, the
measurement changed what it implies, and the remaining prize does not look
worth the new failure mode.

## 8. Questions for you

1. **Order:** take 1 and 2 from §7 and re-measure — my recommendation — or
   build full artefact persistence as F-20 was originally framed?
2. **The LTR leak:** skip training while `n = 0` (no ranking change), or
   treat the leakage as a bug and drop the two leaking features (ranking
   changes, golden baselines move)?
3. **F-67 first?** The LTR target is built from the fabricated ratings. Worth
   settling F-67 before touching the ranker.
4. **Code-change invalidation:** if persistence does go ahead — a
   hand-bumped version constant (forgettable, precise) or a source digest
   (automatic, invalidates on comment edits)?
