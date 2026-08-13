# Transformer Classifier Baseline Report

- Task type: regulation_compliance_check
- Model: /root/.cache/huggingface/hub/models--distilbert-base-uncased/snapshots/12040accade4e8a0f71eabdb258fecc2e7e948be
- Device: cuda
- Objective: transformer_sequence_classification
- Train records: 5655
- Validation records: 707
- Best epoch: 8
- Validation accuracy: 0.9745
- Validation macro F1: 0.9808
- Validation balanced accuracy: 0.9797
- Rationale input enabled: False
- Input profile: full
- Test split: data/v1.2_sd_core_test_en.jsonl
- Test records: 701
- Test accuracy: 0.9201
- Test macro F1: 0.9204
- Test balanced accuracy: 0.9354

Local Transformer classification baseline with held-out evaluation.
