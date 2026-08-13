# Data Quality Optimization Report

Generated: 2026-07-31T09:50:19.209190+00:00
Dataset: `data/gridinstruct_v1.2_sd_core.jsonl`

## Verdict

- Status: `pass`
- Records: 95,479
- Required missing fields: 0
- Split ID overlap: 0
- Split prompt/input overlap: 0

## Task Counts

- auxiliary_decision: 18,087
- dispatcher_intent_tool_call: 19,773
- intelligent_data_query: 17,766
- operation_ticket_check: 10,881
- regulation_compliance_check: 24,767
- regulation_qa: 4,205

## Network Coverage

- IEEE118: 38,159
- IEEE14: 6,701
- IEEE30: 12,405
- IEEE300: 560
- IEEE57: 15,336
- ILLINOIS200: 2,912
- PEGASE1354: 1,056
- PEGASE2869: 624
- PEGASE89: 1,472
- RTE1888: 544
- RTE2848: 624
- none: 15,086

## Split Overlap

- ood_test__test: ids=0, scenarios=0, prompt_inputs=0
- ood_test__train: ids=0, scenarios=0, prompt_inputs=0
- ood_test__validation: ids=0, scenarios=0, prompt_inputs=0
- test__train: ids=0, scenarios=0, prompt_inputs=0
- test__validation: ids=0, scenarios=0, prompt_inputs=0
- train__validation: ids=0, scenarios=0, prompt_inputs=0

## Label Distribution

- operation_ticket_check: compliant=2771, compliant_with_monitoring=4949, non_compliant=3161
- regulation_compliance_check: compliant=1029, compliant_with_monitoring=10697, non_compliant=13041

## Gate Details

- all_tasks_present: PASS
- min_records_60000: PASS
- min_task_records_4000: PASS
- no_split_id_overlap: PASS
- no_split_prompt_input_overlap: PASS
- official_splits_present: PASS
- required_fields_complete: PASS
