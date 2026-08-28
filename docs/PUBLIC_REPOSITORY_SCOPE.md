# Public repository scope

The public GitHub repository is the code and provenance companion to the data
archive. It should contain the generation and audit scripts, synchronized
manuscript sources, figures, schemas, rule metadata, licenses, documentation,
machine-readable receipts, and the release instructions. The complete JSONL
tables and solver arrays belong in the separately archived data record and are
excluded from ordinary Git history by the root `.gitignore`.

The repository must not contain `.env` files, access tokens, local caches,
installed dependency trees, model checkpoints, build directories, or temporary
archives. The current README records the public repository URL and archived
code-release DOI; the data DOI, release tag, and access date must be added or
updated when the formal data deposition is completed.

Before publishing, inspect the staged file list and verify that no file larger
than GitHub's ordinary per-file limit is included. The release archive itself
is retained locally for DOI deposition and is ignored by Git.
