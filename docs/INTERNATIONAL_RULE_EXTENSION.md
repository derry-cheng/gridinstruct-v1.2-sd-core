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

Each fold trains on 256 records from one jurisdiction and tests on 256 records from the other. The manifests enforce zero train--test record overlap and keep all four cards of the held-out jurisdiction together. They define an evaluation route; model scores and expert agreement are intentionally not reported until the corresponding runs and reviews are complete.

The current local gate checks rule-link integrity, per-card denominators, unique record identifiers, and direct-generation metadata. Two follow-up gates remain before making a cross-jurisdiction generalization claim: independent expert review of the rule interpretations and a held-out-jurisdiction evaluation. The extension is therefore reported separately from the core benchmark counts until those gates are complete.
