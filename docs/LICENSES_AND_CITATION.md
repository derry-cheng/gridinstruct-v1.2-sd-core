# Licenses and Citation

This note records the licence, attribution, and citation contract for the GridInstruct code and data release.

## GridInstruct licenses

- Code: MIT, see `LICENSE-CODE`.
- GridInstruct data and metadata: CC BY 4.0, see `LICENSE-DATA`.
- Long source regulation text, installed dependency source code, pretrained weights, and raw upstream power-system case files are excluded from the release package.

## Fixed PGLib-OPF input contract

The formal numerical source is IEEE PES PGLib-OPF release `v23.07`, repository
`https://github.com/power-grid-lib/pglib-opf`, commit
`dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3`.

The pinned upstream `LICENSE` has SHA-256
`95b1cd9fee1676221d74f7c0cbba622d98ac098e9b317b3848113ef4356ab4fd`.
It identifies case data as CC BY 4.0 and software as MIT. The upstream README
permits sharing and adaptation of cases when users credit the original author,
link to the license, and indicate changes. It also asks scholarly users to
identify the repository version and cite both the source documents named in the
case headers and the PGLib archive report.

This verification applies only to the eleven files and hashes recorded in
`metadata/pglib_opf_v23_07_case_registry.json`. A different commit, release, or
case hash requires a new license and attribution audit.

## Attribution and source citations

All derived GridInstruct numerical records must retain the PGLib repository,
release, commit, source filename, source SHA-256, CC BY 4.0 link, applicable
original attribution below, and the statement that GridInstruct generated
scaled, contingency, topology-stress, OPF, query, or instruction records from
the fixed input.

| Fixed cases | Original attribution retained from file headers | Source citation or notice |
| --- | --- | --- |
| `ieee14`, `ieee30`, `ieee57`, `ieee118`, `ieee300` | Copyright (c) 1999 Richard D. Christie, University of Washington Electrical Engineering; source: University of Washington Power Systems Test Case Archive | Retain the IEEE Common Data Format conversion provenance. `ieee57` records the manual generator-1 reactive-limit change; `ieee118` records manually added base-kV values. |
| `illinois200` | Copyright (c) 2017 A. B. Birchfield, T. Xu, K. M. Gegner, K. S. Shetye, and T. J. Overbye | Cite Birchfield et al., “Grid Structural Characteristics as Validation Criteria for Synthetic Networks,” *IEEE Transactions on Power Systems* (2017), DOI `10.1109/TPWRS.2016.2616385`. The file states that this is a synthetic model with no CEII and does not represent the actual grid. |
| `pegase89`, `pegase1354`, `pegase2869` | Copyright (c) 2015 Cédric Josz, Stéphane Fliscounakis, Jean Maeght, and Patrick Panciatici; source: PEGASE project | Cite Fliscounakis et al., *IEEE Transactions on Power Systems* 28(4), 4909–4917 (2013), DOI `10.1109/TPWRS.2013.2251015`. Retain each header’s line-limit, shunt-model, line-constraint, and objective change notices. These fictitious cases are for validation and are not operational European-grid data. |
| `rte1888`, `rte2848` | Copyright (c) 2016 Cédric Josz, Stéphane Fliscounakis, Jean Maeght, and Patrick Panciatici; source: RTE/iTesla | Cite Josz et al., “AC Power Flow Data in MATPOWER and QCQP Format: iTesla, RTE Snapshots, and PEGASE,” arXiv:`1603.01533`. Retain the warning that these cases are for validating methods and tools and must not be used for French or European grid operation or planning. |

For all eleven cases, also cite the PGLib archive report, “The Power Grid
Library for Benchmarking AC Optimal Power Flow Algorithms,” arXiv:`1908.02788`,
and state PGLib-OPF release `v23.07`.

## Release-package treatment

- Raw PGLib `.m` case files are excluded.
- Derived numerical states and instruction records are included as attributed adaptations.
- Installed pandapower, lightsim2grid, and power-grid-model source code is excluded.
- The compact archive includes `third_party/pglib-opf-v23.07/LICENSE` and
  `third_party/pglib-opf-v23.07/UPSTREAM_COMMIT`; raw case files remain outside
  that archive. The repository retains the attributed pinned case files.
- The current compact archive includes the pinned PGLib `LICENSE`; its member
  manifest and SHA-256 sidecar are regenerated and replay-validated with each
  package rebuild. Any change to the upstream revision or the derived-state
  scope requires a new archive and a new license audit.

The technical audit confirms the fixed-source license text, file identities,
attribution, citations, and release handling. It is not an independent legal
opinion.

## External submission fields still required

- Final author identities and author-contribution declarations.
- Final citation of the fixed Git tag, code revision, and data-only release attachment.
- Final funding and competing-interest metadata.
- Confirmation of upstream benchmark-case attribution and redistribution terms
  if the pinned upstream revision changes.

The repository URL is https://github.com/derry-cheng/gridinstruct-v1.2-sd-core.
The current release route uses a fixed Git tag and its associated data-only
archive. Historical Zenodo identifiers do not identify this version.
