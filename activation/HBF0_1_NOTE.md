# HBF-0.1 — inventory với FULL PROJECTION EQUIVALENCE (19/08)

Script inline (một lần, read-only); logic = HBF0_inventory.py + so sánh trọn
hợp đồng cho lớp LEGACY: content bằng + confidence/importance/salience/utility
khớp contract call-site theo hook type + metadata.state hiện diện.

```
CURRENT HISTORICAL EVENTS         326
ALREADY_MANAGED                    22
TOMBSTONED_EXCLUDED                 2
EVENT_ONLY                         35
LEGACY_PROJECTION_EQUIVALENT      240   ← đủ tư cách ADOPT với
                                         equivalence_proof = full_projection_contract_v1
LEGACY_CONTENT_EQUIVALENT_ONLY      0
CONTRACT_UNKNOWN                   27   ← nguồn không phải claude-code hook;
                                         HBF-1 phải gọi tên contract của writer
TRUE_MISSING / DIVERGENT / AMBIGUOUS  0 / 0 / 0
UNCLASSIFIED                        0
```

Artifact: `HBF0/hbf0_1_report.json` (snapshot sha, ids từng lớp cần quyết định).
