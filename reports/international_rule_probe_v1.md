# International rule-probe extension

Status: `pass`

This extension is separate from the 95,479-record domestic core. It contains directly generated English regulation-QA records linked to four NERC standards and four EU System Operation Guideline articles.

| Jurisdiction | Rule cards | Records |
| --- | ---: | ---: |
| European Union | 4 | 256 |
| NERC North America | 4 | 256 |

The JSONL data SHA-256 is `ffd3ce089f704d1f55341a66ce355cfc5c0a739e8c90024e40d1590c283cc85d`. No domestic numeric threshold is copied into the international cards; each card records whether limits are operator-defined or regulation-specific.
The extension schema is `metadata/international_rule_probe_schema.json` (SHA-256 `44bc99e037697493178795991d9e23d20c9a8d0fdf36bc5c8e3b6a8078ea1658`); schema validation status is `pass` for all 512 records.

The extension still requires independent expert review and a jurisdiction-held-out evaluation before any cross-jurisdiction generalization claim is made.
