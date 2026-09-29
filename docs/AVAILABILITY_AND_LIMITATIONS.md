# Availability And Limitations

## Data Availability

The current release snapshot is the directly rendered English table `data/gridinstruct_v1.2_sd_core_en.jsonl`. The review archive also contains the full official and strict projections, the instruction-surface development projections, the converged scenario registry, and the bounded OPF evidence tables. The public GitHub repository is https://github.com/derry-cheng/gridinstruct-v1.2-sd-core and is the data and code access point for the Energy & AI submission route.

## Code Availability

The code repository and MIT licence are recorded in the manuscript and release metadata. No archive DOI is required for the current GitHub-based submission route.

## License

Local code/data licenses are declared in `LICENSE-CODE`, `LICENSE-DATA`, and `docs/LICENSES_AND_CITATION.md`. Release metadata should point those licenses at the final public archive identifiers.

## Sensitive Or Restricted Content

The dataset uses public IEEE benchmark systems and synthetic simulation states. It should not contain real grid operating records or secrets. Long copyrighted regulation text is not republished; records use rule identifiers and short task-oriented summaries.

## Known Limitations

0. External human review status is `external_human_review_ready` with 0/1600 complete assignments; machine-assisted screening is not counted as human expert evidence.
1. 保持 GitHub release 与稿件版本一致；如期刊后续要求独立数据 DOI，再单独建立数据归档并更新引用。
2. IEEE benchmark systems and synthetic scenarios do not establish real-grid operational safety.
3. The current rule dictionary only covers 17 publicly linked rule summaries and should not be described as comprehensive national or provincial regulation coverage.
