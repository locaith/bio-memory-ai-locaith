# CORE INTEGRITY — FINAL BASELINE (single host / common clock)

**Đóng ngày 19/08/2026.** Lane này không đổi một dòng runtime nào:
`RUNTIME SEMANTICS DELTA = 0`. Nó đóng hồ sơ, đóng baseline, và đặt ranh giới
public/private trước khi nghiên cứu bước sang tầng Lifespan.

---

## 1. Phạm vi — chữ CLOSED này nói gì và KHÔNG nói gì

```
CLOSED     một memory kernel chạy trên MỘT host với MỘT đồng hồ chung
           thì bảo toàn được sự thật và lịch sử qua: crash · replay ·
           mất outbox · version rebuild · forgetting · historical migration

KHÔNG NÓI  multi-node · clock skew · nhiều host cùng ghi
           (NOT CERTIFIED — chưa có clock-skew contract, chưa đo)

KHÔNG NÓI  hệ thống "sống tốt hơn theo thời gian"
           (đó là câu hỏi của Lifespan Substrate, chưa mở)
```

Bất biến khép kín được phát biểu đúng một lần:

```
WRITE DECISION
     │
     ▼
IMMUTABLE EVIDENCE
     │
     ▼
DURABLE PROJECTION STATE
     │
     ├── crash        ├── replay          ├── lost outbox
     ├── version rebuild                  ├── forgetting
     └── historical migration
            │
            ▼
      SAME SEMANTIC INTENT
```

Recovery nghĩa là **khôi phục điều hệ thống đã biết**, không phải **đoán lại
điều hệ thống có lẽ từng muốn làm**. Đó là đường biên giữa Bio-Memory và một
replayable event log thông thường.

---

## 2. Hiến pháp — mỗi luật phải trỏ được vào ba thứ

> **NO PROSE-ONLY CONSTITUTION.**

Danh sách luật đầy đủ nằm ở [`core_integrity_manifest.json`](core_integrity_manifest.json),
mỗi luật kèm: sự cố gốc · test/mutant thực thi nó · vị trí thực thi trong
runtime. Bảng dưới đây **được sinh ra từ chính manifest** nên nó không thể
trôi khỏi dữ liệu.

Kiểm bằng máy, không bằng lời:

```bash
python tools/verify_core_integrity_manifest.py --run-tests
```

Bộ kiểm đọc AST của từng file được trích dẫn: một node id pytest không tồn
tại, một symbol runtime không có thật, hay một sự cố không khớp commit nào —
đều làm nó trả mã 1. Một hiến pháp trỏ vào chỗ trống còn tệ hơn không có
hiến pháp: nó tạo cảm giác được bảo vệ.

<!-- LAWS:BEGIN — sinh bởi tools/render_core_integrity_laws.py, đừng sửa tay -->
**91 điều luật** · 55 điều có mutant canh · 234 test được trích dẫn · 45 vùng.


### SQLite generation replacement, rollback safety, store corruption and recovery

#### GENERATION REPLACEMENT BEATS IN-PLACE RESTORATION. Canonical is never overwritten in place; it is MOVE-ASIDE'd into a quarantined generation bundle and the candidate is installed into the resulting EMPTY slot. · **mutant**

The old rollback protocol (copy2 over the live DB file) produced 92 integrity findings on the owner's real store, because a still-live handle carried the page cache of the old file and every subsequent write or checkpoint — including the implicit one inside close() — re-applied old pages onto the new lineage.

- **sự cố gốc** — rca(lane1): root cause VERIFIED duoi co che TINH CHINH — page cache, khong phai WAL frames
- **test** — `tests/test_store_generation.py::test_mutant_the_old_bug_still_corrupts_and_the_primitive_does_not`
- **test** — `tests/test_store_generation.py::test_no_copy_over_canonical_rollback_code_returns`
- **test** — `tests/test_store_generation.py::test_happy_path_installs_and_quarantines`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:install_generation`

#### MOVE-ASIDE failure caused by a live handle IS the fail-closed fence, not an inconvenience: "trên Windows, một file đang bị handle sống giữ sẽ làm move thất bại — và đó CHÍNH LÀ hàng rào fail-closed". Branch A = nothing installed, old store intact; branch B = a surviving handle can only dirty the corpse in quarantine, it has no key into the new generation.

The primitive refuses to compete in the race it cannot win ("have all handles been closed yet?"); the 18/08 corruption happened precisely because that race was assumed won.

- **sự cố gốc** — recovery: store that phuc hoi VERIFIED — candidate ngoai, chung minh, thay MOT lan
- **test** — `tests/test_store_generation.py::test_live_handle_fails_closed_and_canonical_survives`
- **test** — `tests/test_store_generation.py::test_process_level_stale_handle_cannot_reach_new_generation`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:install_generation`

#### FAIL CLOSED phải đúng TRỌN đường, không chỉ nửa move-aside: a failure in copy2/replace/verify AFTER move-aside must delete the half-installed file and move the quarantined bundle back, and the caller must decide canonical state BY OBSERVATION (REAL.exists() + sha in the known set), never by its own bookkeeping.

Before HBF-3 only the move-aside loop had a give-back branch, so a failure past it left canonical moved away with nothing in its place while the caller read "not installed yet" and re-enabled the write hook into a store that did not exist — three of four adversarial review lenses independently landed on it and graded it catastrophic.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md — Review đối kháng 4 lăng kính, finding #1 (`install_generation` chưa fail-closed trọn đường)
- **test** — `tests/test_store_generation.py::test_install_failure_after_move_aside_restores_canonical`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:install_generation`
- **runtime** — `activation/HBF3_naturalization.py:main`

#### Candidate phải tự chứng minh trước khi được phép lại gần canonical: PRAGMA integrity_check must return exactly ['ok'] and the content hash is taken BEFORE anything touches canonical; a candidate that fails is refused with canonical byte-identical to what it was.

The real-store recovery only counted because the candidate was built OUTSIDE from a clean post-A4 snapshot and certified (integrity ok, fk empty, COUNT==scan==distinct 268/268/268) before a single byte of the canonical path was moved.

- **sự cố gốc** — recovery: store that phuc hoi VERIFIED — candidate ngoai, chung minh, thay MOT lan
- **test** — `tests/test_store_generation.py::test_corrupt_candidate_is_refused_before_touching_canonical`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:certify_candidate`

#### Chỉ -wal CÓ NỘI DUNG mới là bằng chứng candidate chưa checkpoint xong. An EMPTY -wal (and -shm) created by any read-only reader — including certify_candidate itself — is standard SQLite behaviour, not a defect of the candidate. (An instance of CORRECTION MUST BE POPULATION-SCOPED.)

Probe 18/08: the second certify tripped over exactly the empty -wal that the first certify had created, so a rule written as `wal.exists()` would fail-closed on its own footprint and make retry impossible; the rule was rescoped to `st_size > 0`.

- **sự cố gốc** — feat(lane2): safe rollback primitive — GENERATION REPLACEMENT BEATS IN-PLACE RESTORATION
- **test** — `tests/test_store_generation.py::test_candidate_with_sidecar_is_refused`
- **test** — `tests/test_store_generation.py::test_process_level_stale_handle_cannot_reach_new_generation`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:certify_candidate`

#### Sidecars are part of the artefact: "Hash file chính KHÔNG đủ: ở chế độ WAL, một prompt mới commit vào memory.db-wal, còn memory.db có thể không đổi một byte." A clone/move/guard that ignores -wal/-shm compares against something that is not the database.

A CAS guard hashing only the main file would have reported "nobody wrote" while a live prompt sat in memory.db-wal, then installed the candidate and pushed that write into quarantine — the write would have vanished from the future of the live store.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md — "CAS guard chỉ hash file chính là không đủ" (self-caught before the adversarial review returned)
- **test** — `tests/test_frozen_sources.py::test_a_clone_carries_the_sidecar_files`
- **runtime** — `bio_agent_os/evals/frozen.py:clone`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:SIDECARS`
- **runtime** — `activation/HBF3_naturalization.py:source_generation`

#### HAVING A ROLLBACK POINT != SAFE LIVE MUTATION — "Having a rollback point does not make live in-place mutation a safe migration protocol." The only sanctioned road back to canonical is candidate-offline → certify → install_generation; a certified snapshot does not license opening a write connection to the live store.

SP-2 repaired 9 real memories correctly but did it by opening a write connection to the real store and UPDATE-ing in place while holding a certified rollback point; product right, procedure FAIL, recorded as law rather than rolled back. The same shape had already destroyed a store once on 18/08.

- **sự cố gốc** — fix(SP-1/SP-2): persist the write contract — mot constructor, hai writer, va repair 9 ky uc that
- **test** — `tests/test_store_generation.py::test_no_copy_over_canonical_rollback_code_returns`
- **test** — `tests/test_frozen_sources.py::test_the_gate_writes_to_a_clone_and_not_to_the_source`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:install_generation`
- **runtime** — `bio_agent_os/evals/frozen.py:verify`
- **runtime** — `scripts/slot_gate.py:gate`

#### STALE BASELINE != SAFE REPLACEMENT for a whole store: "Candidate có thể hoàn hảo, nhưng nếu một prompt mới lọt vào canonical sau lúc snapshot thì install candidate sẽ XOÁ prompt đó khỏi tương lai." Writers stay quiesced from before the snapshot until after the install, and the source generation must be re-proved unchanged (main + WAL + logical counts) at the instant of replace.

A generation replacement is a total substitution; anything committed to canonical between snapshot and install is silently moved into quarantine and lost from the live store's future.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md — HBF-3 design note + CAS guard section (main+wal+logic không lệch)
- **runtime** — `activation/HBF3_naturalization.py:quiesce`
- **runtime** — `activation/HBF3_naturalization.py:source_generation`

#### Một 'điểm rollback' đã mang bảng adoption thì KHÔNG phải điểm rollback — a rollback snapshot that already contains the migration's own tables is refused at both ends (before snapshotting and before restoring).

Re-running --go after a successful install would re-snapshot the already-migrated canonical over SNAP, and the later --rollback would then 'restore' the system into exactly the state it was trying to leave.

- **sự cố gốc** — activation/HBF3_naturalization.py — docstring of assert_pre_migration (documented reproduction of the re-run path; guard added before the real store was touched)
- **runtime** — `activation/HBF3_naturalization.py:assert_pre_migration`
- **runtime** — `activation/HBF3_naturalization.py:rollback`

#### A disagreement between COUNT, table-scan and DISTINCT over the same file is a corruption signal, not a population delta; recovery is not certified until COUNT == scan == distinct and integrity_check == ok and foreign_key_check is empty. "Hash sau copy chỉ chứng minh lệnh copy — không chứng minh SQLite sẽ thấy một database hợp lệ."

During the 18/08 incident the store reported 267 vs 223 rows for the same table — an index-driven query versus a table scan over one corrupt file. Reading that as a real 44-row loss would have sent the recovery after a data-loss ghost instead of after index corruption.

- **sự cố gốc** — recovery: store that phuc hoi VERIFIED — candidate ngoai, chung minh, thay MOT lan
- **runtime** — `activation/RECOVERY/R_recover.py:main`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:certify_candidate`


### deployed config authority

#### DECLARED MODE MUST BE CORROBORATED BY EFFECTIVE RUNTIME MODE — a declared/effective mismatch is INVALID, not PASS

A leaked session env (BIO_AGENT_PROJECTION_MODE=outbox left over from an A5.4 run already reverted in settings) made the suite run under a default it did not declare, turning 5 cases red for a reason that had nothing to do with the code; every single-writer case now states its mode and reads the runtime's mode back before asserting anything.

- **sự cố gốc** — commit 06f7049 'feat(lane2): safe rollback primitive' — section 'PHAT HIEN PHU TRONG LUC VALIDATE: session env con mang BIO_AGENT_PROJECTION_MODE=outbox tu lan A5.4 da revert trong settings — 5 ca do'; also tests/test_canary_supervisor_args.py docstring (an import-time os.environ.setdefault silently switched observe() onto the outbox and broke two unrelated tests)
- **test** — `tests/test_hook_single_writer.py::test_outbox_substantive_worker_is_the_only_writer`
- **test** — `tests/test_hook_single_writer.py::test_legacy_substantive_one_memory_no_production_debt`
- **test** — `tests/test_hook_single_writer.py::test_outbox_non_substantive_stays_event_only`
- **test** — `tests/test_hook_single_writer.py::test_shadow_semantics_unchanged`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.__init__`
- **runtime** — `bio_agent_os/cognitive/shadow.py:current_mode`

#### A MISCONFIGURED MODE MUST NOT QUIETLY CHANGE HOW MEMORIES ARE WRITTEN — an unrecognised value fails closed to legacy, and doing nothing gets you the behaviour that shipped

Mode is read from a single environment variable that a typo, a stale shell, or an imported module can set; without a fail-closed resolver a misspelling would silently move every write onto an unvalidated projection path.

- **sự cố gốc** — commit 06f7049 'feat(lane2): safe rollback primitive' (leaked BIO_AGENT_PROJECTION_MODE=outbox produced 5 false-red cases and prompted the conftest env pin); rule stated in bio_agent_os/cognitive/shadow.py:current_mode docstring
- **test** — `tests/test_shadow_mode.py::test_an_unknown_mode_falls_back_to_legacy`
- **test** — `tests/test_projection_control.py::test_an_unrecognised_mode_falls_back_to_legacy`
- **test** — `tests/test_projection_control.py::test_legacy_is_the_default_with_no_environment_variable`
- **test** — `tests/test_projection_control.py::test_observe_enqueues_nothing_by_default`
- **test** — `tests/test_shadow_mode.py::test_default_mode_is_legacy`
- **runtime** — `bio_agent_os/cognitive/shadow.py:current_mode`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS._projection_types`

#### HARNESS-FORCED ENV != DEPLOYED CONFIG AUTHORITY — proving the hook path while the process is forced into OUTBOX does not prove that persistent settings are the party choosing OUTBOX; no mode string may be hard-coded in the witness · **mutant**

A5-v2's ladder injected BIO_AGENT_PROJECTION_MODE=outbox inside the harness's own hook(), so its '3+1 canary' proved the execution path but not the configuration authority — the same false-green family as every earlier one: the execution path did not measure what the report claimed.

- **sự cố gốc** — commit 62bcac1 'witness(A5-v2.1): deployed config la ben THAT SU chon OUTBOX — day noi cuoi da gan'; artifact activation/A5v2_1/a5v2_1_report.json ('DEPLOYED CONFIG WITNESS: PASS', declared_mode_from_settings read out of .claude/settings.json, effective mode witnessed as 1 completed job + 1 ledger row in a fresh process); re-run after SP-1B in commit ba9c9b0 'requalify: ladder + deployed window PASS sau SP-1B'
- **test** — `tests/test_shadow_mode.py::test_mode_comes_from_the_environment`
- **test** — `tests/test_shadow_mode.py::test_default_mode_is_legacy`
- **test** — `tests/test_shadow_mode.py::test_an_unknown_mode_falls_back_to_legacy`
- **test** — `tests/test_deployed_config_authority.py::test_the_settings_file_decides_the_write_path`
- **test** — `tests/test_deployed_config_authority.py::test_session_env_must_not_override_the_settings_file`
- **test** — `tests/test_deployed_config_authority.py::test_mutant_hard_coded_mode_breaks_settings_authority`
- **runtime** — `bio_agent_os/cognitive/hook_cli.py:main`
- **runtime** — `bio_agent_os/cognitive/shadow.py:current_mode`


### durable-forgetting / NOT_EVALUABLE vs NOT_APPLICABLE in deletion scope

#### 'I cannot tell whether this is about the topic' must not be rendered as 'this is not about the topic'. A candidate that cannot be judged is kept (inf never sets the bar for the others) or reported as ABSTAINED — never deleted on a guess and never silently left behind.

A memory with no vector yet could not be scored and was dropped from scope, so deletions arriving before the async embedding backfill under-deleted silently — and only for the newest memories, which are the ones a person is most likely to want gone.

