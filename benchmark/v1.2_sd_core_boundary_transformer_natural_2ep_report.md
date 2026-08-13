# Transformer Classifier Baseline Report

- Task type: operation_ticket_check
- Model: distilbert-base-uncased
- Device: mps
- Objective: transformer_sequence_classification
- Train records: 8675
- Validation records: 1096
- Best epoch: 1
- Validation accuracy: 1.0000
- Validation macro F1: 1.0000
- Validation balanced accuracy: 1.0000
- Rationale input enabled: False
- Input profile: natural
- Test split: data/v1.2_sd_core_boundary_challenge_test_en.jsonl
- Test records: 3000
- Test accuracy: 0.9990
- Test macro F1: 0.9990
- Test balanced accuracy: 0.9990
- Test non-compliant recall: 0.9970
- Test monitoring recall: 1.0000

Local Transformer classification baseline with held-out evaluation.
