# Independent Query Truth Validation

- Status: `pass`
- Query records: 17766
- Unique scenarios independently recomputed: 2452
- Scenarios retained outside complete replay: 1
- Query records retained outside complete replay: 2
- Simulation errors: 0
- Answer mismatches: 0

Scenarios explicitly marked with `query_truth_complete=false` are retained in the dataset but are not
promoted to independently recomputed numerical truth. All other query-linked scenarios must pass the
deterministic Newton/Iwamoto replay and complete-set comparison.