- **sự cố gốc** — commit c805662 'fix(privacy): phep xoa bo qua nhung ky uc no CHUA THE danh gia'; measured store state at tick 440 recorded in tests/test_forget_primary_survives.py:5-9 ('kho lúc xoá: 434 ký ức, 250 có vector (184 CHƯA có)')
- **test** — `tests/test_forget_primary_survives.py::test_a_candidate_without_a_vector_is_not_treated_as_off_topic`
- **test** — `tests/test_forget_scope_precision.py::test_a_memory_with_no_readable_predicate_is_abstained_not_deleted`
- **test** — `tests/test_forget_scope_precision.py::test_abstentions_are_reported_in_the_result`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:_topic_members`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:abstentions`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:_by_predicate`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:preview`


### durable-forgetting / deletion perimeter completeness

#### The privacy perimeter is a closed world, not a list: every table in the database minus every table declared in the registry must equal the empty set, and a declared store holding content must either be scanned by the deletion verifier or state why it is kept. · **mutant**

rejected_inputs and agent_checkpoints were declared, described, and still outside CONTENT_COLUMNS — so forget_derived and erase_history both reported an unqualified success while leaving a live API key on disk in the same database file.

- **sự cố gốc** — commit f8e0b63 'feat(privacy): F6 dong — hai kho cuoi cung ra khoi vung mu cua bo kiem chung xoa'; measurement on a real MemoryOS 17/08/2026 recorded in tests/test_quarantine_erasure.py:10-21 (forget_derived verified_clean=True, erase_history verified_clean=True with 2 events redacted, rejected_inputs.content still 'An Phat api_key: sk-live-…')
- **test** — `tests/test_privacy_registry.py::test_every_persistent_store_is_declared`
- **test** — `tests/test_privacy_registry.py::test_an_undeclared_store_fails_the_suite`
- **test** — `tests/test_privacy_registry.py::test_no_declared_store_holds_content_outside_the_verifier`
- **test** — `tests/test_privacy_registry.py::test_the_registry_matches_what_the_deletion_verifier_scans`
- **test** — `tests/test_privacy_registry.py::test_a_store_holding_content_and_keeping_it_must_say_why`
- **test** — `tests/test_quarantine_erasure.py::test_both_stores_are_scanned_by_the_deletion_verifier`
- **test** — `tests/test_quarantine_erasure.py::test_erase_history_reaches_an_input_that_never_became_a_memory`
- **runtime** — `bio_agent_os/cognitive/privacy_registry.py:undeclared`
- **runtime** — `bio_agent_os/cognitive/privacy_registry.py:REGISTRY`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:CONTENT_COLUMNS`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:REDACTED_STORES`


### durable-forgetting / deletion provenance

#### A deletion nobody is named for is a deletion nobody can be asked about: place() and lift() refuse without an actor (lift also without a reason), and the actor recorded must be WHO ASKED, not the function that ran. · **mutant**

The first real lifetime run placed 68 tombstones and every single one was signed 'forget' — forget_scoped was calling forget_derived without passing the actor it had been given, so a table recording who deleted something answered nothing.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; incident recorded at tests/test_replay_resurrection.py:175-181 ('The lifetime run placed 68 tombstones and every one of them was signed "forget"') and bio_agent_os/cognitive/forget_scope.py:553-555
- **test** — `tests/test_replay_resurrection.py::test_a_tombstone_needs_a_name_on_it`
- **test** — `tests/test_replay_resurrection.py::test_the_tombstone_names_who_asked_not_the_function_that_ran`
- **test** — `tests/test_forget_scope.py::test_a_deletion_needs_an_actor`
- **test** — `tests/test_memory_constitution.py::test_recording_a_decision_needs_an_actor`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:place`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:lift`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:forget_scoped`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_bury`


### durable-forgetting / deletion scope precision

#### Deletion scope is decided by predicate agreement, not by a similarity threshold. Where the memory names no predicate the answer is ABSTAIN.

Forty-three per cent of what a deletion removed should have stayed — a request to forget where somebody lives was taking their phone number, their job and their project with it, and unlike a leak that cannot be undone.

