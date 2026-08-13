# Pretrained Seq2Seq Generation Baseline

- Model: /root/.cache/huggingface/hub/models--t5-small/snapshots/df1b051c49625cf57a3d0d8d3863ed4d13564fe4
- Device: cuda
- Task types: regulation_qa
- Target mode: full
- Train records: 3366
- Validation records: 420
- Best epoch: 2
- Validation exact match: 0.0024
- Validation selection F1: 0.8161
- Test split: data/v1.2_sd_core_test_en.jsonl
- Test records: 419
- Test exact match: 0.0072
- Test selection F1: 0.8096
- Test token_f1: 0.8096

Pretrained seq2seq generation baseline with held-out evaluation and saved checkpoints. In actionable mode, intelligent_data_query targets the executable structured_query; query_result remains validated as a dataset/simulation field rather than generated from hidden simulation records.

Metric: whitespace-token F1 for natural-language QA; character F1 is separately named for structured JSON and accompanied by canonical JSON, schema-validity, and typed required-field metrics
