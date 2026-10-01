# GridInstruct revision status, 1 October 2026

The current manuscript targets Energy & AI and presents a data-generation
method. Manuscripts, artwork and human-review material remain local and outside
Git. Code, experiment protocols and reports are versioned; the raw data,
predictions and small trained adapters are distributed in the matching release
archive. The immutable release identifier is `v1.2-eai-20261001`.

## Generation and electrical interpretation

The manuscript now separates AC balance equations from acceptance limits and
includes the native device constraints in the OPF model. Its security equation
describes the released topology-dependent control policy. The supplementary
common-command probe contains two full 7,680-state ledgers: 5,457 and 5,456
states satisfy the respective electrical limits. No base command passes all
registered stress states. These results explain the role of contingency-specific
controls; they do not establish a common preventive command. The candidate
mapping distinguishes 35,200 registered IEEE14 candidates, 136 robust-screened
candidates, 120 accepted candidates, 80 selected IEEE14 cases and 80 IEEE118
cases, yielding 320 records over 160 scenarios.

The 95,479-record denominator is retained alongside 8,823 structured
configurations and 4,968 abstract patterns. Compliance covers 312 structured
configurations. Language variants are not counted as independently generated
electrical configurations. The official split, five-key source split, wording
split and template-family split retain their different purposes and overlap
diagnostics. Exact wording isolation is not treated as semantic independence.

## Model-training evidence

A pinned pretrained SmolLM2-135M-Instruct model is evaluated before and after
supervised low-rank adaptation. Fixed strict-source samples contain 768 training,
192 validation and 288 test records; the test covers 164 scenarios and is
label-balanced. Three declared seeds raise macro-F1 from 0.160985 to a mean
0.823423 with sample standard deviation 0.045678. The same-sample TF-IDF
reference reaches 0.911586. The tuned request-only mean is 0.391249.

The state-change diagnostic contains 32 exact-request pairs. Its both-correct
counts are 0 before adaptation and 2, 0 and 5 after adaptation. A separate
controlled request diagnostic renders two requests over each of 32 unchanged
strict-test source inputs. Both-correct counts are 11 before adaptation and
14, 22 and 16 after adaptation. These small diagnostics retain their own
denominators. The target labels derive from the synthetic compliance contract;
the experiment does not measure free-form executable control or live dispatch.

An independent reviewer found a saved-adapter precision issue. Loading the
single-precision adapter tensors before conversion had changed predictions.
The evaluator now creates single-precision adapter parameters before loading.
All three saved adapters reproduce their 350 main-test and state-probe labels,
with zero changed labels. Raw-prediction metric recomputation, provenance
isolation, checkpoint selection and before/after input equality pass. The
remaining audit warning concerns scientific scope, not an unrepaired score.

## Human review and public access

The authors confirm completed external review by five PhD-level reviewers.
This is an author attestation. Individual judgments, sampling counts and
agreement statistics were not supplied; no such records or statistics have
been fabricated. Completion of the package review does not retroactively
change stored row-level review-status fields.

The repository is public and an unauthenticated HTTP request returned 200.
The versioned archive download is verified separately after upload. Zenodo and
new DOIs are not part of the active route. Full-population solver arrays,
utility deployment evidence and measured human agreement remain unavailable.

## Editorial and reproducibility work

The abstract follows background, method and validation in one 236-word
paragraph. The manuscript has five tables and 35 references. Generated data and
coverage share one section, the end-to-end case closes the validation section,
and conclusions precede availability and author declarations. Acronym first
use, stale access statements and stale no-training statements are corrected.
The new transfer and calibration figures were rendered and checked in the PDF.
The regression suite passes 163 tests with one skipped test.

Archive replay reads members directly and extracts only small required scripts.
It avoids whole-archive duplicate extraction. Newly downloaded base-model
weights were removed after replay verification; the pinned model can be
downloaded again using the experiment instructions. Small adapters and all
raw predictions remain available. No remote GPU was used; training used local
MPS and four CPU threads.
