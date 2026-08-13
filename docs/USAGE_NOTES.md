# Usage Notes

Generated: 2026-08-04T04:35:54.539790+00:00

## Validate And Split

`python scripts/validate_dataset.py --input data/gridinstruct_v1.2_sd_core_en.jsonl`
`python scripts/create_splits.py --input data/gridinstruct_v1.2_sd_core_en.jsonl`

## Official Splits

- `train`: `data/v1.2_sd_core_train_en.jsonl`
- `validation`: `data/v1.2_sd_core_validation_en.jsonl`
- `test`: `data/v1.2_sd_core_test_en.jsonl`
- `ood_test`: `data/v1.2_sd_core_ood_test_en.jsonl`

## Scope Note

`ood_test` in v1.2 is intended for topology/scenario transfer checks and does not include flat-task families such as `operation_ticket_check` and `regulation_qa`.

## Operational Caveat

GridInstruct is for research on data construction, benchmark design, and model evaluation. It is not a real-time dispatch tool and is not validated for safety-critical operational decisions.
