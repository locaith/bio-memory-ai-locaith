# Shadow comparison — cognitive_memory

```
==============================================================================
  SHADOW COMPARISON — cognitive_memory
==============================================================================
  observations         :    1,000
  legacy projections   :    1,000
  shadow projections   :    1,000
  comparisons          :    1,000
  matches              :    1,000  (100.00%)
  --------------------------------------------------------------------------
    MATCH                         1,000
  --------------------------------------------------------------------------
  legacy_observe_ms      p50=   0.298  p95=   0.566  p99=   8.075
  shadow_projection_ms   p50=   0.413  p95=   0.868  p99=   7.656
  --------------------------------------------------------------------------
  UNSUPPORTED (not tested, not passed)
    checkpoint_reference       no builder implemented; pins a memory version and is not replayable
    context_block              no builder implemented; depends on cognitive_memory
    prospective_memory         no builder implemented; scheduled for v0.8.3
    self_model_update          no builder implemented; scheduled for v0.8.3
==============================================================================
  verdict: CLEAN
==============================================================================
```

Raw results: `shadow_comparison.json`
