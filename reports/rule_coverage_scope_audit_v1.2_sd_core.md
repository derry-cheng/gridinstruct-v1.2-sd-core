# Rule Coverage and Scope Audit

- Generated: 2026-08-28T19:59:41.722580+00:00
- Status: `pass`
- Rule cards: 17
- Records with rule links: 95479
- Invalid rule links: 0

## Rule Cards

| Rule | Type | Source URL | Applicable tasks | Record links |
| --- | --- | --- | --- | ---: |
| REG_OVERLOAD_001 | threshold | True | regulation_compliance_check, auxiliary_decision, regulation_qa, intelligent_data_query | 50752 |
| REG_VOLTAGE_001 | threshold | True | regulation_compliance_check, auxiliary_decision, regulation_qa, intelligent_data_query | 49207 |
| REG_SWITCHING_001 | procedural | True | operation_ticket_check, regulation_qa | 11722 |
| REG_TOOL_001 | procedural | True | dispatcher_intent_tool_call, regulation_qa | 20721 |
| REG_QUERY_001 | data_query | True | intelligent_data_query, regulation_qa | 18712 |
| REG_OVERLOAD_ALERT_002 | threshold_detail | True | regulation_compliance_check, auxiliary_decision, intelligent_data_query | 35973 |
| REG_OVERLOAD_EMERGENCY_003 | threshold_detail | True | regulation_compliance_check, auxiliary_decision | 15150 |
| REG_VOLTAGE_LOW_002 | threshold_detail | True | regulation_compliance_check, auxiliary_decision | 14911 |
| REG_VOLTAGE_HIGH_003 | threshold_detail | True | regulation_compliance_check, auxiliary_decision | 1611 |
| REG_SECURITY_REDISPATCH_004 | closed_loop_validation | True | auxiliary_decision, dispatcher_intent_tool_call | 26481 |
| REG_SWITCHING_PERMISSION_002 | procedural_detail | True | operation_ticket_check | 10880 |
| REG_SWITCHING_MONITORING_003 | procedural_detail | True | operation_ticket_check | 10880 |
| REG_TOOL_DIAGNOSIS_002 | tool_routing_detail | True | dispatcher_intent_tool_call | 13601 |
| REG_TOOL_MITIGATION_003 | tool_routing_detail | True | dispatcher_intent_tool_call | 8714 |
| REG_TOOL_POSTCHECK_004 | tool_routing_detail | True | dispatcher_intent_tool_call, auxiliary_decision, regulation_compliance_check | 20337 |
| REG_QUERY_FILTER_002 | data_query_detail | True | intelligent_data_query | 17765 |
| REG_QUERY_AGGREGATION_003 | data_query_detail | True | intelligent_data_query | 13475 |

## Scope Statement

The release uses 17 traceable rule cards as modeling abstractions over public power-grid operating documents. This audit verifies link integrity and record-level coverage of those rule cards; it does not claim that GridInstruct is a complete regulatory corpus.
