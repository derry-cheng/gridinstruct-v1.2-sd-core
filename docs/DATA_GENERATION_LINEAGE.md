# GridInstruct Data Lineage

Generated: 2026-08-29

The current local package is a promoted, hash-bound release. The authoritative
record table is `data/gridinstruct_v1.2_sd_core_en.jsonl`; official and stress
split files are the English projections under `data/`. The current revision
manifest records the exact hashes used by the release:
`metadata/current_revision_manifest_v1.2_sd_core.json`.

The intermediate construction JSONL stages, raw scenario arrays, and completed
human-review outcomes are intentionally absent
from this compact local package. Historical reports retain their own output
hashes and are not promoted to fresh replay evidence. This boundary is part of
the release contract, not an undocumented deletion.

| Layer | Current artifact | Evidence status |
| --- | --- | --- |
| Canonical records | `data/gridinstruct_v1.2_sd_core.jsonl`, `data/gridinstruct_v1.2_sd_core_en.jsonl` | Present; 95,479 rows; SHA-256 bound |
| Dispatcher request contract | `scripts/repair_dispatcher_request_contract.py`, `reports/dispatcher_request_contract_repair_v1.2_sd_core.json` | Present; 19,773 intent rows re-rendered with zero conflicting prompt--input groups and zero official cross-split duplicates |
| Official splits | `data/v1.2_sd_core_{train,validation,test,ood_test}_en.jsonl` | Present; record IDs and exact normalized content are disjoint across development files, registered OOD strata are held out, and development-key overlaps are reported |
| Strict source-group split | `data/v1.2_sd_core_strict_{train,validation,test}.jsonl` | Present; five provenance keys are disjoint across train/validation/test |
| Stress splits | strict source-group, challenge, atomic template-family ID-only manifests, proxy-reduced English files | Present; the legacy full template-surface projections are historical and are not shipped |
| Rule and schema metadata | `metadata/schema.json`, `metadata/rule_dictionary.csv`, `rules/regulation_rules.json` | Present; current hashes are recorded in the revision manifest |
| Physical/source reports | `reports/full_n1_enumeration_v1.2_sd_core.json`, `reports/equipment_rating_provenance_v1.2_sd_core.json`, and external-topology reports | Present as hash-bound summaries; raw scenario tables pending |
| OPF release-field audit | `reports/current_opf_closed_loop_audit_v1.2_sd_core.json` | Pass for 320 records, including detailed N-1, stress, and reduction checks |
| Independent fixed-control replay | `reports/independent_solver_validation_v1.2_sd_core_rebound.json` | Pass for 160/160 registered IEEE14/IEEE118 fixed-control AC replays; scope excludes independent OPF and full-population reconstruction |
| Independent OPF envelope | `reports/independent_opf_envelope_v1.2_sd_core.json` | Pass for 9/9 selected pandapower AC-OPF solves; diagnostic only |
| PGLib family envelope | `reports/pglib_network_envelope_replay_v1.2_sd_core.json` | 26/33 attempts converge across 11 pinned families; seven non-convergent attempts retained in the denominator |
| Template-family holdout | `reports/template_family_holdout_v1.2_sd_core.json` | Pass; 38,775/5,557/11,089 records with zero task--family group overlap and compliance macro-F1 0.944 |
| External review | Submission-side review records are maintained outside the code/data repository | No row-level human judgments are distributed in this release |

Reconstruction of deleted intermediate stages is possible only from the
original external inputs and the generator scripts. Rebuilding those stages
does not change the current release hash unless a new revision is explicitly
promoted and audited.