- **sự cố gốc** — commit 42fbc2a 'fix(privacy): pham vi xoa quyet dinh boi VI TU, khong boi mot con so'; measurement over the 18 deletion requests of the lifetime world recorded in tests/test_forget_scope_precision.py:3-12 (scope_recall 51/52=0.981, scope_precision 51/90=0.567, collateral_deletion_rate 39/90=0.433) with the population overlap sweep proving no single cut works
- **test** — `tests/test_forget_scope_precision.py::test_a_city_request_takes_only_the_city_memories`
- **test** — `tests/test_forget_scope_precision.py::test_another_persons_record_is_never_in_scope`
- **test** — `tests/test_forget_scope_precision.py::test_the_rule_does_not_depend_on_an_embedder`
- **test** — `tests/test_forget_scope_precision.py::test_a_whole_subject_request_still_takes_everything`
- **test** — `tests/test_forget_scope_precision.py::test_the_match_says_which_predicate_it_agreed_on`
- **test** — `tests/test_forget_scope.py::test_a_topic_scope_takes_only_that_aspect`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:_by_predicate`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:_predicate_of`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:preview`


### durable-forgetting / escalation from reversible to irreversible deletion

#### A second, stronger deletion request after a forget must not silently fail. Once the memory rows are gone the tombstone is the only surviving link from a deleted memory back to its events, so erase_history resolves through tombstones and event content — and a subject-scoped hard erase after a deletion says it cannot resolve rather than quietly erasing everything buried.

'Forget it' followed by 'actually, erase it properly' is an ordinary sequence, and until 16/08 the second call resolved through memory rows the first call had removed, found no targets, and returned a clean-looking report having redacted nothing — the user's stronger request was the one that silently failed.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; incident written into the shipping comment at bio_agent_os/cognitive/forgetting.py:936-945 and the helper contract at :518-541
- **test** — `tests/test_replay_resurrection.py::test_erase_history_is_still_a_separate_operation`
- **test** — `tests/test_quarantine_erasure.py::test_erase_history_still_says_when_it_found_nothing`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_tombstoned_events`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:erase_history`


### durable-forgetting / freeze and causal isolation of the forgetting substrate

#### Tombstone/materialization safety must be re-measured under BOTH arms of any semantic change, isolated in one process — and a patch may not touch forgetting/tombstone/projection_engine/outbox/erase/replay/materialization while that door is open. · **mutant**

Comparing HEAD against a commit before the change would mix three unrelated patches and attribute the result to none of them (STALE BASELINE != CAUSAL DELTA at small scale); and if the patch had touched the tombstone path, 'resurrection not yet measured' would change meaning from an infrastructure gap into an unverified risk.

- **sự cố gốc** — commit 69c55ac 'test(#7): Door 5 do lai bang duong materialization that — NOT_EVALUABLE -> PASS'; decision record docs/decisions/2026-08-17-accept-seven.md:91,135-138; isolation argument at tests/test_seven_door_five.py:14-17
- **test** — `tests/test_seven_acceptance.py::test_the_patch_does_not_touch_forget_or_replay_infrastructure`
- **test** — `tests/test_seven_door_five.py::test_the_control_materialises_in_both_arms[pre-#7]`
- **test** — `tests/test_seven_door_five.py::test_the_control_materialises_in_both_arms[#7]`
- **test** — `tests/test_seven_door_five.py::test_the_clean_implementation_blocks_in_both_arms[pre-#7]`
- **test** — `tests/test_seven_door_five.py::test_the_clean_implementation_blocks_in_both_arms[#7]`
- **test** — `tests/test_seven_door_five.py::test_the_mutant_resurrects_in_both_arms[pre-#7]`
- **test** — `tests/test_seven_door_five.py::test_the_mutant_resurrects_in_both_arms[#7]`
- **test** — `tests/test_seven_door_five.py::test_seven_changes_proposition_grouping_and_nothing_about_burial[pre-#7]`


### durable-forgetting / honest deletion reporting

#### NO MATCH IS NOT SUCCESS, and ZERO CHECKS IS NOT CLEAN. verified_clean answers 'is the content gone'; succeeded answers 'did this request do anything' — and verified_clean is False whenever checks_run <= 0. Enforced on the real deletion path by the constitution gate (RULE_002, MEMORY != EVIDENCE), not only in a test file. · **mutant**

Fifteen of eighteen deletion requests in the first lifetime run matched nothing and reported a clean bill of health; separately, _probes() produced nothing at all for four of six ordinary Vietnamese sentences, so the verification loop never ran and the flag was set True regardless.

- **sự cố gốc** — commits 1ff9ad3 'feat: xoa theo pham vi -- va lan chay dau tien cua cognitive/ tren 1.000 su kien' and 3586a65 'fix: bon co che bao thanh cong ma khong kiem gi, va bom duoc thoi gian vao duong ghi'; measurement 15/08/2026 recorded in bio_agent_os/cognitive/forget_scope.py:3-8 ('yêu cầu xoá đã THỰC SỰ xoá được gì: 3/18') and 14/08 measurement in bio_agent_os/cognitive/forgetting.py:212-224
- **test** — `tests/test_forget_scope.py::test_matching_nothing_reports_no_match_not_a_clean_bill`
- **test** — `tests/test_forget_scope.py::test_verified_clean_and_succeeded_are_different_questions`
- **test** — `tests/test_forget_scope.py::test_a_scope_that_matches_nothing_says_so`
- **test** — `tests/test_memory_constitution.py::test_a_mutant_that_claims_clean_without_checking_fails_rule_002`
- **test** — `tests/test_memory_constitution.py::test_a_real_deletion_report_satisfies_rule_002`
- **test** — `tests/test_memory_constitution.py::test_the_real_deletion_path_goes_through_the_gate`
- **test** — `tests/test_memory_constitution.py::test_checking_nothing_does_not_produce_a_clean_sheet`
- **test** — `tests/test_provable_forgetting.py::test_forget_is_verified_not_assumed`
- **test** — `tests/test_provable_forgetting.py::test_forget_refuses_to_claim_success_when_residue_remains`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:ForgetResult.succeeded`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:ForgetResult.verified_clean`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:ForgetReport.verified_clean`
- **runtime** — `bio_agent_os/memory_constitution/laws.py:check_memory_not_evidence`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:_check_constitution`


### durable-forgetting / independent read-back of deletion

#### A live primary row makes the whole deletion a failure, however clean the derived stores are — and the verifier must not ask the deleter. Verification opens its own connection to the file and reads the table directly.

Six failures in the lifetime run were all forget_primary_survives; and if the deleter and its verifier share one filter bug they will agree the record is gone — a common-mode failure no amount of checking through one path can catch.

- **sự cố gốc** — commit c805662 'fix(privacy): phep xoa bo qua nhung ky uc no CHUA THE danh gia'; six cases frozen before the fix at tests/fixtures/forget_primary_survives.json, repro at tick 440 recorded in tests/test_forget_primary_survives.py:1-30
- **test** — `tests/test_forget_primary_survives.py::test_the_six_cases_are_on_disk_and_unchanged`
- **test** — `tests/test_forget_primary_survives.py::test_the_verifier_does_not_ask_the_deleter`
- **test** — `tests/test_forget_primary_survives.py::test_success_is_refused_while_the_primary_row_survives[S04/phone@500]`
- **test** — `tests/test_forget_primary_survives.py::test_success_is_refused_while_the_primary_row_survives[S09/job_title@500]`
- **test** — `tests/test_forget_primary_survives.py::test_success_is_refused_while_the_primary_row_survives[S00/city@750]`
- **test** — `tests/test_forget_primary_survives.py::test_a_deletion_reaches_memories_that_have_no_vector_yet[S04/phone@500]`
- **test** — `tests/test_forget_primary_survives.py::test_a_deletion_reaches_memories_that_have_no_vector_yet[S16/project@1000]`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:ForgetResult.succeeded`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:forget_scoped`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:verify`


### durable-forgetting / irreversible erasure integrity

#### A REDACTION CAN NEVER BE SILENT. erase_history rewrites the payload but leaves the checksum of the payload that used to be there, so the hash stops matching on purpose; a mismatch is tampering unless an erasure record explains it — delete the record to hide the erasure and it reads as tampering. · **mutant**

Without a deliberately broken checksum plus a separate erasure record, a payload rewrite is indistinguishable from silent tampering, and the erasure log would be decoration.

- **sự cố gốc** — commit 2b3eb52 'feat: tach ba nghia cua viec quen, va chan duong tai sinh'; 14/08/2026 measurement in bio_agent_os/cognitive/forgetting.py:17-27 (forget() -> verified_clean=True while cognitive_events.payload_json still holds the secret; rebuild_version + worker -> the secret is back, verbatim)
- **test** — `tests/test_memory_resurrection.py::test_the_erasure_is_recorded_with_who_why_and_the_old_checksum`
- **test** — `tests/test_memory_resurrection.py::test_integrity_check_accepts_a_recorded_redaction`
- **test** — `tests/test_memory_resurrection.py::test_integrity_check_still_catches_an_unrecorded_redaction`
- **test** — `tests/test_memory_resurrection.py::test_erase_history_refuses_without_explicit_confirmation`
- **test** — `tests/test_memory_resurrection.py::test_erase_history_requires_a_reason_and_an_actor`
- **test** — `tests/test_memory_resurrection.py::test_a_rebuild_cannot_resurrect_after_erase_history`
- **test** — `tests/test_memory_resurrection.py::test_no_query_path_returns_the_erased_content`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:erase_history`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:verify_event_integrity`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_redact_payloads`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:IntegrityVerdict`


### durable-forgetting / probe validity for no-resurrection

#### A resurrection probe must carry controls that PROVE the forget was not simply too broad: same subject / different predicate (B) and different subject / similar value (C) must both survive. B or C dying is FORGET SCOPE INVALID, not 'no resurrection'; A returning is RESURRECTION; and the mechanism being unreachable is a third failure class distinct from both. · **mutant**

An over-broad forget and a correctly forgetting system produce the same number — the first Lê Sơn probe deleted everything about the person, reported spans=0 on both branches, and read as success.

- **sự cố gốc** — commit 48d8006 'test(forget): probe resurrection dung lai — va no bao cao mot lop hong THU BA'; the invalidation is written up at tests/test_forget_resurrection.py:4-21, and the third failure class (mechanism unreachable, replay enqueues but nothing consumes) at :106-128, retired by commit 032889e when ReconciliationWorker became the consumer
- **test** — `tests/test_forget_resurrection.py::test_the_probe_is_valid_before_it_measures_anything`
- **test** — `tests/test_forget_resurrection.py::test_forgotten_evidence_stays_gone_through_replay_and_rebuild`
- **test** — `tests/test_forget_resurrection.py::test_the_mutant_resurrects_and_this_test_must_catch_it`
- **test** — `tests/test_forget_resurrection.py::test_the_correction_that_number_seven_rescues_is_not_the_forgotten_one`
- **test** — `tests/test_queued_forget_safety.py::test_e_control_without_forget_materialises`
- **test** — `tests/test_projection_materialization_audit.py::test_the_at_apply_tombstone_check_is_what_holds_a_queued_job`


### durable-forgetting / queued forget, revocation of future state

#### A DELETE OF CURRENT STATE IS NOT A REVOCATION OF FUTURE STATE. A forget must reach a logical memory that exists only as queued projection work; the identity that spans every representation is the event, not the memory row. · **mutant**

forget_derived resolved from the serving layer, found no row for an unmaterialised memory, and returned 'không tìm thấy ký ức nào khớp' about a memory the worker was about to write — then the worker wrote it, verbatim.

- **sự cố gốc** — commit 99218ae 'fix(forget): mot lenh quen phai di nhanh hon ky uc tren moi representation cua no'; deterministic repro in bio_agent_os/cognitive/forgetting.py:666-678 and tests/test_queued_forget_safety.py:10-12 (observe -> outbox pending, memories 0, tombstones 0; forget_derived -> verified_clean=False, tombstones 0, note 'không tìm thấy'; worker.run_once -> completed=1, memories 1)
- **test** — `tests/test_queued_forget_safety.py::test_a_enqueue_then_forget_then_drain`
- **test** — `tests/test_queued_forget_safety.py::test_a_the_tombstone_names_an_event_with_no_memory`
- **test** — `tests/test_queued_forget_safety.py::test_b_forget_then_replay_never_materialises`
- **test** — `tests/test_queued_forget_safety.py::test_c_forget_while_worker_holds_the_lease`
- **test** — `tests/test_queued_forget_safety.py::test_d_duplicate_jobs_neither_materialises`
- **test** — `tests/test_queued_forget_safety.py::test_mutant_forget_ignores_pending_projection`
- **test** — `tests/test_queued_forget_safety.py::test_e_control_without_forget_materialises`
- **test** — `tests/test_projection_safety_gate.py::test_case_03_forget_while_worker_holds_the_lease`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_events_awaiting_projection`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:forget_derived`


### durable-forgetting / replay preview fidelity

#### The tombstone is checked BEFORE dependencies and before the dry_run branch, so a dry run reports the same decision the real one would make. A preview that disagrees with the act is worse than no preview.

An operator previewing a recovery must see the same skipped_tombstoned count the real replay will produce, or the preview licenses an action it did not actually model.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; rule stated in the shipping comment at bio_agent_os/cognitive/projection_engine.py:356-358
- **test** — `tests/test_replay_resurrection.py::test_a_dry_run_makes_the_same_decision_as_the_real_one`
- **test** — `tests/test_replay_resurrection.py::test_a_replay_says_how_many_it_declined_to_restore`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.replay`


### durable-forgetting / report distinguishes now from after-rebuild

#### deleted_claims says the serving layer is clean NOW; tombstoned/survives_rebuild says it STAYS clean through a rebuild. These are two different facts and the report must carry both.

Before tombstones existed the first was true and the second was not, and the report only carried the first — so a clean-looking deletion was one maintenance command away from being undone.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; stated at bio_agent_os/cognitive/forgetting.py:200-204 and bio_agent_os/cognitive/forget_scope.py:186-187
- **test** — `tests/test_replay_resurrection.py::test_the_scoped_report_says_it_will_survive_a_rebuild`
- **test** — `tests/test_replay_resurrection.py::test_a_replay_says_how_many_it_declined_to_restore`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:ForgetReport.as_dict`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:ForgetResult.as_dict`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ReplayReport.render`


### durable-forgetting / residue verification scoping

#### A residue probe must identify the DELETED CONTENT, not the person it was about. A scoped deletion probes with the full sentences it removed; the six-character token net stays only for callers that have nothing else.

'Nguyễn' is exactly six characters, so the subject's own surname became a probe and every remaining memory about that person read as leftover — succeeded came back False on a finished job, and a caller acting on that retries or escalates a deletion that is already done.

- **sự cố gốc** — commit 0cd59aa 'fix(privacy): probe kiem chung lay TEN NGUOI lam moc -- va mot phat hien nghiem trong hon'; frozen case 3 repro recorded in tests/test_forget_probe_scoping.py:1-18 (deleting Nguyễn Dũng's city reported residue of his phone record) and in bio_agent_os/cognitive/forget_scope.py:502-511
- **test** — `tests/test_forget_probe_scoping.py::test_a_persons_name_is_not_used_as_a_residue_probe`
- **test** — `tests/test_forget_probe_scoping.py::test_a_scoped_deletion_probes_with_content_not_tokens`
- **test** — `tests/test_forget_probe_scoping.py::test_real_residue_is_still_reported`
- **test** — `tests/test_forget_probe_scoping.py::test_the_unrelated_memory_is_left_alone`
- **test** — `tests/test_forget_probe_scoping.py::test_the_token_fallback_still_exists_for_callers_without_content`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:_deletion_probes`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_probes`


### durable-forgetting / structured slot inside the perimeter

#### The structured slot column is inside the deletion perimeter, and a replay must not rebuild it: the slot comes back with the row or not at all.

A structured slot holds the value verbatim ({'attribute':'phone','value':'0977123456'}), so a verifier that scans content and not structured_json would report a clean deletion with the number still on disk.

- **sự cố gốc** — commit e72c646 'feat(predicate): slot duoc LUU chu khong doan lai — STATE_AT thang RECALL' added ('cognitive_memories','structured_json') to CONTENT_COLUMNS before anything wrote there; the replay half was closed in commit 502492c 'fix(predicate): hai loi CHI MANG do mot reviewer doc lap tim ra'; rationale at bio_agent_os/cognitive/forgetting.py:90-99
- **test** — `tests/test_structured_slots.py::test_the_deletion_verifier_scans_the_slot_column`
- **test** — `tests/test_structured_slots.py::test_a_deletion_leaves_nothing_in_the_slot_column`
- **test** — `tests/test_structured_slots.py::test_a_replay_does_not_rebuild_the_slot_either`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:CONTENT_COLUMNS`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:verify`


### durable-forgetting / three named erasure levels

#### The three erasure levels stay three, and every report names WHICH one ran, carries reversible, and — when reversible is True — names where the content survives. erase_history remains a separate call requiring confirm, reason and actor.

One word doing three jobs is how a caller comes to believe a forget() reached the event log; 'succeeded: true' and nothing else is true and misleading at once.

- **sự cố gốc** — commits 2b3eb52 'feat: tach ba nghia cua viec quen, va chan duong tai sinh' and 2c5c996 'feat(forget): bao cao tu khai no da chay muc xoa nao'; rationale in bio_agent_os/cognitive/forgetting.py:29-41
- **test** — `tests/test_replay_resurrection.py::test_the_report_says_which_of_the_three_erasures_it_ran`
- **test** — `tests/test_replay_resurrection.py::test_erase_history_is_still_a_separate_operation`
- **test** — `tests/test_replay_resurrection.py::test_the_scoped_report_says_it_will_survive_a_rebuild`
- **test** — `tests/test_replay_resurrection.py::test_the_event_log_keeps_the_payload_after_a_derived_delete`
- **test** — `tests/test_memory_resurrection.py::test_forget_derived_admits_the_content_survives_in_the_event_log`
- **test** — `tests/test_memory_resurrection.py::test_forget_projection_leaves_the_derived_stores_and_says_so`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:ERASURE_LEVEL`
- **runtime** — `bio_agent_os/cognitive/forget_scope.py:ForgetResult.as_dict`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:ForgetReport.as_dict`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_note_reversibility`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:erase_history`


### durable-forgetting / tombstone as second append-only fact

#### The fix is not to delete the event — the log is no longer the ONLY input to a rebuild. A tombstone is a second append-only fact of equal standing: replaying event + tombstone in order yields a serving layer without the content. The event still exists, still explains what the system once believed, and still cannot be materialised. · **mutant**

A derived deletion reported verified_clean=True and held until the first routine rebuild, which is exactly when nobody is watching — the secret came back verbatim.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; forty-line repro dated 2026-08-15 recorded verbatim in bio_agent_os/cognitive/tombstones.py:3-14 and tests/test_replay_resurrection.py:1-20 (remember -> forget_derived deleted=1 verified_clean=True -> cognitive_memories 0 -> replay+rebuild+drain -> cognitive_memories 1, back verbatim)
- **test** — `tests/test_replay_resurrection.py::test_a_rebuild_does_not_bring_back_what_was_forgotten`
- **test** — `tests/test_replay_resurrection.py::test_it_is_the_tombstone_doing_the_work`
- **test** — `tests/test_memory_resurrection.py::test_a_routine_rebuild_no_longer_resurrects_after_forget_derived`
- **test** — `tests/test_forget_resurrection.py::test_forgotten_evidence_stays_gone_through_replay_and_rebuild`
- **test** — `tests/test_forget_resurrection.py::test_the_mutant_resurrects_and_this_test_must_catch_it`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:place`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:SCHEMA`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:_bury`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.rebuild_version`


### durable-forgetting / tombstone auditability

#### LIFTING A TOMBSTONE IS A WRITE, NOT A DELETE. lift() appends who reversed it and why; rows are marked, never removed. A tombstone table where entries disappear cannot answer 'was this ever deleted', which is the question an audit asks.

An un-answerable 'was this ever deleted?' makes the whole deletion record decorative — and lifting is the one operation that can legitimately restore content, so it is the one that most needs a name on it.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; rule stated in bio_agent_os/cognitive/tombstones.py:38-40
- **test** — `tests/test_replay_resurrection.py::test_lifting_a_tombstone_is_a_write_not_a_delete`
- **test** — `tests/test_replay_resurrection.py::test_a_tombstone_is_auditable`
- **test** — `tests/test_replay_resurrection.py::test_it_is_the_tombstone_doing_the_work`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:lift`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:tombstones`


### durable-forgetting / tombstone check fails open on absence

#### No tombstone table means no deletions have ever been recorded, which is a different thing from an error. A store that has never deleted anything must not fail closed here, or nothing would ever project.

Failing closed on a missing memory_tombstones table would stop every projection in a fresh store — the safe check would become the expensive one and somebody would turn it off.

- **sự cố gốc** — commit c7696ff 'fix(privacy): xoa song sot qua replay -- bia mo ben canh nhat ky su kien'; rationale at bio_agent_os/cognitive/tombstones.py:119-123 and the cheapness argument at :111-114
- **test** — `tests/test_replay_resurrection.py::test_a_store_that_never_deleted_anything_still_projects`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried_among`


### durable-forgetting / tombstone over queue-row deletion

#### The queue row is deliberately NOT deleted. A job removed from the outbox is a decision no audit can see, and a re-enqueue from any producer would bring the content straight back. The tombstone is the durable fact.

Deleting the queued job would have looked like a fix and left the deletion revocable by the next producer, with no audit record that anything was removed.

- **sự cố gốc** — commit 99218ae 'fix(forget): mot lenh quen phai di nhanh hon ky uc tren moi representation cua no'; rationale stated at bio_agent_os/cognitive/forgetting.py:703-707 and tests/test_queued_forget_safety.py:21-23
- **test** — `tests/test_queued_forget_safety.py::test_d_duplicate_jobs_neither_materialises`
- **test** — `tests/test_queued_forget_safety.py::test_b_forget_then_replay_never_materialises`
- **test** — `tests/test_projection_safety_gate.py::test_case_07_duplicate_projection_jobs`
- **runtime** — `bio_agent_os/cognitive/forgetting.py:forget_derived`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:place`


### durable-forgetting / tombstoned job liveness

#### REPORTED SUCCESS != CONFIRMED TRANSITION. A job declined by the tombstone gate must reach a TERMINAL state; the return value of skip() is checked rather than discarded, and a transition counts as confirmed only when it is read back from the store. · **mutant**

The tombstone gate held (safety), but skip() was called with job_id in the event_id slot so its WHERE matched zero rows while metrics.tombstoned incremented anyway — the job stayed in_progress for ever, attempts grew unbounded, and drain() could never reach zero on any store that had ever deleted anything, so the rollback runbook's completion gate could not close.

- **sự cố gốc** — commit dcfc057 'fix(outbox): mot job bi chan phai den duoc trang thai ket thuc'; defect D1 documented in PROJECTION_MATERIALIZATION_AUDIT.md §6.2 and re-stated in the shipping comment at bio_agent_os/cognitive/reconciliation_worker.py:374-390
- **test** — `tests/test_outbox_skip_contract.py::test_a_buried_job_reaches_a_terminal_state`
- **test** — `tests/test_outbox_skip_contract.py::test_the_rollback_runbook_completion_gate_closes`
- **test** — `tests/test_outbox_skip_contract.py::test_mutant_skip_using_job_id`
- **test** — `tests/test_projection_materialization_audit.py::test_a_tombstoned_job_is_retired_and_the_queue_can_drain`
- **test** — `tests/test_projection_safety_gate.py::test_case_10_buried_job_reaches_terminal_state`
- **test** — `tests/test_projection_safety_gate.py::test_case_11_rollback_drain_to_zero`
- **test** — `tests/test_projection_safety_gate.py::test_false_success_report_rate_is_zero`
- **test** — `tests/test_projection_safety_gate.py::test_mutant_ignore_callee_false_and_increment_success_metric`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.process`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.skip`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:TransitionLedger`


### durable-forgetting / tombstones survive historical migration and version rebuild

#### A tombstoned event may keep a ledger row written by a live pipeline before it was forgotten — that is historical evidence of 'this was once built'. What is forbidden is the MIGRATION issuing a ledger for a buried row. Adoption writes no outbox row for a tombstone: the tombstone is the authority, the audit only keeps the book. · **mutant**

A migration that grants a passport to a buried row hands replay a route back in; conversely a rule banning ALL ledgers on tombstones produced a false-red immediately after install, on two genuinely historical rows written by canary-a4 and hook-22344.

- **sự cố gốc** — commit 2cc4489 'feat(HBF-3): lich su da nhap tich — 270 ho so, MOT lan thay generation, no replay 305 -> 0'; the false-red caught after install is recorded at tests/test_historical_adoption.py:160-167 and in the shipping comment at bio_agent_os/cognitive/historical_adoption.py:770-776
- **test** — `tests/test_historical_adoption.py::test_tombstoned_event_may_keep_a_live_pipeline_ledger`
- **test** — `tests/test_historical_adoption.py::test_adopt_zero_semantic_delta_and_complete_triples`
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:verify_closure_from_audit`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:SEMANTIC_TABLES`


### durable-forgetting / two independent tombstone gates

#### Two checks rather than one, because a queue outlives the decision that filled it: at enqueue a tombstoned event is never offered to the outbox; at apply a job already queued is skipped when it comes up. EITHER ALONE LEAVES A WINDOW. · **mutant**

A deletion landing between a replay() and the worker draining it slips past the enqueue gate; a caller enqueueing directly slips past the apply gate — measured, blinding either one alone resurrects the memory on the fixture where the other cannot vote.

- **sự cố gốc** — PROJECTION_MATERIALIZATION_AUDIT.md §5 F2 matrix (f2-clean: worker completed=2 tombstoned=1 / f2-blind2: worker completed=3 tombstoned=0) and §5 conclusion 'PROVEN — bolt 2, the at-apply check at reconciliation_worker.py:334, is load-bearing'; gates landed in commit c7696ff
- **test** — `tests/test_forget_resurrection.py::test_replay_gate_is_not_the_only_guard`
- **test** — `tests/test_projection_materialization_audit.py::test_the_at_apply_tombstone_check_is_what_holds_a_queued_job`
- **test** — `tests/test_projection_materialization_audit.py::test_blinding_only_the_at_apply_check_resurrects_it`
- **test** — `tests/test_queued_forget_safety.py::test_mutant_ignore_reconciliation_worker_buried_check`
- **test** — `tests/test_forget_resurrection.py::test_the_mutant_resurrects_and_this_test_must_catch_it`
- **test** — `tests/test_projection_safety_gate.py::test_case_01_forget_before_enqueue`
- **test** — `tests/test_projection_safety_gate.py::test_case_02_enqueue_then_forget`
- **test** — `tests/test_projection_safety_gate.py::test_case_05_forget_then_replay`
- **test** — `tests/test_projection_safety_gate.py::test_case_06_forget_then_reprojection`
- **test** — `tests/test_projection_safety_gate.py::test_case_07_duplicate_projection_jobs`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.replay`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.rebuild_version`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.process`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried_among`


### durable-forgetting / validity of the no-resurrection measurement

#### UNREACHABLE SAFE PATH != VERIFIED SAFETY. A forgetting-safety case is PASS only with all four of CONTROL_EXECUTED, DANGEROUS_PATH_EXECUTED, MUTANT_TRIGGERED_PROHIBITED_STATE, CLEAN_PREVENTED_PROHIBITED_STATE. A mutant that cannot trigger the prohibited state is INVALID, not PASS; a production path that cannot be reached is NOT_EVALUABLE. The prohibited state is stated as persistent state read back from the store, never as a function return value. · **mutant**

Two probes in this project reported perfect forgetting for the wrong reason and both looked exactly like PASS: an over-broad forget reported '0 resurrection' with nothing left to resurrect, and a replay probe reported clean while no consumer was running.

- **sự cố gốc** — commit e0bdef9 'feat(gate): Projection Safety Gate v1 — 9 PASS / 2 INVALID / 0 FAIL, dung nhu da do'; the two saving incidents are named in bio_agent_os/evals/projection_safety_gate.py:14-18 and the second is documented in PROJECTION_MATERIALIZATION_AUDIT.md §4 (the ENQUEUE-ONLY finding against tests/test_forget_resurrection.py)
- **test** — `tests/test_projection_safety_gate.py::test_case_01_forget_before_enqueue`
- **test** — `tests/test_projection_safety_gate.py::test_case_02_enqueue_then_forget`
- **test** — `tests/test_projection_safety_gate.py::test_case_03_forget_while_worker_holds_the_lease`
- **test** — `tests/test_projection_safety_gate.py::test_case_04_forget_after_materialisation`
- **test** — `tests/test_projection_safety_gate.py::test_case_05_forget_then_replay`
- **test** — `tests/test_projection_safety_gate.py::test_case_06_forget_then_reprojection`
- **test** — `tests/test_projection_safety_gate.py::test_case_07_duplicate_projection_jobs`
- **test** — `tests/test_projection_safety_gate.py::test_case_08_worker_retry`
- **test** — `tests/test_projection_safety_gate.py::test_case_09_worker_restart`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:CaseResult.state`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:CaseResult`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:TransitionLedger`


### historical contract archaeology and naturalization (HBF lanes)

#### CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT — equal content plus equal source link does not prove the two writers produced the same projection; a comparator must measure the whole reproducible contract (scores, memory_type, lifecycle_state, verification_status, governance defaults, applicable_context, metadata echo), not just content. · **mutant**

SP-0 measured a real product regression the outbox builder hid behind identical content: every score fell back to the 0.5 default model and metadata.state vanished, moving retrieval score 3.330 -> 3.172 on the same query — a content-only comparator would have certified that as parity.

- **sự cố gốc** — commit bac0f96 "forensic(SP-0): SEMANTIC PARITY FAIL — builder outbox danh roi call-site semantics"
- **test** — `tests/test_projection_equivalence.py::test_m2_mutant_content_identical_but_full_field_altered_must_die`
- **test** — `tests/test_projection_equivalence.py::test_degraded_to_content_only_must_die`
- **test** — `tests/test_projection_equivalence.py::test_full_contract_passes_and_counts_fields`
- **test** — `tests/test_historical_adoption.py::test_m2_full_field_altered_content_identical_must_die`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:compare`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:hook_contract`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:EVENT_DERIVED`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:build_memory_from_event`

#### Proof names must be weaker-or-equal to what measurement proves. A proof class may only ever be DOWNGRADED (full_..._v1 -> full_..._except_<dimension>_v1, joined with _and_ if an except_ clause already exists); there is no promote path back up to FULL. · **mutant**

The first equivalence comparator called itself FULL while measuring only 6 fields — the same false-green family as SP-0 — and the resulting "240 ADOPT_FULL_CONTRACT" was a stronger claim than the measurement; the truthful number under a real 20+ field comparator was 203.

- **sự cố gốc** — commit c15f3d8 "law(HBF-1.1): chu FULL gio co rang that — 240 xuong 203, va do la tin tot"; activation/HBF1_MIGRATION_PLAN.md section "HBF-1.1 — số đo cuối"
- **test** — `tests/test_projection_equivalence.py::test_full_contract_passes_and_counts_fields`
- **test** — `tests/test_projection_equivalence.py::test_curated_proof_is_declared_weaker`
- **test** — `tests/test_historical_adoption.py::test_structured_content_closure_downgrades_not_forces`
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:PROOFS`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:_downgrade_structured`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:EquivalenceVerdict`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:compare`

#### PRESENT != CLOSED — a triple (outbox row, ledger row, audit row) that is merely present is not closed. Closure requires every link to be semantically correct: outbox.status/projection_key/tenant_id, ledger.target_id/type/version/tenant_id, audit.management_action/event_id. Present-but-wrong must raise, not count as a reapply no-op. · **mutant**

The HBF-2 `_triple_state` returned three existence booleans, so a triple with the wrong outbox status or a ledger pointing at the WRONG memory would be silently counted as an idempotent no-op — the migration would walk past a broken row and report "already done", and K4 could not see it because K4 re-ran on a triple it had just built correctly.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "HBF3-0 — CLOSURE LAW: PRESENT != CLOSED" (mutants C1 wrong status, C2 ledger points at wrong memory); commit 2cc4489 "feat(HBF-3): lich su da nhap tich"
- **test** — `tests/test_historical_adoption.py::test_hbf3_0_closure_law_present_is_not_closed`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:ClosureState.closed`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:closure_state`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:verify_closure`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`

#### Post-install closure must be read from the audit ledger, not from a fresh re-classification. After install the fresh classifier sees every adopted row as ALREADY_MANAGED, so a "closure PASS" computed from it measures almost nothing; the population must be the 307 audit rows, each re-proving outbox status, ledger target and absence of a stale lease. · **mutant**

The fresh-process certification for HBF-3 measured only 2 of 307 rows (the two tombstones) and still reported "2/2 closed" — a green that measured essentially nothing, caught by the adversarial review before the migration touched the canonical store.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "Review đối kháng TRƯỚC khi chạm động mạch — 4 finding sống sót", finding #3 ("Fresh-process cert chỉ đo 2/307 hàng"); verified result in activation/HBF3/hbf3_report.json key whole_population_closure = {checked: 307, closed: 307}
- **test** — `tests/test_historical_adoption.py::test_tombstoned_event_may_keep_a_live_pipeline_ledger`
- **test** — `tests/test_historical_adoption.py::test_k4_reapply_is_idempotent`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:verify_closure_from_audit`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`

#### A tombstoned event MAY legitimately carry a ledger row written by a live pipeline — that row is historical evidence the projection was once built, and it is what blocks resurrection on replay. What is forbidden is the MIGRATION issuing a ledger row for a buried row (ledger.worker_id == 'migration:hbf-adopt'). · **mutant**

The first closure checker banned all ledgers on tombstones and produced a false-red immediately after the HBF-3 install: two tombstoned events carried ledgers written by `canary-a4` and `hook-22344` while they were still alive, before being forgotten — banning them was banning the past.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "Ghi sổ trung thực" ("closure cấm nhầm ledger cũ của bia mộ", one of the 4 harness measurement errors in the post-install phase)
- **test** — `tests/test_historical_adoption.py::test_tombstoned_event_may_keep_a_live_pipeline_ledger`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:verify_closure_from_audit`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:MIGRATION_ACTOR`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`

#### MARKER-ONLY IS NOT ENOUGH TO SAY "NOTHING IS OWED" — content shape does not decide whether a projection is owed; the RELATION to an already-materialized memory decides. An event whose rendered content is a bare `hook=...` marker but which has a memory row linked to it must be classified as ADOPT, never EVENT_ONLY_SKIP. · **mutant**

Three real `hook=SessionStart` events from 07/08 — when the legacy path still materialized markers — were classified EVENT_ONLY_SKIP, so the audit would have declared "there is no projection here" while the memory was sitting right there, and those three memories would have been left with no ledger: exactly the hole the naturalization ceremony exists to close.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "Review đối kháng TRƯỚC khi chạm động mạch", finding #2 (3 marker-only events with materialized memories)
- **test** — `tests/test_historical_adoption.py::test_marker_only_with_materialized_memory_is_adopted_not_skipped`
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:_marker_only`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_invariants`

#### MISSING LEDGER != MISSING PROJECTION — the absence of a ledger row for a historical event does not mean nothing was ever built. A pre-outbox store carries hundreds of real, correct projections with no ledger, and treating "no ledger" as "never built" makes replay construct a SECOND memory. · **mutant**

The HBF-0 inventory classified 316 historical events with UNCLASSIFIED = 0 and found 240 legacy projections that were contract-equivalent but ledger-less; the M1 witness proves the cost of the opposite worldview by measurement — delete one adopted ledger row, replay + drain, and the store ends with duplicate = 2.

- **sự cố gốc** — commit b8424b5 "inventory(HBF-0): 316 event lich su phan lop het, UNCLASSIFIED = 0"; M1 witness recorded in activation/HBF1_MIGRATION_PLAN.md ("M1 DIES — ... phớt lờ → duplicate=2")
- **test** — `tests/test_historical_adoption.py::test_m1_missing_ledger_worldview_must_die`
- **test** — `tests/test_historical_adoption.py::test_k1_replay_no_duplicates_and_ledger_authority`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_invariants`

#### HAVING A ROLLBACK POINT != SAFE LIVE MUTATION. A migration may only mutate an OFFLINE candidate; the single road back to canonical is certify -> move-aside -> install into an empty slot -> independent verify, and that path must be fail-closed along its WHOLE length, not just the move-aside loop. · **mutant**

SP-2's repair was correct about the product but wrong about procedure — it opened a write connection and UPDATEd the real store because a backup existed; and even the replacement primitive was only half fail-closed: copy2/replace/verify sat outside any restore branch, so a Windows scanner holding the `.installing` file would leave canonical moved away with nothing in its place while the caller read "not installed" and re-enabled the hook against a store that did not exist.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "Nợ thủ tục ghi sổ (luật mới của HBF)" and adversarial-review finding #1; commits 0498467 "fix(SP-1/SP-2)" and 06f7049 "feat(lane2): safe rollback primitive — GENERATION REPLACEMENT BEATS IN-PLACE RESTORATION"
- **test** — `tests/test_store_generation.py::test_install_failure_after_move_aside_restores_canonical`
- **test** — `tests/test_store_generation.py::test_live_handle_fails_closed_and_canonical_survives`
- **test** — `tests/test_store_generation.py::test_no_copy_over_canonical_rollback_code_returns`
- **test** — `tests/test_store_generation.py::test_mutant_the_old_bug_still_corrupts_and_the_primitive_does_not`
- **test** — `tests/test_historical_adoption.py::test_adopt_zero_semantic_delta_and_complete_triples`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:install_generation`
- **runtime** — `bio_agent_os/cognitive/store_generation.py:certify_candidate`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:tables_digest`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:SEMANTIC_TABLES`

#### Provenance lives in its own home: a LEASE field is not a provenance bag, and a NULL-able audit column may only be filled when the claim is real. Terminal rows written by the migration carry locked_by=NULL / locked_at=NULL (the exact terminal shape of production complete()/skip()); provenance goes to ledger.worker_id + the audit table. NOT_APPLICABLE must be distinguishable from version 1. · **mutant**

The first HBF-2 transaction stuffed 'migration:hbf-adopt' into outbox.locked_by, leaving 303 "ghost workers" alive forever in every ownership query; and it wrote builder_version_checked=1 for curated/tombstone/event-only rows, so a future audit would have claimed "this curated row was checked against builder v1" about a comparison that never happened.

- **sự cố gốc** — commit 1e20670 "fix(HBF-2.1): provenance ve dung nha — locked_by khong phai tui dung, NULL khong phai 1"; activation/HBF1_MIGRATION_PLAN.md section "HBF-2.1 — CORRECTION PROVENANCE"
- **test** — `tests/test_historical_adoption.py::test_p1_mutant_lease_as_provenance_must_die`
- **test** — `tests/test_historical_adoption.py::test_p2_mutant_curated_builder_claim_must_die`
- **test** — `tests/test_historical_adoption.py::test_audit_provenance_by_class_and_terminal_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_invariants`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:AUDIT_SCHEMA`

#### Adopt AS-IS: "we understand why it differs" never becomes "we are allowed to rewrite the past". Drift rows keep their real observed_at, the real delta is written into the audit, and the proof keeps the word `except_observed_at...`. The epsilon is a MEASURED admissibility policy for this population (positive-only, 0.505-53.861 ms), not a law that "the same call is always <100ms".

Normalising the 46-48 drifted observed_at values would have converted a measured, explained mechanism (the pre-inheritance write path stamped its own clock) into a fabricated FULL count — buying a prettier number by editing history, which is the exact failure the proof-naming discipline was paid for.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "HBF-2 — KẾT QUẢ DIỄN TẬP" constraint 1; measured drift in activation/HBF3/hbf3_report.json key drift_stats = {n: 48, min_ms: 0.505, max_ms: 53.861}
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **test** — `tests/test_historical_adoption.py::test_adopt_zero_semantic_delta_and_complete_triples`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:DriftPolicy.admits`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:tables_digest`

#### structured_content must be CLOSED before the transaction opens. Reconstructable by the deterministic slot extractor -> reconstructed_equal; not reconstructable -> the proof is DOWNGRADED and the row is still adopted as-is (the count is never forced); not measured at all -> the admissibility gate stays SHUT. · **mutant**

structured_content is the one contract field derived by running code rather than inherited from the event, so leaving it unmeasured would have let the migration hand out a FULL-named proof over a dimension nobody had compared — the same shape as the 6-field comparator calling itself FULL.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "HBF-2 — KẾT QUẢ DIỄN TẬP" constraint 2 ("240/240 hook rows reconstructed_equal"); commit 7c3ee9f "feat(HBF-2): le nhap tich chay dien tap offline"; result in activation/HBF3/hbf3_report.json key structured_content_closure
- **test** — `tests/test_historical_adoption.py::test_structured_content_closure_downgrades_not_forces`
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_gate`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:_downgrade_structured`

#### A naturalization ceremony that issues extra passports on its second run is not a ceremony, it is a photocopier (K4). Re-applying the migration — with the same report or with a freshly computed one — must produce zero new outbox/ledger/audit rows and an identical digest across ALL tables.

Migrations get re-run after crashes, restarts and partial installs; without idempotency keyed on semantic closure of the triple, a rerun would duplicate ledger authority and audit provenance for events that were already naturalized.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "Bốn dao thử (K1–K4)", K4 ("Lễ nhập tịch chạy lần hai mà cấp thêm hộ chiếu thì là máy photocopy"); commit 7c3ee9f
- **test** — `tests/test_historical_adoption.py::test_k4_reapply_is_idempotent`
- **test** — `tests/test_historical_adoption.py::test_k3_restart_managed_state_durable`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:closure_state`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:AdoptionResult`

#### The adoption transaction is all-or-nothing: an injected failure before COMMIT must leave ZERO partial state — no orphan terminal outbox row, no ledger without audit, no audit without ledger — and the real ceremony must still run intact afterwards. · **mutant**

Partial naturalization is worse than none: an outbox row marked COMPLETED with no ledger tells replay the debt is settled while nothing owns the memory, which is precisely the state the ledger authority exists to make impossible.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md section "ADMISSIBILITY (tách khỏi K1–K4)"; verified in activation/HBF2/hbf2_report.json / plan line "abort test inject trước COMMIT → ZERO partial state"
- **test** — `tests/test_historical_adoption.py::test_admissibility_abort_before_commit_zero_partial`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:InjectedAbort`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_invariants`

#### The admissibility gate closes if ANY blocking class is non-zero (TRUE_MISSING, AMBIGUOUS, DIVERGENT, ANOMALY, UNEXPLAINED) or if the per-class counts do not balance against the total population. The migration may not run on a population it cannot fully explain. · **mutant**

HBF-0 established the discipline that every historical event must land in a named class with UNCLASSIFIED = 0 before anything is adopted; the M1 and M2 knives then show the gate actually firing — a blinded worldview yields TRUE_MISSING and a single altered contract field yields DIVERGENT, both of which must stop the run rather than adopt around them.

- **sự cố gốc** — commit b8424b5 "inventory(HBF-0): 316 event lich su phan lop het, UNCLASSIFIED = 0"; activation/HBF0_1_NOTE.md
- **test** — `tests/test_historical_adoption.py::test_m1_missing_ledger_worldview_must_die`
- **test** — `tests/test_historical_adoption.py::test_m2_full_field_altered_content_identical_must_die`
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_gate`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:BLOCKING_CLASSES`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:ClassificationReport.counts`

#### UNKNOWN WRITER never adopts. A source with no named contract is classified UNKNOWN_WRITER with proof=None and equivalent=False — being unable to name the writer's contract is a reason to stop, not a reason to assume equivalence.

HBF-0.1 left 27 rows as CONTRACT_UNKNOWN; they were only adoptable after archaeology named their writer (the 06/08 hand-curated seed) and gave it an explicitly weaker contract, curated_seed_v1 with proof content_plus_curated_provenance_v1 — because the event carries only content and no formula can reconstruct the author's chosen intent.

- **sự cố gốc** — activation/HBF0_1_NOTE.md ("CONTRACT_UNKNOWN 27 ← nguồn không phải claude-code hook; HBF-1 phải gọi tên contract của writer"); commit ebcbd20 "plan(HBF-1): 27 ky uc cuoi da duoc goi ten — equivalence thanh luat thuc thi duoc"
- **test** — `tests/test_projection_equivalence.py::test_unknown_writer_never_adopts`
- **test** — `tests/test_projection_equivalence.py::test_curated_proof_is_declared_weaker`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:compare`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:PROOFS`

#### Curated rows keep canonical_candidate_hash = NULL and structured_content_status = curated_preserved_as_authored. A pretty but meaningless canonical hash would lie; NULL is more honest, because the persisted memory IS the authored contract and no builder comparison was ever performed. · **mutant**

The 27 hand-seeded memories from 06/08 had author-chosen confidence/importance/type that no formula can reproduce; synthesising a canonical candidate for them would manufacture a comparison that never happened and let the audit claim a builder check it did not do.

- **sự cố gốc** — activation/HBF1_MIGRATION_PLAN.md sections "Khảo cổ 27 CONTRACT_UNKNOWN — đã gọi tên" and "HBF-2.1 — CORRECTION PROVENANCE" item 2; commit 1e20670
- **test** — `tests/test_historical_adoption.py::test_classification_matches_population_shape`
- **test** — `tests/test_historical_adoption.py::test_p2_mutant_curated_builder_claim_must_die`
- **test** — `tests/test_historical_adoption.py::test_audit_provenance_by_class_and_terminal_shape`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:classify_store`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adopt`
- **runtime** — `bio_agent_os/cognitive/historical_adoption.py:adoption_invariants`

#### ABSENCE OF OUTBOX != EVIDENCE THAT PROJECTION IS OWED. A write-time decision not to project must become durable evidence in two independent layers — the immutable, checksummed event payload (projection_intents.cognitive_memory.decision = 'no_projection') and a terminal SKIPPED row written in the SAME transaction as the event. Replay reads evidence, never absence. · **mutant**

The HBF-3 canary measured it: under OUTBOX a non-substantive hook wrote no outbox row at all, so ProjectionReplayEngine.scan classified it MISSING_OUTBOX — permanent actionable debt — and `replay --repair` would materialize `hook=SessionStart` into a memory, the exact head the substantive gate had cut. The dragon regrew not through the write path but through the recovery path.

- **sự cố gốc** — activation/HBF3_FINDING_non_substantive_debt.md (found in the HBF-3 canary, 19/08); closed by commit 032889e "feat(RC-0): replay intent closure — quyet dinh luc ghi la bang chung, khong phai su vang mat"
- **test** — `tests/test_replay_intent_closure.py::test_r2_absence_read_as_owed_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r1_dropping_the_durable_skip_row_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_replay_repair_cannot_materialize_a_non_substantive_event`
- **test** — `tests/test_replay_intent_closure.py::test_lost_skip_row_is_reconstructed_as_skip_not_as_memory`
- **test** — `tests/test_replay_intent_closure.py::test_live_non_substantive_event_gets_an_explicit_durable_skip`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.scan`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:no_projection_fragment`
- **runtime** — `bio_agent_os/cognitive/event_store.py:SQLiteEventStore.append`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`

#### INGEST-TIME NO-PROJECTION DECISION MUST SURVIVE REPLAY AND VERSION REBUILD. rebuild_version skips events carrying a no_projection decision, and treats a SKIPPED row at ANY version as terminal — "not worth becoming a memory" is a proposition about the EVENT, not about the version of the projection logic, so it does not expire when the builder is upgraded. · **mutant**

A second egg under the same shell: even with the durable SKIPPED row, rebuild_version only accepted SKIPPED as terminal at the CURRENT projection version, so a v1 -> v2 bump would make the old row stop matching and the engine would build a v2 candidate for exactly the marker it had just refused.

- **sự cố gốc** — docs/RC0_REPLAY_INTENT_CLOSURE.md section "Bệnh" (owner caught the second layer); commit 032889e "feat(RC-0): replay intent closure"
- **test** — `tests/test_replay_intent_closure.py::test_r3_ignoring_skip_during_version_rebuild_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_version_rebuild_cannot_materialize_a_skipped_event`
- **test** — `tests/test_replay_intent_closure.py::test_substantive_event_still_owes_and_still_gets_built`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.rebuild_version`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:intent_from_payload`

#### UNKNOWN INTENT MUST NOT AUTO-MATERIALIZE, and must never be inferred from content shape. An event with no outbox row and no recorded decision is reported as UNKNOWN_INTENT with owed=False (visible to an operator, never actionable); guessing 'no projection owed' from marker-looking text is mutant R5 and must die. · **mutant**

Shape-guessing was already measured wrong once in this exact lane: three events that looked like bare `hook=SessionStart` markers DID have real memories behind them. So the 5 pre-RC-0 marker events left in the real store were deliberately NOT backfilled to SKIPPED — back-filling would be the same guess-by-shape, and the correct outcome is UNKNOWN_INTENT with actionable = 0.

- **sự cố gốc** — docs/RC0_REPLAY_INTENT_CLOSURE.md sections "Đo" (R4/R5) and "Store thật" ("marker cũ (trước RC-0) 5 event → UNKNOWN_INTENT, actionable = 0, KHÔNG backfill"); the 3-marker measurement in activation/HBF1_MIGRATION_PLAN.md adversarial finding #2
- **test** — `tests/test_replay_intent_closure.py::test_r4_unknown_intent_assumed_owed_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r5_deciding_skip_from_marker_text_alone_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_unknown_intent_is_reported_but_never_actionable`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ReplayCandidate.actionable`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.replay`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ReplayReason`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`


### multi-worker concurrency / admissibility of measurement

#### A GREEN CONCURRENCY RESULT WITHOUT PROVEN INTERLEAVING IS INVALID, NOT PASS — and the witness must be a synchronisation event (barrier / Event), never a timestamp.

A green case where two workers happened not to meet says only that they did not meet. Timestamps cannot serve as the witness: two consecutive `time.time()` reads on this machine were measured equal 199986/200000 times, and timestamp coincidence broke two conclusions in this lane (including a correction that the published 15.625 ms resolution was not the causal boundary — real granularity ~0.51 ms). The same shape is encoded in shipping code as INVALID / NOT_EVALUABLE states rather than as a score.

- **sự cố gốc** — H1_QUEUE_LIVENESS_REPORT.md entries #1–#3 (a FAIL retracted for being an unrepeated observation) and its 18/08 clock correction; commit 4f07306 "docs: scoped clock correction — hai loai, khong gop"; H1_4_MULTIWORKER_REPORT.md admissibility gate
- **test** — `tests/test_multiworker_concurrency.py::test_case_01_double_reclaim_of_the_same_expired_job`
- **test** — `tests/test_multiworker_concurrency.py::test_case_02_a_stale_yield_cannot_destroy_a_live_lease`
- **test** — `tests/test_multiworker_process_level.py::test_case_01_process_level_reclaim_has_one_owner_in_valid_domain`
- **test** — `tests/test_multiworker_process_case02.py::test_case_02_process_level_stale_yield_cannot_destroy_live_lease`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_03_healthy_progress_with_two_workers_and_a_poison_job`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_04_concurrent_scoped_workers_never_cross_tenants`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_06_duplicate_detector_actually_detects`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:CaseResult.state`


### multi-worker concurrency / claim eligibility

#### ELIGIBILITY MUST RESPECT A LIVE LEASE — a row whose lease has not expired is not claimable, and the predicate lives in both the SELECT and the UPDATE guard. · **mutant**

This is the single load-bearing column of the two-worker property: three earlier mutants (remove yield, shift both clocks, skew one worker's clock) all failed to starve healthy work, but removing the lease predicate from eligibility in BOTH workers hands the head-of-queue poison job to every worker on every round and healthy jobs never materialise.

- **sự cố gốc** — H1_4_MULTIWORKER_REPORT.md, section "Ba mutant chết non — và chúng dạy về kiến trúc"; commit a2ea71b "test(H1.4): cases 03/04/05/07/06 + mutants — H1 GLOBAL VERIFIED"
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_03_healthy_progress_with_two_workers_and_a_poison_job`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_03_mutant_ignoring_live_leases_starves_healthy`
- **test** — `tests/test_lease_contract.py::test_shadow_drain_does_not_mistake_a_live_lease_for_an_empty_queue`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`


### multi-worker concurrency / forget-vs-reclaim race

#### A FORGET LANDING INSIDE THE RECLAIM WINDOW MUST WIN — a job reclaimed after a deletion re-checks burial at write time and skips; the queue outlives the decision that filled it. · **mutant**

The reclaim window is exactly the window a deletion runs in: a worker dies holding a job, `forget_derived` lands, and a peer then reclaims and processes the job. Without the at-write-time burial check the forgotten content is materialised again — the mutant `IGNORE_RECONCILIATION_WORKER_BURIED_CHECK` reproduces the resurrection, which is what makes the clean case load-bearing rather than vacuously green.

- **sự cố gốc** — commit 99218ae "fix(forget): mot lenh quen phai di nhanh hon ky uc tren moi representation cua no" (P0_A_FORGET_SAFETY_REPORT.md), closed for the concurrent case by commit a2ea71b case 07
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_07_forget_landing_inside_the_reclaim_window_still_wins`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_07_mutant_blind_burial_resurrects`
- **test** — `tests/test_projection_safety_gate.py::test_case_03_forget_while_worker_holds_the_lease`
- **test** — `tests/test_projection_safety_gate.py::test_case_09_worker_restart`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.process`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried`


### multi-worker concurrency / mutual exclusion

#### AT MOST ONE OWNER PER JOB — `double_claim_ack_rate = 0` at thread level and at process level; the question is who was TOLD the job is theirs, not what the final row says.

A WIP reproduction showed two processes both being granted the same job (2/10 isolated, 5/25 in sweep); the sweep proved the cause was zero-duration lease, not broken CAS, and the remaining contract is that with a valid lease and an explicitly past-expiry clock the racing UPDATE's own `status`/`locked_at` predicate lets exactly one winner through — the loser gets rowcount 0, which is losing a race, not an error.

- **sự cố gốc** — commit 75d14f3 "wip(H1.4): tai hien double-claim o muc TIEN TRINH — chua tin duoc" then commit 5ec90c7 "test(H1.4): case 01 process-level trong MIEN LEASE HOP LE — mot chu duy nhat"
- **test** — `tests/test_multiworker_concurrency.py::test_case_01_double_reclaim_of_the_same_expired_job`
- **test** — `tests/test_multiworker_process_level.py::test_case_01_process_level_reclaim_has_one_owner_in_valid_domain`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_05_crash_then_peer_reclaims_exactly_once_and_completes`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`


### multi-worker concurrency / yield-vs-claim race

#### A STALE OBSERVATION MUST NOT DESTROY A LIVE LEASE — the yield write is a compare-and-set on (status, locked_by, locked_at) using `IS`, not `=`; losing the race is rowcount 0, not an error. · **mutant**

Reproduced with synchronous witnesses in three steps: A decides to yield X; B claims X and commits; A runs its now-stale UPDATE — leaving `X status=pending locked_by=None available_at=<future>` while B still believes it owns X and is processing it. The queue considered a live job ownerless and re-assignable.

- **sự cố gốc** — commit 70c469e "fix(outbox): mot quan sat cu khong duoc pha mot lease dang song"; process-level closeout in commit 3500492, reported in H1_4_MULTIWORKER_REPORT.md
- **test** — `tests/test_multiworker_concurrency.py::test_case_02_a_stale_yield_cannot_destroy_a_live_lease`
- **test** — `tests/test_multiworker_concurrency.py::test_mutant_yield_ignores_current_claim_state`
- **test** — `tests/test_multiworker_process_case02.py::test_case_02_process_level_stale_yield_cannot_destroy_live_lease`
- **test** — `tests/test_multiworker_process_case02.py::test_case_02_process_level_mutant_destroys_live_lease`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox._yield_expired_leases`


### production hook single-writer

#### SINGLE WRITER BY MODE — under OUTBOX the reconciliation worker is the one and only materialization authority; the hook adapter must not also call remember() · **mutant**

Through the real production hook command under outbox, 3 prompts produced 6 memories: observe() grew a projection job (worker built copy #1) while remember() wrote copy #2 directly, and the ledger could not catch it because the direct write never passes through the ledger.

- **sự cố gốc** — commit 73ebb14 'activation(A5): FAIL dung stop-condition — hook adapter double-materialize duoi outbox' (A5.4 window: 3 prompt -> 6 memories; report activation/A5_REPORT.md); fixed in commit 3baa422 'fix(hooks): single writer by mode — OUTBOX co dung MOT materialization authority' with an L3-A clean-clone repro of 1 event -> 2 memories
- **test** — `tests/test_hook_single_writer.py::test_outbox_substantive_worker_is_the_only_writer`
- **test** — `tests/test_hook_single_writer.py::test_m1_exact_incident_mutant_restoring_remember_duplicates`
- **test** — `tests/test_hook_single_writer.py::test_legacy_substantive_one_memory_no_production_debt`
- **test** — `tests/test_hook_single_writer.py::test_shadow_semantics_unchanged`
- **runtime** — `bio_agent_os/cognitive/hooks.py:ClaudeCodeHookAdapter._direct_write_allowed`
- **runtime** — `bio_agent_os/cognitive/hooks.py:ClaudeCodeHookAdapter.ingest`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:CognitiveMemoryBuilder.build`

#### AN EVENT NOT WORTH A MEMORY MUST NOT OWE A PROJECTION — the substantive gate has to hold on the outbox path too, or the duplicate fix re-opens the noise channel · **mutant**

Without this second latch, routing every hook through the outbox would have made `hook=SessionStart` markers materialize again — measured 2026-08-07, three of the five slots a recall returned were rows whose entire content was `hook=SessionStart`, on a store holding the company bank account and a customer's real pricing.

- **sự cố gốc** — commit 87ec0f3 'fix(hooks): mot hook chay la mot SU KIEN, khong tu dong la mot KY UC' (measurement of 2026-08-07 recorded in the hooks.py comment and tests/test_hook_noise_filter.py docstring); re-armed for the outbox path in commit 3baa422
- **test** — `tests/test_hook_single_writer.py::test_outbox_non_substantive_stays_event_only`
- **test** — `tests/test_hook_single_writer.py::test_m2_substantive_mutant_materializes_markers`
- **test** — `tests/test_hook_noise_filter.py::test_a_bare_hook_records_an_event_but_not_a_memory`
- **test** — `tests/test_hook_noise_filter.py::test_the_noise_no_longer_crowds_out_the_signal`
- **test** — `tests/test_hook_noise_filter.py::test_a_marker_sized_value_is_not_substance`
- **runtime** — `bio_agent_os/cognitive/hooks.py:ClaudeCodeHookAdapter._projection_debt_allowed`
- **runtime** — `bio_agent_os/cognitive/hooks.py:ClaudeCodeHookAdapter._is_substantive`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`

#### ABSENCE OF OUTBOX != EVIDENCE THAT PROJECTION IS OWED · **mutant**

The substantive gate held on the write path but not on the recovery path: an event the hook deliberately declined to project had no outbox row, replay read that absence as unpaid debt, and `replay --repair` would have built `hook=SessionStart` into a memory — 5 such marker events were already carrying permanent actionable debt after the HBF-3 canary.

- **sự cố gốc** — activation/HBF3_FINDING_non_substantive_debt.md ('debt tu event sinh sau install: 5, toan bo la marker SessionStart'); closed by commit 032889e 'feat(RC-0): replay intent closure — quyet dinh luc ghi la bang chung, khong phai su vang mat'
- **test** — `tests/test_replay_intent_closure.py::test_lost_skip_row_is_reconstructed_as_skip_not_as_memory`
- **test** — `tests/test_replay_intent_closure.py::test_replay_repair_cannot_materialize_a_non_substantive_event`
- **test** — `tests/test_replay_intent_closure.py::test_r1_dropping_the_durable_skip_row_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r2_absence_read_as_owed_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_substantive_event_still_owes_and_still_gets_built`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.scan`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine._recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:no_projection_fragment`

#### INGEST-TIME NO-PROJECTION DECISION MUST SURVIVE REPLAY AND VERSION REBUILD — replay may recover the decision, replay may not reinterpret it; and the event plus its decision must be atomic · **mutant**

'Not worth a memory' is a proposition about the event, not about the projection logic version — the old latch only treated a SKIPPED row as terminal at the current version, so a v1->v2 bump rebuilt a candidate for exactly the marker that had just been refused, a second hatching of the same egg under rebuild rather than replay.

- **sự cố gốc** — commit 032889e 'feat(RC-0): replay intent closure — quyet dinh luc ghi la bang chung, khong phai su vang mat'; documented gap in activation/HBF3_FINDING_non_substantive_debt.md and docs/RC0_REPLAY_INTENT_CLOSURE.md
- **test** — `tests/test_replay_intent_closure.py::test_version_rebuild_cannot_materialize_a_skipped_event`
- **test** — `tests/test_replay_intent_closure.py::test_r3_ignoring_skip_during_version_rebuild_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_live_non_substantive_event_gets_an_explicit_durable_skip`
- **test** — `tests/test_replay_intent_closure.py::test_event_and_skip_are_atomic_at_process_level[after_event_insert]`
- **test** — `tests/test_replay_intent_closure.py::test_event_and_skip_are_atomic_at_process_level[after_outbox_insert]`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.rebuild_version`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`
- **runtime** — `bio_agent_os/cognitive/event_store.py:SQLiteEventStore.append`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:no_projection_fragment`

#### UNKNOWN INTENT MUST NOT AUTO-MATERIALIZE, AND MUST NOT BE GUESSED FROM CONTENT SHAPE · **mutant**

A pre-contract event that recorded no decision is UNKNOWN, not owed — and deciding by text shape is provably wrong: events whose whole content is `hook=SessionStart` were measured on the real store already carrying a materialized legacy memory, so a shape-based verdict would stamp a permanent false provenance claim on a row that has a real projection.

- **sự cố gốc** — commit 032889e 'feat(RC-0): replay intent closure' (R5 fixture is the measured 07/08 shape: three `hook=SessionStart` rows that did have memories); UNKNOWN semantics stated in bio_agent_os/cognitive/projection_intent.py:recorded_decision docstring
- **test** — `tests/test_replay_intent_closure.py::test_unknown_intent_is_reported_but_never_actionable`
- **test** — `tests/test_replay_intent_closure.py::test_r4_unknown_intent_assumed_owed_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r5_deciding_skip_from_marker_text_alone_must_die`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.scan`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:intent_from_payload`

#### A BUILDER MUST NOT COMMIT — the ledger row and the projection commit together as one transaction on the projection connection, one commit per job

A builder that commits on its own splits one transaction in two, and the window between them is a state where the projection exists but the ledger does not yet describe it — which is exactly the state that made the A5.4 duplicate invisible to the ledger.

- **sự cố gốc** — commit 73ebb14 'activation(A5): FAIL dung stop-condition — hook adapter double-materialize duoi outbox' ('Ledger khong cuu duoc: ban ghi thang khong di qua ledger'); contract stated in bio_agent_os/cognitive/reconciliation_worker.py:ProjectionBuilder docstring
- **test** — `tests/test_reconciliation_worker.py::test_one_commit_on_the_target_connection_per_job`
- **test** — `tests/test_reconciliation_worker.py::test_target_id_is_durable_with_the_projection`
- **test** — `tests/test_reconciliation_worker.py::test_put_commits_by_default_and_defers_when_asked`
- **test** — `tests/test_reconciliation_worker.py::test_the_ledger_is_what_makes_the_retry_safe`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:CognitiveMemoryBuilder.build`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.run_once`


### queue fairness

#### AN ABANDONED JOB MUST YIELD ITS TURN, NOT BE TERMINATED — linear-capped yield (`min(base * attempts, cap)`), no dead-letter, no quarantine, no lost work. `attempts` sizes the yield and ONLY sizes it; it decides no job's fate. · **mutant**

H1.2 measured 11 runtime signals and found 2 durable, both merely job identity: flaky infrastructure and poison payload leave the SAME trace, so terminating a job on a counter encodes a certainty the system does not have. The bake-off then measured exponential backoff at a 40-round horizon falling BELOW baseline on exactly two churn fixtures, and counter-based quarantine falsely jailing 3/8 healthy jobs.

- **sự cố gốc** — commit c18016e "feat: H1.3 fairness contract — nhuong luot, khong nhot, khong vut", built on commit 860c2d5 (H1_2_ABANDONMENT_ATTRIBUTION_AUDIT.md) and commit 05c1f2d (H1_REMEDIATION_BAKEOFF.md)
- **test** — `tests/test_fairness_contract.py::test_1_healthy_work_eventually_progresses`
- **test** — `tests/test_fairness_contract.py::test_2_no_acquisition_monopoly`
- **test** — `tests/test_fairness_contract.py::test_3_transient_crash_recovers`
- **test** — `tests/test_fairness_contract.py::test_4_no_silent_data_loss`
- **test** — `tests/test_fairness_contract.py::test_5_tenant_fairness_holds`
- **test** — `tests/test_fairness_contract.py::test_8_zero_automatic_false_quarantine`
- **test** — `tests/test_fairness_contract.py::test_9_no_operator_needed_for_healthy_progress`
- **test** — `tests/test_fairness_contract.py::test_mutant_abandoned_job_never_yields`
- **test** — `tests/test_fairness_contract.py::test_mutant_poison_monopolizes_acquisition`
- **test** — `tests/test_fairness_contract.py::test_mutant_infra_crash_auto_quarantines`
- **test** — `tests/test_queue_liveness.py::test_p4_every_job_gets_a_turn_even_with_a_poison_job`
- **test** — `tests/test_queue_liveness.py::test_a_poison_job_yields_and_healthy_work_proceeds`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox._yield_expired_leases`
- **runtime** — `bio_agent_os/cognitive/outbox.py:FAIRNESS_YIELD_BASE`
- **runtime** — `bio_agent_os/cognitive/outbox.py:FAIRNESS_YIELD_CAP`

#### FAIRNESS IS A RELATION BETWEEN JOBS — never yield into an empty queue. With nobody waiting, an expired lease is reclaimed immediately as before.

Yielding when no one is waiting only stalls the queue: nobody benefits and a lone job that hit a crash is slowed for no reason. Verified by removing only the `waiting` gate from `_yield_expired_leases`: the single-job crash-recovery case drops from one ack to zero (`thu hồi không đúng-một-lần: 0`).

- **sự cố gốc** — commit c18016e "feat: H1.3 fairness contract — nhuong luot, khong nhot, khong vut" (documented as the third of three deliberate non-actions in the `_yield_expired_leases` docstring)
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_05_crash_then_peer_reclaims_exactly_once_and_completes`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox._yield_expired_leases`


### queue liveness / counter semantics

#### `attempts` COUNTS DELIVERIES, NOT PROCESSING FAILURES — and `fail()` reads that same counter, so worker losses consume the processing-failure retry budget.

Three distinct concepts (delivery_attempts, processing_failures, worker_losses) share one column. `claim()` increments it, so a job crash-churned `max_attempts` times and then failing for the FIRST real time dead-letters immediately — the whole retry budget spent on infrastructure, not on payload. The variable name is the programmer's testimony; the execution trace is the witness.

- **sự cố gốc** — commit 9acc0e1 "test: H1 queue liveness matrix, tat dinh truoc khi ket luan"; H1_QUEUE_LIVENESS_REPORT.md "PROVEN" section
- **test** — `tests/test_queue_liveness.py::test_attempts_counts_deliveries_not_failures`
- **test** — `tests/test_queue_liveness.py::test_worker_losses_consume_the_processing_failure_budget`
- **test** — `tests/test_queue_liveness.py::test_p3_retry_after_a_real_failure_is_bounded`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.fail`


### queue liveness / drain-completion semantics

#### NOTHING CLAIMABLE NOW != THE QUEUE IS DRAINED — a live lease makes `claim()` return empty while the work is still owed, so "a cycle claimed nothing" must not be read as "the queue is empty".

`run_shadow_comparison` ends its loop when a cycle claims nothing. Zero-duration lease used to hide the gap by making the two propositions coincide as events; once a positive lease can genuinely be held, a job still owed and never built must not be reported as a clean run.

- **sự cố gốc** — commit 8f10905 "test: 59 zero-lease call-site migrate THEO Y DINH, khong search-replace" (the shadow-drain half of the lease contract, written as an open question and measured directly rather than inferred)
- **test** — `tests/test_lease_contract.py::test_shadow_drain_does_not_mistake_a_live_lease_for_an_empty_queue`
- **runtime** — `bio_agent_os/cognitive/shadow.py:ShadowReport.clean`
- **runtime** — `bio_agent_os/cognitive/shadow_runner.py:run_shadow_comparison`
- **runtime** — `bio_agent_os/cognitive/shadow_runner.py:SHADOW_LEASE_SECONDS`


### queue liveness / lease reclaim

#### NO PERMANENT LEASE — an abandoned lease must EVENTUALLY become reclaimable. "Eventually", not "immediately": immediate reclaim was itself the starvation mechanism. · **mutant**

A worker that dies holding a job must not pin it forever (mutant NEVER_RECLAIM_EXPIRED_LEASE leaves it `in_progress` for good), but the old "reclaim on the spot" contract put the abandoned job back at the head of the queue every single round — measured 12/12 deliveries to one job while two healthy jobs stayed `pending attempts=0`.

- **sự cố gốc** — H1_QUEUE_LIVENESS_REPORT.md, section "Ca quyết định P4" (12 rounds, batch_size=1, POISON attempts=12, HEALTHY_A/B attempts=0); commits 9acc0e1 and c18016e
- **test** — `tests/test_queue_liveness.py::test_p1_worker_dies_before_the_handler_runs`
- **test** — `tests/test_queue_liveness.py::test_mutant_never_reclaim_expired_lease`
- **test** — `tests/test_fairness_contract.py::test_6_no_permanent_lease`
- **test** — `tests/test_projection_safety_gate.py::test_case_09_worker_restart`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`
- **runtime** — `bio_agent_os/cognitive/outbox.py:DEFAULT_LEASE_SECONDS`


### queue liveness / operational conclusion

#### DRAIN MUST RETURN A BOUNDED OUTCOME — a queue reaching zero is not the completion gate. An unresolved job is REPORTED (`DRAIN_INCOMPLETE_UNRESOLVED` + count), not waited out. · **mutant**

`ROLLBACK_RUNBOOK.md:71` uses drain-to-zero as its completion gate, so a queue that cannot reach zero means the rollback procedure cannot close. Since a job left unresolved is better than a healthy job jailed to make the number zero, drain must state a finite conclusion instead of burning its timeout — a job held by a lease in the far future must not make drain loop forever.

- **sự cố gốc** — commit c18016e and commit 345d74b "test: khoa xfail cu vao tien de cua no, va khang dinh outcome o p4b"; H1_QUEUE_LIVENESS_REPORT.md
- **test** — `tests/test_fairness_contract.py::test_7_drain_returns_a_bounded_outcome`
- **test** — `tests/test_fairness_contract.py::test_mutant_drain_waits_forever`
- **test** — `tests/test_queue_liveness.py::test_p4b_drain_cannot_converge_with_a_poison_job`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_06_concurrent_drains_materialize_exactly_once_and_conclude`
- **runtime** — `bio_agent_os/cognitive/projection_control.py:drain`
- **runtime** — `bio_agent_os/cognitive/projection_control.py:DRAIN_INCOMPLETE_UNRESOLVED`


### queue liveness / terminal states

#### A BLOCKED JOB MUST REACH A TERMINAL STATE — and `skip()`'s return value is checked rather than thrown away a second time. · **mutant**

`reconciliation_worker` called `skip(job.job_id, "<reason>")` against the signature `skip(event_id, projection_type, *, reason)`, so the WHERE clause matched 0 rows, the return value was discarded, and `metrics.tombstoned` incremented anyway. The job reported skipped and stayed `in_progress` forever — re-leased on every expiry, `attempts` unbounded, never dead-lettered because `fail()` is not on that branch — so `drain()` could never reach zero on any store that had ever deleted anything.

- **sự cố gốc** — commit dcfc057 "fix(outbox): mot job bi chan phai den duoc trang thai ket thuc"
- **test** — `tests/test_outbox_skip_contract.py::test_a_buried_job_reaches_a_terminal_state`
- **test** — `tests/test_outbox_skip_contract.py::test_the_rollback_runbook_completion_gate_closes`
- **test** — `tests/test_outbox_skip_contract.py::test_mutant_skip_using_job_id`
- **test** — `tests/test_outbox_skip_contract.py::test_retry_semantics_are_unchanged`
- **test** — `tests/test_projection_safety_gate.py::test_case_10_buried_job_reaches_terminal_state`
- **test** — `tests/test_projection_safety_gate.py::test_case_11_rollback_drain_to_zero`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.process`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.skip`


### queue ownership / lease semantics

#### LEASE EXPIRY IS NOT AN EVENT — `lease_seconds` belongs to the READER, not to the job. There is no expiry column; `stale_before = now - lease_seconds` is recomputed by each claimer, so staleness must be configured on the DRAIN side, never on the take side.

Safety-gate cases 03 and 09 went INVALID because the fixture passed `lease_seconds=0` to the call that TOOK the lease — which changes no row at all — and then drained with a default 300 s worker, so `locked_at <= now-300` was false and the row was invisible. The worker did not refuse the job; it never saw it. The measurement was broken, not the product.

- **sự cố gốc** — commit 4e9d517 "fix(gate): 03 va 09 tu INVALID sang PASS — phep do hong, khong phai san pham hong"; PROJECTION_SAFETY_GATE_V1_REPORT.md
- **test** — `tests/test_projection_safety_gate.py::test_case_03_forget_while_worker_holds_the_lease`
- **test** — `tests/test_projection_safety_gate.py::test_case_09_worker_restart`
- **test** — `tests/test_queue_liveness.py::test_attempts_counts_deliveries_not_failures`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`
- **runtime** — `bio_agent_os/cognitive/outbox.py:OUTBOX_SCHEMA`


### queue ownership / lease validity

#### A lease must have a LENGTH. `lease_seconds` must be finite and strictly > 0 — zero is not "expires immediately", it is absence.

With `lease = 0`, `stale_before = now - lease == now` and the expiry predicate uses `<=`, so a lease taken at exactly the peer's `now` already satisfies "abandoned" — measured 5/25 double-acks at lease=0 and 0/25 at every positive lease across a 125-run two-process sweep, with the two-way causal signature `double_ack <=> locked_at_B <= stale_before_A`.

- **sự cố gốc** — commit 55cd240 "fix(outbox): mot lease phai co DO DAI — zero bi tu choi truoc moi mutation"; forensics kept at experiments/h1_4_lease_sweep.py and experiments/h1_4_process_double_claim_WIP.py, summarised in H1_4_MULTIWORKER_REPORT.md
- **test** — `tests/test_lease_contract.py::test_the_validator_is_a_single_shared_rule`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[0]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[0.0]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[-1]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[nan]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[inf]`
- **test** — `tests/test_lease_contract.py::test_a_positive_lease_is_accepted`
- **runtime** — `bio_agent_os/cognitive/outbox.py:validate_lease_seconds`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`

#### An invalid lease configuration must be rejected BEFORE any state mutation — including before the fairness yield step.

A rejected configuration that has already moved rows leaves the queue in a state nobody asked for; the validator call is deliberately the first statement in `claim()`, ahead of `_yield_expired_leases`, and the test snapshots (job_id, status, attempts, locked_by) before and after the raise.

- **sự cố gốc** — commit 55cd240 "fix(outbox): mot lease phai co DO DAI — zero bi tu choi truoc moi mutation"
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[0]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[nan]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[-0.001]`
- **test** — `tests/test_lease_contract.py::test_invalid_lease_is_rejected_before_any_state_mutation[-inf]`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`
- **runtime** — `bio_agent_os/cognitive/outbox.py:validate_lease_seconds`


### queue ownership / metric truthfulness

#### REPORTED SUCCESS != CONFIRMED TRANSITION — a transition counts as `confirmed` only when it can be READ BACK from the store; a truthy return value is `attempted`. `false_success_report_rate` must be 0. · **mutant**

Two separate incidents in this lane: (a) `metrics.claimed` counted the list AFTER the tenant filter, so a scoped worker reported `claimed=0` while another tenant's row was already leased — an absent number, not a wrong one; (b) the `:335` chain — callee reports FAIL, caller discards the return value, caller increments the success metric, system reports success — kept a broken `skip()` call alive for a whole release.

- **sự cố gốc** — commit c8453d5 (metrics.claimed counted post-filter) and commit e0bdef9 "feat(gate): Projection Safety Gate v1"; PROJECTION_SAFETY_GATE_V1_REPORT.md:195 records it as the second occurrence in the same lane
- **test** — `tests/test_projection_safety_gate.py::test_false_success_report_rate_is_zero`
- **test** — `tests/test_projection_safety_gate.py::test_mutant_ignore_callee_false_and_increment_success_metric`
- **test** — `tests/test_tenant_isolation_claim.py::test_7_claimed_counts_only_tenant_valid_claims`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:TransitionLedger.confirm`
- **runtime** — `bio_agent_os/evals/projection_safety_gate.py:TransitionLedger.false_success_report_rate`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.run_once`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.process`


### queue ownership / tenant isolation

#### ISOLATION MUST CONSTRAIN ACQUISITION, NOT MERELY PROCESSING — the tenant predicate goes into the SQL, in both the SELECT and the UPDATE guard. · **mutant**

The worker used to claim globally and drop wrong-tenant rows afterwards. Measured on a queue holding one `tenant-B` job with a worker scoped to `tenant-A`: metrics said `claimed=0 completed=0` every cycle while the store showed the tenant-B job `in_progress, locked_by=<A's worker>, attempts=1`. Releasing the row quickly cannot fix it — the lease was taken and the rightful tenant's worker was locked out for that window.

- **sự cố gốc** — commit c8453d5 "fix(isolation): tenant phai rang buoc viec GIANH, khong chi viec XU LY"
- **test** — `tests/test_tenant_isolation_claim.py::test_1_a_worker_never_touches_another_tenants_only_job`
- **test** — `tests/test_tenant_isolation_claim.py::test_2_another_tenant_at_the_head_does_not_block`
- **test** — `tests/test_tenant_isolation_claim.py::test_3_an_interleaved_queue_yields_only_this_tenant`
- **test** — `tests/test_tenant_isolation_claim.py::test_4_two_workers_never_cross_claim`
- **test** — `tests/test_tenant_isolation_claim.py::test_5_reclaim_keeps_tenant_ownership`
- **test** — `tests/test_tenant_isolation_claim.py::test_6_restart_cannot_recover_another_tenants_job`
- **test** — `tests/test_tenant_isolation_claim.py::test_mutant_claim_globally_then_filter_tenant`
- **test** — `tests/test_tenant_isolation_claim.py::test_an_unscoped_worker_still_takes_everything`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_04_concurrent_scoped_workers_never_cross_tenants`
- **test** — `tests/test_multiworker_cases_03_07.py::test_case_04_mutant_dropping_tenant_scope_crosses_the_boundary`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox.claim`
- **runtime** — `bio_agent_os/cognitive/outbox.py:ProjectionOutbox._yield_expired_leases`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.run_once`


### replay intent closure (RC-0)

#### ABSENCE OF OUTBOX != EVIDENCE THAT PROJECTION IS OWED · **mutant**

Replay read a missing outbox row as "somebody forgot to build this" and enqueued the very non-substantive marker the write-path substantive gate had refused, so `replay --repair` re-grew by the recovery path the head that had been cut on the write path.

- **sự cố gốc** — activation/HBF3_FINDING_non_substantive_debt.md — measured in the HBF-3 canary 19/08/2026: `historical actionable replay debt 0` but `debt from events created after install = 5`, all `hook=SessionStart` markers standing as actionable MISSING_OUTBOX. Closed by commit 032889e `feat(RC-0): replay intent closure — quyet dinh luc ghi la bang chung, khong phai su vang mat`.
- **test** — `tests/test_replay_intent_closure.py::test_live_non_substantive_event_gets_an_explicit_durable_skip`
- **test** — `tests/test_replay_intent_closure.py::test_replay_repair_cannot_materialize_a_non_substantive_event`
- **test** — `tests/test_replay_intent_closure.py::test_lost_skip_row_is_reconstructed_as_skip_not_as_memory`
- **test** — `tests/test_replay_intent_closure.py::test_substantive_event_still_owes_and_still_gets_built`
- **test** — `tests/test_replay_intent_closure.py::test_r1_dropping_the_durable_skip_row_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r2_absence_read_as_owed_must_die`
- **test** — `tests/test_projection_engine.py::test_an_event_with_no_recorded_intent_is_reported_but_never_owed`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.scan`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine._recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`
- **runtime** — `bio_agent_os/cognitive/event_store.py:SQLiteEventStore.append`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`
- **runtime** — `bio_agent_os/cognitive/hooks.py:ClaudeCodeHookAdapter.ingest`

#### INGEST-TIME NO-PROJECTION DECISION MUST SURVIVE REPLAY AND VERSION REBUILD · **mutant**

`rebuild_version` treated a SKIPPED row as terminal only when it matched the current projection version, so a v1→v2 bump made the old skip row stop matching and the engine generated a v2 candidate for exactly the marker it had just refused — the second egg, hatching under rebuild rather than under replay.

- **sự cố gốc** — docs/RC0_REPLAY_INTENT_CLOSURE.md, section "Bệnh": "Chủ bắt thêm tầng thứ hai: `rebuild_version` chỉ coi `SKIPPED` là terminal khi nó nằm đúng version hiện tại. Bump v1→v2 là hàng cũ hết khớp và engine dựng candidate v2 cho chính cái marker vừa từ chối"; commit 032889e.
- **test** — `tests/test_replay_intent_closure.py::test_version_rebuild_cannot_materialize_a_skipped_event`
- **test** — `tests/test_replay_intent_closure.py::test_r3_ignoring_skip_during_version_rebuild_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r1_dropping_the_durable_skip_row_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_live_non_substantive_event_gets_an_explicit_durable_skip`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.rebuild_version`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:NO_PROJECTION`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:no_projection_fragment`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:intent_from_payload`
- **runtime** — `bio_agent_os/cognitive/event_store.py:SQLiteEventStore.append`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`

#### UNKNOWN INTENT MUST NOT AUTO-MATERIALIZE — không có hàng nợ và event không ghi quyết định nào → UNKNOWN_INTENT: báo cáo, KHÔNG hành động · **mutant**

A pre-contract event records no decision either way, so treating its silence as debt builds a memory from a row nobody ever said anything about; the five surviving markers on the real store are held as UNKNOWN with actionable = 0 rather than backfilled.

- **sự cố gốc** — docs/RC0_REPLAY_INTENT_CLOSURE.md, section "Store thật": "marker cũ (trước RC-0) 5 event → UNKNOWN_INTENT, actionable = 0 — KHÔNG backfill thành SKIPPED — làm vậy chính là đoán theo hình dạng chữ, đúng mutant R5"; commit 032889e.
- **test** — `tests/test_replay_intent_closure.py::test_unknown_intent_is_reported_but_never_actionable`
- **test** — `tests/test_projection_engine.py::test_an_event_with_no_recorded_intent_is_reported_but_never_owed`
- **test** — `tests/test_replay_intent_closure.py::test_r4_unknown_intent_assumed_owed_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r5_deciding_skip_from_marker_text_alone_must_die`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ReplayReason.UNKNOWN_INTENT`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ReplayCandidate.actionable`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.replay`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.scan`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`

#### WRITE-TIME DECISION must become durable evidence — REPLAY MAY RECOVER IT, REPLAY MAY NOT REINTERPRET IT (a lost skip row is restored as the DECISION, never as a memory, and the decision is never inferred from content shape) · **mutant**

Any rule that reads "skip" off the text would stamp "owes nothing" onto rows that already carry a real projection — measured on the real store, three `hook=SessionStart`-shaped rows of 07/08 have live memories — and that would be a permanently false statement about provenance.

- **sự cố gốc** — Docstring of bio_agent_os/cognitive/projection_intent.py:recorded_decision — "UNKNOWN KHÔNG được đoán theo hình dạng nội dung: một event trông như marker vẫn có thể đã có ký ức thật (đo được: 3 hàng `hook=SessionStart` của 07/08)"; docs/RC0_REPLAY_INTENT_CLOSURE.md knife R5; commit 032889e.
- **test** — `tests/test_replay_intent_closure.py::test_lost_skip_row_is_reconstructed_as_skip_not_as_memory`
- **test** — `tests/test_replay_intent_closure.py::test_r5_deciding_skip_from_marker_text_alone_must_die`
- **test** — `tests/test_replay_intent_closure.py::test_r1_dropping_the_durable_skip_row_must_die`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine._reconstruct_skip`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ReplayReason.SKIP_ROW_LOST`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine._recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:recorded_decision`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:intent_from_payload`

#### Hoặc cả event lẫn quyết định cùng bền, hoặc không cái nào — the event and the record of what it owes (or explicitly does not owe) commit in one transaction; no (1 event, 0 decision) hybrid may survive a crash

An event that is durable without its projection decision is precisely the gap replay misreads as debt, so a crash between the two writes would manufacture the very ambiguity RC-0 exists to remove.

- **sự cố gốc** — Commit 032889e body: "Atomicity kiem cap process tai after_event_insert va after_outbox_insert: chi (0,0) hoac (1,1)"; docs/RC0_REPLAY_INTENT_CLOSURE.md, "Đo" table row `atomicity`.
- **test** — `tests/test_replay_intent_closure.py::test_event_and_skip_are_atomic_at_process_level[after_event_insert]`
- **test** — `tests/test_replay_intent_closure.py::test_event_and_skip_are_atomic_at_process_level[after_outbox_insert]`
- **runtime** — `bio_agent_os/cognitive/event_store.py:SQLiteEventStore.append`
- **runtime** — `bio_agent_os/cognitive/fault_points.py:ProjectionFaultPoint`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`

#### Bia mộ có HAI cổng độc lập — a tombstone is enforced twice, at the replay engine and again at the worker; one broken defence layer must not be enough to resurrect a forgotten memory · **mutant**

RC-0 gave the forget probe a real consumer to drain, which exposed that "A did not come back" had two independent causes; the property was holding by luck until it was written down and measured — disabling the engine gate resets all 5 jobs and the worker still refuses to build.

- **sự cố gốc** — docs/RC0_REPLAY_INTENT_CLOSURE.md, section "Hệ quả ngoài dự kiến — một xfail hết hạn": "bia mộ có HAI cổng độc lập — tắt cổng ở `projection_engine` thì `ReconciliationWorker.process` vẫn từ chối dựng. Mutant phải tắt cả hai mới hồi sinh được"; commit 032889e.
- **test** — `tests/test_forget_resurrection.py::test_replay_gate_is_not_the_only_guard`
- **test** — `tests/test_forget_resurrection.py::test_the_mutant_resurrects_and_this_test_must_catch_it`
- **test** — `tests/test_forget_resurrection.py::test_forgotten_evidence_stays_gone_through_replay_and_rebuild`
- **runtime** — `bio_agent_os/cognitive/projection_engine.py:ProjectionReplayEngine.replay`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.process`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried`
- **runtime** — `bio_agent_os/cognitive/tombstones.py:buried_among`

#### DECLARED CONSUMER != EXECUTED MATERIALIZATION — a probe that only enqueues has not measured resurrection; "không có gì quay lại" and "không có gì được dựng lại" produce the same number · **mutant**

Two forget-resurrection tests carried `xfail(strict=True)` on the premise that nothing in bio_agent_os materialised the outbox, so they were observing a mechanism that had never run; RC-0 made them XPASS and they were rewritten to replay and then drain with the product's own worker, asserting the mechanism actually built something.

- **sự cố gốc** — docs/RC0_REPLAY_INTENT_CLOSURE.md, section "Hệ quả ngoài dự kiến — một xfail hết hạn"; the retired `_NO_MATERIALISER` marker text kept as a record in tests/test_forget_resurrection.py ("replay() chỉ enqueue; không có consumer nào ... OPEN, không phải PASS"); commit 032889e.
- **test** — `tests/test_forget_resurrection.py::test_forgotten_evidence_stays_gone_through_replay_and_rebuild`
- **test** — `tests/test_forget_resurrection.py::test_the_mutant_resurrects_and_this_test_must_catch_it`


### write semantic parity

#### CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT · **mutant**

The same prompt through both production paths produced byte-identical content and materially different memories: confidence 0.72->0.5, importance 0.55->0.5, utility 0.65->0.5, metadata.state (mode/stress_state, which retrieval reads directly) missing — and 8 REAL_USER_WRITE rows were found alive on the real store with those degraded semantics.

- **sự cố gốc** — commit bac0f96 'forensic(SP-0): SEMANTIC PARITY FAIL — builder outbox danh roi call-site semantics'; artifact activation/SP0/sp0_report.json (field_diff + managed_audit listing REAL_USER_WRITE deltas 'confidence=0.5 (legacy=0.72)', 'metadata.state THIEU')
- **test** — `tests/test_semantic_parity.py::test_same_event_two_writers_produce_identical_semantics`
- **test** — `tests/test_semantic_parity.py::test_mutant_dropping_intent_breaks_parity`
- **test** — `tests/test_projection_equivalence.py::test_degraded_to_content_only_must_die`
- **test** — `tests/test_projection_equivalence.py::test_m2_mutant_content_identical_but_full_field_altered_must_die`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:build_memory_from_event`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:CognitiveMemoryBuilder.build`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.remember`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:compare`

#### EXACTLY-ONCE EXECUTION != SEMANTIC PARITY · **mutant**

The A5-v2 / A5-v2.1 windows had already certified exactly-once (1 job completed, 1 ledger row, 0 duplicates, 0 unresolved) over precisely the writes SP-0 later found semantically degraded — counting executions says nothing about what was written.

- **sự cố gốc** — commit bac0f96 'forensic(SP-0): SEMANTIC PARITY FAIL' audited 17 ALREADY_MANAGED rows produced inside the exactly-once-certified A5-v2/A5-v2.1 window (commits 9550c46, 62bcac1) and found 8 real user writes degraded; law text carried in bio_agent_os/cognitive/projection_intent.py module docstring
- **test** — `tests/test_semantic_parity.py::test_retrieval_scores_identical_across_writers`
- **test** — `tests/test_semantic_parity.py::test_same_event_two_writers_produce_identical_semantics`
- **test** — `tests/test_semantic_parity.py::test_mutant_dropping_intent_breaks_parity`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:MemoryProjectionIntent`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:build_memory_from_event`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:CognitiveMemoryBuilder.build`

#### THE WRITE CONTRACT LIVES IN THE IMMUTABLE EVENT PAYLOAD AND EVERY WRITER GOES THROUGH EXACTLY ONE CONSTRUCTOR — event.metadata is not strong enough because it is not under the checksum, and a builder that re-derives hook semantics drifts the moment the call site changes a value · **mutant**

SP-0's regression was a second, silent semantic mapping inside CognitiveMemoryBuilder; fixing it by teaching the builder to re-infer hook semantics would have re-introduced the same drift on the next call-site edit, so the intent is frozen at write time into event.payload['projection_intents'] and both writers call build_memory_from_event.

- **sự cố gốc** — commit 0498467 'fix(SP-1/SP-2): persist the write contract — mot constructor, hai writer, va repair 9 ky uc that' (SP-2 repair artifact activation/SP2/sp2_report.json)
- **test** — `tests/test_semantic_parity.py::test_same_event_two_writers_produce_identical_semantics`
- **test** — `tests/test_semantic_parity.py::test_mutant_dropping_intent_breaks_parity`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:build_memory_from_event`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:intent_from_payload`
- **runtime** — `bio_agent_os/cognitive/projection_intent.py:MemoryProjectionIntent.as_payload_fragment`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.observe`
- **runtime** — `bio_agent_os/cognitive/hooks.py:ClaudeCodeHookAdapter.ingest`

#### Proof names must be weaker-or-equal to what measurement proves — a comparator that calls itself FULL must measure the full field set, and a contract it cannot reconstruct must declare a weaker proof name · **mutant**

The first version of the equivalence comparator was named FULL while measuring roughly six fields — the same false-green shape as SP-0 — and would have adopted 240 historical rows on a label it had not earned; tightening it dropped the adoptable count to 203, which was the good news.

- **sự cố gốc** — commit c15f3d8 'law(HBF-1.1): chu FULL gio co rang that — 240 xuong 203, va do la tin tot' (law text carried in bio_agent_os/cognitive/projection_equivalence.py module docstring)
- **test** — `tests/test_projection_equivalence.py::test_full_contract_passes_and_counts_fields`
- **test** — `tests/test_projection_equivalence.py::test_curated_proof_is_declared_weaker`
- **test** — `tests/test_projection_equivalence.py::test_m2_mutant_content_identical_but_full_field_altered_must_die`
- **test** — `tests/test_projection_equivalence.py::test_unknown_writer_never_adopts`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:compare`
- **runtime** — `bio_agent_os/cognitive/projection_equivalence.py:hook_contract`

#### SP-1B SIDE-EFFECT PARITY — the outbox path must reproduce remember()'s side effects after the ledger+memory commit, in legacy order: put -> world_model.ingest -> context invalidate · **mutant**

Column-level parity is not the whole write: a memory materialized by the worker that never reaches the world model or never invalidates cached context blocks is a second, invisible divergence between the two writers.

- **sự cố gốc** — commit 0498467 'fix(SP-1/SP-2): persist the write contract' — section 'SP-1B — SIDE-EFFECT PARITY: builder.after_commit() chay SAU commit cua cap ledger+memory, dung thu tu legacy'; re-qualified in commit ba9c9b0 'requalify: ladder + deployed window PASS sau SP-1B'
- **test** — `tests/test_side_effect_parity.py::test_outbox_path_reproduces_the_same_side_effects_as_remember`
- **test** — `tests/test_side_effect_parity.py::test_side_effects_run_after_the_row_is_durable`
- **test** — `tests/test_side_effect_parity.py::test_mutant_dropping_after_commit_must_die`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:CognitiveMemoryBuilder.after_commit`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:ReconciliationWorker.run_once`
- **runtime** — `bio_agent_os/cognitive/reconciliation_worker.py:worker_for`
- **runtime** — `bio_agent_os/cognitive/facade.py:MemoryOS.remember`

<!-- LAWS:END -->

---

## 3. Timeline sự cố — giữ cả thất bại, không chỉ thành công

Mỗi mục dưới đây là một lần hệ thống, hoặc phép đo về hệ thống, **đã sai**.
Chúng ở lại vì bài học nằm trong hình dạng của cái sai, không nằm trong bản vá.

### 17/08 — P0: quên một ký ức còn đang trên đường ống
`1fd809a` · Ký ức mới chỉ tồn tại ở event/outbox thì `forget` kết luận "không
có gì để quên", worker chạy sau và nội dung materialize trở lại.
→ luật **DELETE OF CURRENT ≠ REVOCATION OF FUTURE**.

### 18/08 — H1: hàng đợi có thể sống mà vẫn đói
`9acc0e1` → `a2ea71b` · 12 vòng giao việc đều rơi vào một job độc, hai job
lành phía sau **chưa từng tới lượt**. Không chậm — không bao giờ được gọi.
Sửa bằng fairness yield có chốt, rồi mở rộng lên multi-worker với 7 ca + mutant.

### 18/08 — một quan sát cũ phá một lease đang sống
`70c469e` · `_yield_expired_leases` UPDATE vô điều kiện, xoá quyền sở hữu của
worker khác. Vá bằng compare-and-set; mutant `YIELD_IGNORES_CURRENT_CLAIM_STATE`
canh vĩnh viễn.

### 18/08 — zero-duration lease
`55cd240`, `8f10905` · `lease_seconds=0` phá quyền sở hữu độc quyền vì
`locked_at <= stale_before` với hai mốc thời gian bằng nhau (đo: 199986/200000
lần đọc `time.time()` liên tiếp cho giá trị y hệt). Không chọn "đồng hồ tối
thiểu" — từ chối thẳng lease không có độ dài. 59 call-site migrate **theo ý
định**, không search-replace.

### 18/08 — A5 FAIL đúng stop-condition: hook ghi hai lần
`73ebb14` · Dưới OUTBOX, adapter gọi cả `observe()` lẫn `remember()` → 2 ký ức
cho một prompt, và nó **đã dính vào một prompt thật của chủ**. Sửa bằng
single-writer theo mode `3baa422`.

### 18/08 — chính quy trình rollback làm hỏng store
`f1e4de7`, `60bdb2e`, `06f7049` · 92 phát hiện integrity trên store thật, gây
ra bởi `copy2` đè lên một DB đang có handle sống. Giả thuyết đầu (WAL frames)
**bị bác** 0/5; cơ chế thật (page cache của handle sống) VERIFIED 5/5 bằng
control trials. → **HAVING A ROLLBACK POINT ≠ SAFE LIVE MUTATION**, và
generation replacement thay cho in-place restoration.

### 19/08 — SP-0: đổi đường ghi làm nghèo ngữ nghĩa
`bac0f96` → `0498467` · Builder outbox dựng memory bằng default model (0.5)
thay vì call-site contract (0.72/0.55/0.65), `metadata.state` biến mất.
Retrieval score lệch **3.330 vs 3.172** — product-visible. → **CONTENT_EQUIVALENT
≠ PROJECTION_EQUIVALENT**; hợp đồng ghi được lưu bền trong event, một
constructor cho cả hai writer.

### 19/08 — SP-2: sửa đúng sản phẩm, sai thủ tục
`0498467` · Script repair mở write connection vào store thật rồi UPDATE tại
chỗ, thay vì candidate offline → certify → install. Sản phẩm đúng, **thủ tục
FAIL**, ghi sổ thành luật thay vì rollback một store đã chứng minh sạch.

### 19/08 — HBF-1.1: chữ FULL không có răng
`c15f3d8` · Comparator tự gọi mình là FULL nhưng đo 6 trường. Bản đo ≥20
trường kéo 240 xuống **203** và lộ 46 hàng drift `observed_at`. → **tên proof
phải yếu hơn hoặc bằng thứ measurement thực sự chứng minh**.

### 19/08 — HBF-2.1: provenance nhét nhầm nhà
`1e20670` · `locked_by` bị dùng làm túi đựng provenance (303 "worker ma" sống
vĩnh viễn trong query ownership), và audit khai `builder_version_checked=1`
cho cả curated — một comparison chưa từng xảy ra. → **NOT_APPLICABLE phải
phân biệt được với version 1**.

### 19/08 — HBF-3: bốn lỗi ĐO trong pha hậu-install
`2cc4489` · Closure cấm nhầm ledger cũ của bia mộ · nợ replay đo toàn cục mà
gán nhãn "lịch sử" · needle dùng chung cho hai ký ức · so hash file với
candidate sau khi canary đã ghi hợp lệ. Không lỗi nào là lỗi dữ liệu; tất cả
là phép đo sai tập hợp. Canonical vẫn chỉ chịu **đúng một** lần thay generation.

Và trước khi bấm nút: review đối kháng 4 lăng kính bắt được
`install_generation` chỉ fail-closed **nửa đường** — hỏng sau move-aside thì
canonical đã nằm trong quarantine mà caller đọc thành "chưa install", rồi
**bật lại hook vào một store không tồn tại**.

### 19/08 — RC-0: con rồng mọc đầu bằng đường phục hồi
`032889e` · Substantive gate cắt đúng ở đường ghi nhưng thể hiện quyết định
bằng **sự vắng mặt**; replay đọc hàng vắng thành "chắc ai quên build" và dựng
lại chính marker vừa bị từ chối. Chủ bắt thêm tầng hai: `rebuild_version` chỉ
coi SKIPPED là terminal khi khớp version hiện tại. → hai lớp bằng chứng độc
lập, và **UNKNOWN INTENT MUST NOT AUTO-MATERIALIZE**.

### Xuyên suốt — lớp lỗi hay tái phát nhất không nằm ở sản phẩm
Vị từ đúng, **tập hợp sai**. `all()` trên tập rỗng · "còn đang quarantine" vs
"đã từng quarantine" · cửa sổ độc quyền tính cả vòng không tranh chấp · probe
đo clone khác với clone bị tác động · marker tìm trong trường mà renderer
không ghi · đếm tổng thay vì đếm delta. Sáu lần, sáu hình dạng khác nhau, cùng
một họ.

---

## 3b. Lỗ hổng đã khai — và đã trả (19/08, cổng vào LS-0)

Baseline này từng khai hai món nợ bằng chứng. Cổng vào LS-0 buộc trả trước khi
đo lifespan, với luật rõ: **test xanh → khoá regression; test đỏ → dừng LS-0,
mở lại đúng subsystem Core Integrity. Không sửa product chỉ để test xanh.**

Cả hai **xanh ngay lần đầu, và không một dòng product nào bị chạm**
(`git status bio_agent_os/` rỗng). Nghĩa là hai luật ấy vốn đã đúng trong
code; thứ thiếu là hàng rào, không phải hành vi.

```
SP-1B side-effect parity      3 test · mutant bỏ after_commit → chết
                              đo PARITY thật: cùng input, hai đường ghi,
                              cùng dấu vết (ingest → invalidate_scope),
                              và chạy SAU commit — kiểm bằng một
                              connection ĐỘC LẬP, vì hỏi chính connection
                              đang ghi thì lúc nào cũng thấy
DEPLOYED CONFIG AUTHORITY     4 test · mutant hard-code mode → chết
                              khoá chuỗi settings.json → env → mode →
                              hành vi ghi, kèm ca "env của phiên KHÔNG
                              được thắng file"
```

**Phần vẫn không đo được, ghi thẳng:** việc Claude Code có thực sự đọc
`.claude/settings.json` hay không nằm ngoài repo này. Chuỗi từ file trở đi đã
có hàng rào; mắt xích đầu vẫn chỉ có nhân chứng deployment. Không gộp thành
"VERIFIED" trơn.

Và một ghi chú về chính mutant: bản đầu của nó vá `shadow.current_mode` rồi
"chết" — nhưng chết vì **nhắm trượt**, không vì sản phẩm an toàn. `facade` làm
`from .shadow import current_mode` nên tên đã bind lúc import. Bản thứ hai vá
qua `sitecustomize` cũng im lặng không chạy, vì `site` nạp nó trước khi
editable-install kịp vào `sys.path`. Chỉ đến wrapper tường minh mutant mới cắn.
Cùng một họ với chuyện verifier nuốt BOM: **instrument mù là một lớp hỏng
riêng**, và nó đã xuất hiện lần thứ hai trong đúng một ngày.

## 3c. Ghi chú lịch sử — hai nợ này từng được khai thế nào

Đóng hồ sơ mà giấu một chỗ thiếu thì hồ sơ đó vô giá trị. Bộ kiểm in nó ra mỗi
lần chạy, và nó được ghi thẳng vào manifest ở trường `gap`.

### SP-1B SIDE-EFFECT PARITY — chưa có regression test

**Luật:** đường outbox phải tái tạo đúng các side-effect của `remember()` —
`world_model.ingest` rồi `context_blocks.invalidate_scope`, chạy SAU commit,
đúng thứ tự của đường legacy.

**Runtime:** `reconciliation_worker.py:CognitiveMemoryBuilder.after_commit`
(tồn tại, đang chạy).

**Vấn đề:** không một test nào drain worker rồi kiểm hai side-effect đó. Toàn
suite không có file nào tham chiếu `after_commit` hay `invalidate_scope`. Luật
này hiện chỉ được chứng bởi **một lần chạy activation** (requalify `ba9c9b0`)
— một nhân chứng, không phải một hàng rào.

**Vì sao không vá trong lane này:** viết test bây giờ hoặc là mở rộng phạm vi
của một lane đông lạnh (nếu xanh), hoặc là phát hiện một defect cần sửa runtime
(nếu đỏ) — mà lane này đã khoá `RUNTIME SEMANTICS DELTA = 0`. Nên nó được ghi
sổ, không bị làm mờ.

**Việc phải làm ở lane sau:** một test drain-rồi-kiểm cho cả hai side-effect,
kèm mutant bỏ `after_commit` để chứng minh test có răng.

### Coverage một phần — DEPLOYED CONFIG AUTHORITY

Nửa `env → mode` có regression test thật (`test_shadow_mode.py`). Nửa
`settings.json → env` chỉ có nhân chứng một lần
(`activation/A5v2_1_deployed_config_witness.py`). Ghi đúng như vậy trong
manifest ở trường `partial_coverage`, không gộp thành "VERIFIED" trơn.

---

## 4. Baseline đông lạnh

```
full suite (clean env)        1335 passed · 3 skipped · 8 xfailed · 0 failed
store thật                    integrity ok · fk 0
migration invariants          9/9 = 0 · closure 307/307
historical actionable debt    0
live marker debt              0
legacy UNKNOWN_INTENT         5  (báo cáo, non-actionable — KHÔNG backfill:
                                 backfill chính là mutant R5)
projection mode               OUTBOX, deployed-config authority VERIFIED
multi-node                    NOT CERTIFIED
```

Điểm đáng giá của baseline này không phải con số 1335. Nó là chuỗi bất biến ở
mục 1 nối kín được qua sáu loại biến cố.

---

## 5. PUBLIC FRONTIER CUTOFF

Lane này **không đổi LICENSE**, không xoá và không viết lại một dòng lịch sử
public nào. Nó chỉ ghi lại ranh giới kiến trúc tại thời điểm đóng Core
Integrity.

### PUBLIC / VERIFIABLE CORE

- Giao thức event ↔ projection (outbox, ledger, projection key, contract version)
- Bất biến về sự thật, lịch sử và quên (tombstone, no-resurrection, temporal validity)
- Runtime tham chiếu single-host
- An toàn replay/reconciliation (bao gồm RC-0: replay intent closure)
- Các hợp đồng benchmark và verification **đã public từ trước**

Phần này tồn tại để người khác kiểm chứng được, và để giao thức có thể được
dùng lại. Nó là thứ đáng để mở.

### PRIVATE FRONTIER

- Experience Plane
- Reflection Candidate Engine
- Learning Lifecycle
- Credit assignment
- Learned strategy adaptation
- Constitutional meta-learning
- Self-evolution
- Logic thích ứng theo khách hàng / dữ liệu riêng
- Tối ưu phân tán trong tương lai **chưa từng public**

Ranh giới đặt đúng chỗ này vì phần public kết thúc ở *"trí nhớ này có đúng
không"* — một câu hỏi nên kiểm chứng được công khai. Phần frontier bắt đầu ở
*"nó học được gì và trở nên tốt hơn thế nào"* — nơi tạo lợi thế dài hạn.

**Rà soát pháp lý/licensing là một hành động riêng trong tương lai**, không
thuộc lane này.

---

## 6. Cửa tiếp theo

```
CORE INTEGRITY (single host)   CLOSED
        ↓
LIFESPAN SUBSTRATE             chưa mở
   lane đầu tiên dự kiến: LS-0 Lifespan Measurement Constitution
```

Chưa compaction, chưa tối ưu gì cả. Trước hết phải định nghĩa *"sống tốt hơn
qua thời gian"* được đo bằng gì, và ràng nó với:

```
LIFESPAN IMPROVEMENT  +  NO TRUTH REGRESSION
```

Thiếu Measurement Constitution thì rất dễ "tối ưu lifespan" bằng cách xoá và
merge mạnh tay rồi mừng vì database nhỏ đi. Một cây bonsai cũng nhỏ, nhưng nhỏ
không có nghĩa là nó nhớ mùa đông năm ngoái.

**KHÔNG mở tự động:** Multi-node · Lifespan · Experience · Reflection · Learning.
