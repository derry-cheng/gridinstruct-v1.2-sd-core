# Generation Grounding Audit

Generated: 2026-07-31T10:05:17.108958+00:00
Dataset: `data/gridinstruct_v1.2_sd_core.jsonl`

## Verdict

- Overall status: `warn`
- Records audited: 95,479

## Gates

- rule_references_valid: `pass` — invalid=0
- scenario_links_valid: `fail` — missing=160
- network_links_consistent: `pass` — mismatch=0
- severity_metadata_consistent: `fail` — mismatch=22437
- tool_plan_scenarios_valid: `pass` — missing=0
- classification_output_labels_consistent: `pass` — mismatch=0
- compliance_policy_recomputable: `pass` — match_rate=1.000000, checked=24767
- structured_queries_executable: `pass` — match_rate=1.000000, checked=17766

## Task Counts

- auxiliary_decision: 18,087
- dispatcher_intent_tool_call: 19,773
- intelligent_data_query: 17,766
- operation_ticket_check: 10,881
- regulation_compliance_check: 24,767
- regulation_qa: 4,205

## Compliance Label Consistency

- Checked records: 24,767
- Matched records: 24,767
- Match rate: 1.000000

## Executable Query Consistency

- Checked records: 17,766
- Matched records: 17,766
- Match rate: 1.000000
