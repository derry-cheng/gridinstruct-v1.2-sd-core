# Third-Party Asset Inventory

Status: `pass`

The v17 formal rebuild retains the hash-pinned PGLib-OPF `v23.07` checkout at commit `dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3`. Raw `.m` case files are excluded from the GridInstruct release archive. Derived states retain case identity, attribution, required source citations, and change notices.

Scope limit: Only the fixed PGLib-OPF v23.07 commit and eleven registered case hashes are confirmed. Any source revision or case substitution requires re-audit.

| System | PGLib case SHA-256 | Attribution | Required source citation | Change or use notice | Redistribution |
| --- | --- | --- | --- | --- | --- |
| `ieee14` | `bd5c568621de65e4b0922317010868bc7fa94173807faa10ea8fdbbe77c28106` | Copyright (c) 1999 Richard D. Christie, University of Washington Electrical Engineering | Retain the Richard D. Christie and University of Washington Power Systems Test Case Archive attribution from the case header. | Converted from IEEE Common Data Format on 20 September 2004; GridInstruct further generates scaled and contingency-derived numerical states. | `confirmed` |
| `ieee30` | `cae3290639d989731d32428aacf30c0b918bc91db73bc54791f3aa62d3f76c70` | Copyright (c) 1999 Richard D. Christie, University of Washington Electrical Engineering | Retain the Richard D. Christie and University of Washington Power Systems Test Case Archive attribution from the case header. | Converted from IEEE Common Data Format on 20 September 2004; GridInstruct further generates scaled and contingency-derived numerical states. | `confirmed` |
| `ieee57` | `aa3b48f7cbaade2afd69cb3790ef72be981db8494dcef9277bee460f754bdb22` | Copyright (c) 1999 Richard D. Christie, University of Washington Electrical Engineering | Retain the Richard D. Christie and University of Washington Power Systems Test Case Archive attribution from the case header. | Converted from IEEE Common Data Format; generator 1 Qmax and Qmin were manually changed to 200 and -140; GridInstruct further generates scaled and contingency-derived numerical states. | `confirmed` |
| `ieee118` | `b1af0833849040c04babc3700631cff0d9afa66b79c5d3e13ae79bdf516cec78` | Copyright (c) 1999 Richard D. Christie, University of Washington Electrical Engineering | Retain the Richard D. Christie and University of Washington Power Systems Test Case Archive attribution from the case header. | Converted from IEEE Common Data Format; base-kV values were manually added from the PSAP-format file on 10 March 2006; GridInstruct further generates scaled and contingency-derived numerical states. | `confirmed` |
| `ieee300` | `7ecf056d5942135765200ad7ae8791c28f0d35fb1dc888ba2c32dfc950f3c2f5` | Copyright (c) 1999 Richard D. Christie, University of Washington Electrical Engineering | Retain the Richard D. Christie and University of Washington Power Systems Test Case Archive attribution from the case header. | Converted from IEEE Common Data Format on 20 September 2004; GridInstruct uses the pandapower case300 operating point only while retaining PGLib topology and RATE_A values, then generates derived states. | `confirmed` |
| `illinois200` | `676e6f54a3b6726b199b531e7758ad8bf75ba2b076ea3fee86dc3f5cf5846f6b` | Copyright (c) 2017 A. B. Birchfield, T. Xu, K. M. Gegner, K. S. Shetye, and T. J. Overbye | Cite Birchfield et al., Grid Structural Characteristics as Validation Criteria for Synthetic Networks, IEEE Transactions on Power Systems (2017), DOI 10.1109/TPWRS.2016.2616385. | Converted from ACTIV_SG_200.pwb through PowerWorld Simulator and MATPOWER 6; GridInstruct further generates scaled and contingency-derived numerical states. Synthetic system with no CEII; it does not represent the actual grid. | `confirmed` |
| `pegase89` | `0c2ca484db566e587df8565141dbbf053c275e9246968391cc2f560fb4e995ca` | Copyright (c) 2015 Cedric Josz, Stephane Fliscounakis, Jean Maeght, and Patrick Panciatici | Cite Fliscounakis et al., IEEE Transactions on Power Systems 28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015. | Line-flow limits are 20 MVA above the original PEGASE current-flow limits; asymmetric branch shunts are represented nodally; line-flow constraints and the loss-minimizing objective are modified; GridInstruct further generates derived states. Validation-only fictitious data; do not use for operation or planning of the European grid. | `confirmed` |
| `pegase1354` | `cd6d27dff4a56684f1e4f82cfa346b36d84c4e90733228aa88331cd550e17652` | Copyright (c) 2015 Cedric Josz, Stephane Fliscounakis, Jean Maeght, and Patrick Panciatici | Cite Fliscounakis et al., IEEE Transactions on Power Systems 28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015. | Line-flow limits are 100 MVA below the original PEGASE current-flow limits; asymmetric branch shunts are represented nodally; line-flow constraints and the loss-minimizing objective are modified; GridInstruct further generates derived states. Validation-only fictitious data; do not use for operation or planning of the European grid. | `confirmed` |
| `rte1888` | `f4bc237298ed7c2e9291070f3c3a0983319565c12b801114a1e29641d0b5bff0` | Copyright (c) 2016 Cedric Josz, Stephane Fliscounakis, Jean Maeght, and Patrick Panciatici | Cite Josz et al., AC Power Flow Data in MATPOWER and QCQP Format: iTesla, RTE Snapshots, and PEGASE, arXiv:1603.01533. | PGLib curates a French-system snapshot sampled in the iTesla offline platform; GridInstruct further generates derived numerical states. Use only to validate mathematical methods and tools; do not use for operation or planning of the French or European grids. | `confirmed` |
| `rte2848` | `9d1ec26a6aef3e47bc23707a6c81db8d0528ff6ad90068b2f92df3d6e76ef591` | Copyright (c) 2016 Cedric Josz, Stephane Fliscounakis, Jean Maeght, and Patrick Panciatici | Cite Josz et al., AC Power Flow Data in MATPOWER and QCQP Format: iTesla, RTE Snapshots, and PEGASE, arXiv:1603.01533. | PGLib curates a French-system snapshot sampled in the iTesla offline platform; GridInstruct further generates derived numerical states. Use only to validate mathematical methods and tools; do not use for operation or planning of the French or European grids. | `confirmed` |
| `pegase2869` | `6c8e80fba6fc2fa78d65fce64cf4801425b01a0aa093661caf581b6551d4a7ac` | Copyright (c) 2015 Cedric Josz, Stephane Fliscounakis, Jean Maeght, and Patrick Panciatici | Cite Fliscounakis et al., IEEE Transactions on Power Systems 28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015. | Line-flow limits retain the original PEGASE current-flow limits; asymmetric branch shunts are represented nodally; line-flow constraints and the loss-minimizing objective are modified; GridInstruct further generates derived states. Validation-only fictitious data; do not use for operation or planning of the European grid. | `confirmed` |

## Upstream attribution contract

The upstream case data license is CC BY 4.0. Every derived record must:

- give appropriate credit to the original author.
- provide a link to the license.
- indicate whether changes were made.
- identify the repository version in scholarly references.
- cite source documents named in each case header and the PGLib archive report.
- Cite the PGLib archive report, “The Power Grid Library for Benchmarking AC Optimal Power Flow Algorithms,” arXiv:1908.02788.

## Release boundary

- PGLib `LICENSE` included: `true`.
- Raw PGLib case files included: `false`.
- Derived scenario and instruction records included: `true`.
- Final archive rebuild required after this inventory update: `true`.

See `docs/LICENSES_AND_CITATION.md` and `review-stage/SD_V16_PGLIB_LICENSE_ATTRIBUTION_AUDIT_20260724.md` for the audit decision and reusable citation text.
