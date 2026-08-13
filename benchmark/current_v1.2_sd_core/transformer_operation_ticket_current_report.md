# Transformer Classifier Baseline Report

- Task type: operation_ticket_check
- Model: experiments/04_transformer_classification/operation_ticket_check/checkpoints/best_model
- Device: mps
- Objective: transformer_sequence_classification
- Train records: 8675
- Validation records: 1096
- Best epoch: 1
- Validation accuracy: 0.6861
- Validation macro F1: 0.7005
- Validation balanced accuracy: 0.7743
- Rationale input enabled: False
- Input profile: full
- Test split: data/v1.2_sd_core_test_en.jsonl
- Test records: 1110
- Test accuracy: 0.6739
- Test macro F1: 0.6919
- Test balanced accuracy: 0.7706
- Test non-compliant recall: 1.0000
- Test monitoring recall: 0.3118

Local Transformer classification baseline with held-out evaluation.
