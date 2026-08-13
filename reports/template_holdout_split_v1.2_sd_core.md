# Template-holdout Split Audit

Generated: 2026-07-31T10:25:56.768887+00:00

This stress split assigns task-specific prompt/template surfaces atomically to train, validation, or test. Test surfaces are unseen during training.

## Summary

- Status: pass
- Train records: 38,623
- Validation records: 5,682
- Test records: 11,116
- Train/test surface overlap: 0

## Task diagnostics

### dispatcher_intent_tool_call
- Records: 19,773
- Surface groups: 9,563
- Weighted surface label purity: 0.9999
- Split counts: {'train': 13832, 'validation': 1986, 'test': 3955}
- Surface overlap: {'train_validation': 0, 'train_test': 0, 'validation_test': 0}

### operation_ticket_check
- Records: 10,881
- Surface groups: 4,982
- Weighted surface label purity: 0.9996
- Split counts: {'train': 7585, 'validation': 1110, 'test': 2186}
- Surface overlap: {'train_validation': 0, 'train_test': 0, 'validation_test': 0}

### regulation_compliance_check
- Records: 24,767
- Surface groups: 1,416
- Weighted surface label purity: 0.9409
- Split counts: {'train': 17206, 'validation': 2586, 'test': 4975}
- Surface overlap: {'train_validation': 0, 'train_test': 0, 'validation_test': 0}
