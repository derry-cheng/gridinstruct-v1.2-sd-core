# Official exact-content overlap repair

- Status: **pass**
- Reassigned groups: **38**
- Moved records: **38**

| Pair | Before | After |
| --- | ---: | ---: |
| train_validation | 23 | 0 |
| train_test | 14 | 0 |
| validation_test | 1 | 0 |

The repair retains every record and leaves the OOD projection unchanged. The resulting exact-content condition is stronger than record-ID separation and is used only to prevent lexical-baseline leakage.
