# Archive

Files kept for the record, reachable by no running code.

## `app_state.json`

The persistence layer of a pre-Postgres version of the app: a flat JSON
file holding `progress` rows keyed by the **prior** book-ID scheme.

Orphaned since before this repository's first commit — no code reads or
writes it (verified repo-wide). Logged as **F-10** in
[PHASE-0-AUDIT.md](../PHASE-0-AUDIT.md), which calls it *"evidence, not a
source"* and says to archive rather than import: section H.6 declines to
build a migration path for data no running code produces.

Kept rather than deleted because it holds real `guest` reading-progress
records with real timestamps. If those rows are ever wanted, the ID
scheme has to be reconciled first — see H.1.

Moved here from `backend/` on 2026-09-08 by the structure cleanup.
