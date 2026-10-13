# Historical prediction archive

`pre_leakage_fix_predictions_20260908.tar.gz` contains the three old direct-English TF–IDF validation, test and OOD prediction JSONL files. The leakage-fixed reports and predictions supersede this evaluation. These three old files are not referenced by the current evidence-binding manifest, current revision manifest, manuscript metric bindings or figure-generation inputs. The old report values are unchanged.

Every archived member was compared byte-for-byte against its original before removing the unpacked file. The adjacent JSON lists all original names, sizes and space saved. Restore from the repository root with:

```sh
tar -xzf benchmark/archive/pre_leakage_fix_predictions_20260908.tar.gz
```

Current leakage-fixed predictions, strict-split predictions, currently registered historical baselines, and all data, review records and new experiments remain unpacked. This deliberately bounded cleanup does not meet a 120 MB savings target; most remaining large historical predictions still support registered evidence.
