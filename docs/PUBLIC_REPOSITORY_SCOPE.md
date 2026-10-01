# Public repository scope

The GitHub repository contains generation and validation code, experiment
protocols, schemas, rule and source registries, licences, documentation, and
numerical reports. Complete record tables, prediction files, and selected
solver arrays are distributed in the data-only release attachment and excluded
from ordinary Git history. Submission manuscripts, publication artwork, author
declarations, and human-review records remain local.

The repository must not contain `.env` files, access tokens, local caches,
installed dependency trees, model checkpoints, build directories, or temporary
archives. Each release uses an immutable Git tag and an associated data-only
archive. Repository visibility and successful anonymous downloads are checked
before a release is described as publicly available. Zenodo and a data DOI are
outside the selected GitHub publication route.

Before publishing, inspect the staged file list and verify that no file larger
than GitHub's ordinary per-file limit is included. The release archive itself
is rebuilt from its declared member list and uploaded as a GitHub release
attachment. Local archive copies are reproducible build products ignored by Git.
