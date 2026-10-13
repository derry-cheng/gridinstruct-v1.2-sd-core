# Official exact-content overlap repair

- Status: **pass**
- Reassigned groups: **42**
- Moved records: **42**

| Pair | Before | After |
| --- | ---: | ---: |
| train_validation | 11 | 0 |
| train_test | 26 | 0 |
| validation_test | 5 | 0 |

The repair retains every record and leaves the OOD projection unchanged. The resulting exact-content condition is stronger than record-ID separation and is used only to prevent lexical-baseline leakage.
