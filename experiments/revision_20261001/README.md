# Local instruction-transfer experiment

The comparison uses Python 3.12.2, the pinned dependencies in `requirements.txt`,
and HuggingFaceTB/SmolLM2-135M-Instruct at revision
`12fd25f77366fa6b3b4b768ec3050bf629380bac`. The upstream model card declares
Apache-2.0: https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct.
Base weights are obtained separately and are not redistributed in this release.

`protocol.json` fixes the source partition, label-balanced sampling, seeds,
adaptation, checkpoint selection, and canonical-label likelihood scoring.
The five-step timing probes preceded the main comparison; their outputs were
not used to choose a model using test scores. The fixed comparison has 768
training, 192 validation, and 288 primary test records. Three seeds trained
for two epochs using local MPS and four CPU threads, taking 1,280 seconds.

Download the minimal model files into the ignored temporary directory:

```bash
HF_HUB_DISABLE_XET=1 python -c "from huggingface_hub import snapshot_download; snapshot_download('HuggingFaceTB/SmolLM2-135M-Instruct', revision='12fd25f77366fa6b3b4b768ec3050bf629380bac', local_dir='tmp/local_instruction_model', allow_patterns=['config.json','generation_config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','model.safetensors'], max_workers=2)"
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  python scripts/run_local_instruction_transfer.py
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  python scripts/evaluate_local_request_variants.py
python scripts/validate_local_instruction_transfer.py
```

The primary paired probe draws one pair per exact-request group from strict
test records. No exact-context request variants were available there. The
separate `request_variant_protocol.json` therefore defines two controlled
requests over each of 32 unchanged strict-test inputs, using unchanged
synthetic contract labels. These requests do not enter training or checkpoint
selection. Individual predictions, sample and pair identifiers, and small
adapters are under `benchmark/local_instruction_transfer_20261001/`.

The summary and independent prediction replay are under `reports/`.
The validator can use `--archive release/GridInstruct_v1.2_sd_core_data_only.tar.gz`
to recompute primary scores directly from the published archive without model
inference or whole-archive extraction. Label-balanced test results describe
the sampled contract; they do not estimate the prevalence-weighted score on
the full collection. Candidate scoring is a closed-label evaluation.

The saved-adapter check creates single-precision adaptation parameters before
loading their tensors. Each of the three saved adapters reproduces all 350
main-test and state-probe labels. This preserves the training comparison while
allowing prediction regeneration from the small saved weights.
