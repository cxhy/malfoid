# 01: 系列根、书子 work、术语主表

**What to build:** A translator can create one 系列 root that lists 书 in reading order, and each 书 is a sub-work that can be translated independently. The series glossary master is copied into each book as a working copy and can be merged back after the book is delivered. Legacy flat works keep working unchanged.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] A series root carries series metadata: title, author, ordered book list, and status.
- [x] Each book sub-work has its own metadata, source area, work state and translation output.
- [x] A book can be resolved from the series root by its number, and a missing book can be created.
- [x] The series glossary master copies into a book glossary; book-level terms can be merged back into the master.
- [x] Legacy flat single-book works resolve and behave exactly as before.
- [x] Offline tests cover series creation, book resolution, glossary copy and merge, and legacy compatibility.

## Comments

- Implemented in commit `bbbe77c`. Tests: `tests/test_book_state.py` (11 passed); full suite 55 passed; ruff check/format clean.
- Review fixes: series status added; series root naming helper; master glossary stays authoritative (book target conflicts recorded); book copy preserves existing book terms; idempotent merge counts; stray book directories rejected.
- Deferred: story-bible merge belongs to ticket 07.
