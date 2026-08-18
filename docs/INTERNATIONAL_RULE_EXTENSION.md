# International rule-probe extension

The GridInstruct core release remains a 95,479-record collection with its original 17 domestic rule cards. This extension adds an independent jurisdictional rule domain for source-conditioned English regulation question answering. It contains eight cards: four North American Electric Reliability Corporation (NERC) standards and four articles from Commission Regulation (EU) 2017/1485, together with 512 directly generated English records.

The NERC cards cover transmission-operation actions (TOP-001-6), operations planning (TOP-002-5), system-operating-limit methodology (FAC-011-4), and voltage/reactive control (VAR-001-5). The European cards cover system-state classification (Article 18), operational security limits (Article 25), contingency lists (Article 33), and operational security analysis (Article 72(3)). Each card stores its jurisdiction, authority, standard identifier, clause locator, official URL, source kind, evidence fields, and threshold-origin statement.

The extension is generated with:

```text
python3 scripts/generate_international_rule_probe.py
```

The generator writes the rule registry, source matrix, JSONL probe set, and validation report. Each of the eight cards receives 64 records. Records are English-language regulation-QA probes and carry `generation_mode=direct_english_from_typed_rule_contract`. They do not copy the domestic 100/110 percent or 0.95--1.05 p.u. policies into NERC or EU evidence.

The deterministic leave-one-jurisdiction-out manifests are generated with:

```text
python3 scripts/create_international_rule_probe_splits.py
```

Each fold trains on 256 records from one jurisdiction and tests on 256 records from the other. The manifests enforce zero train--test record overlap and keep all four cards of the held-out jurisdiction together.

The CPU retrieval diagnostic is run with:

```text
python scripts/run_international_rule_probe_baseline.py
```

It fits character-level TF--IDF on one jurisdiction, copies the answer from the nearest training record, and evaluates the other jurisdiction. The two folds give macro exact match 0.0000, macro token-F1 0.3532, and mean nearest-neighbour cosine similarity 0.7861. These values quantify lexical transfer under a deliberately weak retrieval baseline; they do not measure semantic legal correctness, physical compliance, or expert agreement.

A deterministic review package is generated with:

```text
python scripts/create_international_rule_review_assignments.py
```

It samples 16 records per card (128 records in total) and assigns each sampled record to two independent reviewer slots (256 assignments). The labels and adjudication fields are intentionally blank. The current local gate therefore establishes source, schema, split, and baseline reproducibility, while the independent human review remains an explicit external gate before making a cross-jurisdiction semantic-generalization claim. The extension remains reported separately from the core benchmark counts.
