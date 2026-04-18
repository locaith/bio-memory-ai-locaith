# Real Evaluation Comparison: gemma4:e2b

- Runs: 2
- Avg total tokens: 15113.0
- Avg total latency (s): 85.519
- Avg retention rate: 0.834
- Avg task success rate: 0.5
- Runs with contradiction resolved: 0/2
- Contradiction-suite resolved runs: 2/2
- Heuristic contradiction-suite resolved runs: 2/2
- Approved-override-suite resolved runs: 2/2
- Detector benchmark heuristic correct totals: [4, 4]
- Detector benchmark hybrid correct totals: [8, 8]
- Detector benchmark heuristic precision: [1.0, 1.0]
- Detector benchmark hybrid precision: [1.0, 1.0]
- Detector benchmark heuristic false positives: [0, 0]
- Detector benchmark hybrid false positives: [0, 0]
- Hybrid NLI cache hits across runs: [8, 8]
- Hybrid NLI live calls across runs: [8, 8]
- Hybrid repeat-pass cache confirmations: [8, 8]

## Per-run trend

- run-1: tokens=15816, latency=92.924s, retention=1.0, task_success=0.667, contradiction_resolved=False, contradiction_suite_stable=2, contradiction_suite_reinforced=0, contradiction_suite_resolved=True, heuristic_contradiction_suite_resolved=True, detector_heuristic=4/8, detector_hybrid=8/8, heuristic_precision=1.0, hybrid_precision=1.0, heuristic_fp=0, hybrid_fp=0, hybrid_cache_hits=8, hybrid_live_calls=8, hybrid_repeat_cache=8, approved_override_reinforced=3, approved_override_edges=2, approved_by_policy_edges=2, expiring_override_edges=1, approved_override_resolved=True
- run-2: tokens=14410, latency=78.113s, retention=0.667, task_success=0.333, contradiction_resolved=False, contradiction_suite_stable=2, contradiction_suite_reinforced=0, contradiction_suite_resolved=True, heuristic_contradiction_suite_resolved=True, detector_heuristic=4/8, detector_hybrid=8/8, heuristic_precision=1.0, hybrid_precision=1.0, heuristic_fp=0, hybrid_fp=0, hybrid_cache_hits=8, hybrid_live_calls=8, hybrid_repeat_cache=8, approved_override_reinforced=3, approved_override_edges=2, approved_by_policy_edges=2, expiring_override_edges=1, approved_override_resolved=True