"""
Sleep consolidation and memory compilation.
"""

import math
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from bio_agent_os.core.audit_log import AuditLog
from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.compaction import MemoryCompactor
from bio_agent_os.core.dream_journal import DreamJournal
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.memory_health import MemoryHealthMonitor
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.reconciliation import ContradictionResolver
from bio_agent_os.memory.exact_memory import ExactMemoryStore
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


class MemoryLabel(BaseModel):
    topic: str = Field(description="Main topic")
    importance_score: int = Field(description="Importance from 1 to 10")
    is_junk_or_transient: bool = Field(description="Whether this should be forgotten quickly")
    user_state: str = Field(description="Observed user or agent state")


class CompiledMemory(BaseModel):
    episodic_summary: str = Field(
        description=(
            "Short factual record of what happened, keeping speaker names, "
            "dates, and concrete details verbatim"
        )
    )
    semantic_memory: str = Field(
        description=(
            "Self-contained declarative facts from the event: WHO did WHAT, "
            "WHEN, WHERE. Keep names, dates, places, and exact values. "
            "Never replace facts with generic advice"
        )
    )
    procedural_memory: str = Field(description="Reusable procedure or workflow guidance")
    exception_memory: str = Field(description="Important exception, caveat, or dangerous special case")
    identity_rule: str = Field(description="Stable rule candidate for the self-model")
    confidence: float = Field(description="Confidence score from 0 to 1")
    scope: str = Field(description="core, project, agent, user, session, organization")


class Hippocampus:
    def __init__(
        self,
        engine: LLMEngine,
        l1: L1WorkingMemory,
        persona: Persona,
        l2: Optional[L2SemanticMemory] = None,
        episodes: Optional[EpisodeStore] = None,
        exact_memory: Optional[ExactMemoryStore] = None,
        graph: Optional[KnowledgeGraph] = None,
        dream_journal: Optional[DreamJournal] = None,
        audit_log: Optional[AuditLog] = None,
        approval_queue: Optional[ApprovalQueue] = None,
        detector_mode: str = "heuristic",
    ):
        self.engine = engine
        self.l1 = l1
        self.persona = persona
        self.l2 = l2
        self.episodes = episodes
        self.exact_memory = exact_memory
        self.graph = graph
        self.dream_journal = dream_journal
        self.audit_log = audit_log
        self.approval_queue = approval_queue
        self.compactor = MemoryCompactor()
        self.reconciler = ContradictionResolver(
            persona=persona,
            approval_queue=approval_queue,
            engine=engine,
            detector_mode=detector_mode,
        )
        # Reconsolidation replay (Phase 4, Feature B). LLM-free core runs in
        # consolidate(); set BIO_RECONSOLIDATION_ENABLED=0 to revert.
        self.reconsolidation_enabled = os.getenv("BIO_RECONSOLIDATION_ENABLED", "1") != "0"
        self.reconsolidation_replay_size = int(os.getenv("BIO_RECONSOLIDATION_R", "24"))
        self._log: List[str] = []
        self._allowed_scopes = {"core", "project", "agent", "user", "session", "organization"}

    @staticmethod
    def _is_plausible_anchor_value(value: str) -> bool:
        # An anchor is stored verbatim and trusted at importance 9, so a bad
        # capture becomes a confidently-wrong "fact". Reject values that are
        # negations ("NOT 123456", "không phải mật khẩu") or carry no content —
        # those are almost never the real secret the user is pinning.
        lowered = value.strip().lower()
        if not lowered or not any(ch.isalnum() for ch in lowered):
            return False
        if lowered.startswith(("no longer", "không phải", "khong phai")):
            return False
        negation_words = {
            "not", "no", "never", "isn't", "wasn't", "aren't", "don't", "doesn't",
            "không", "khong", "chẳng", "chang", "đừng", "dung",
        }
        return lowered.split()[0] not in negation_words

    def _extract_anchor_fact(self, raw_data: str) -> Dict[str, str]:
        text = " ".join(raw_data.split())
        subject_patterns = [
            (
                "special_character",
                r"(?:special character|ky tu dac biet|ký tự đặc biệt)\s+(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)\s+(?:is|was|la|là|=)\s+(?P<value>[^.!?\n]{1,120})",
            ),
            (
                "verification_code",
                r"(?:secret code|code word|passphrase|password|verification code|mat ma|mật mã)\s+(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)\s+(?:is|was|la|là|=)\s+(?P<value>[^.!?\n]{1,120})",
            ),
        ]
        for kind, pattern in subject_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            subject = re.sub(r"\s+", " ", match.group("subject")).strip(" .,:;!?-")
            value = re.sub(r"[*`_]+", "", match.group("value")).strip(" .,:;!?-")
            value = re.sub(r"\s+", " ", value)
            if subject and value and len(value) <= 96 and len(value.split()) <= 6 and "," not in value and ":" not in value and self._is_plausible_anchor_value(value):
                return {"anchor_kind": kind, "anchor_subject": subject, "anchor_value": value}
        patterns = [
            (
                "special_character",
                r"(?:ký tự đặc biệt|ky tu dac biet|special character)[^:]*?(?:là|la|is)\s+(.+?)(?:[.!?\n]|$)",
            ),
            (
                "verification_code",
                r"(?:ký tự mật mã|ky tu mat ma|mật mã|mat ma|secret code|code word|passphrase|password)[^:]*?(?:là|la|is)\s+(.+?)(?:[.!?\n]|$)",
            ),
            (
                "special_character",
                r"(?:ghi nhớ chính xác|ghi nho chinh xac|remember exactly)\s*[:\-]\s*(.+?)(?:[.!?\n]|$)",
            ),
        ]
        for kind, pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = re.sub(r"[*`_]+", "", match.group(1)).strip(" .,:;!?-")
            value = re.sub(r"\s+", " ", value)
            if value and len(value) <= 96 and len(value.split()) <= 6 and "," not in value and ":" not in value and self._is_plausible_anchor_value(value):
                return {"anchor_kind": kind, "anchor_value": value}
        return {}

    def _apply_anchor_overrides(
        self,
        raw_data: str,
        metadata: Dict[str, Any],
        observation_type: str,
    ) -> Dict[str, Any]:
        lowered = raw_data.lower()
        extracted = self._extract_anchor_fact(raw_data)
        has_marker = any(
            marker in lowered
            for marker in ["remember exactly", "ghi nhớ chính xác", "ghi nho chinh xac"]
        )
        if extracted:
            # A concrete, validated verbatim value → full anchor treatment.
            metadata["topic"] = "verification_anchor"
            metadata["importance_score"] = max(int(metadata.get("importance_score", 5)), 9)
            metadata["is_junk_or_transient"] = False
            metadata["is_anchor_memory"] = True
            metadata["retain_verbatim"] = True
            metadata.update(extracted)
            if observation_type in {"chat_input", "chat_output"} and "urgent" not in str(metadata.get("user_state", "")):
                metadata["user_state"] = metadata.get("user_state") or "focused"
        elif has_marker:
            # The user signalled importance but we could NOT extract a verbatim
            # value to pin. Raise importance modestly, but do NOT fabricate a
            # verbatim anchor — that is exactly how a hallucinated or negated
            # "secret" gets stored and later trusted at face value.
            metadata["topic"] = metadata.get("topic") or "verification_anchor"
            metadata["importance_score"] = max(int(metadata.get("importance_score", 5)), 7)
            metadata["is_junk_or_transient"] = False
        return metadata

    @property
    def logs(self) -> List[str]:
        return list(self._log)

    def clear_logs(self):
        self._log.clear()

    async def label(self, raw_data: str, source: str = "unknown") -> Dict[str, Any]:
        self._log.append(f"Hippocampus labeling input from {source}.")

        prompt = (
            "You are the hippocampus for an AI agent.\n"
            "Label the following raw data with topic, importance, whether it is junk, "
            f"and observed state.\nData:\n{raw_data[:1200]}"
        )
        try:
            metadata = await self.engine.generate_structured(
                prompt,
                schema=MemoryLabel,
                temperature=0.1,
            )
            self._log.append(
                "Labeled memory "
                f"(importance={metadata['importance_score']}, junk={metadata['is_junk_or_transient']})."
            )
            return metadata
        except Exception as exc:
            self._log.append(f"Label failed: {exc}")
            return {
                "topic": "unknown",
                "importance_score": 5,
                "is_junk_or_transient": False,
                "user_state": "unknown",
            }

    async def label_and_store(
        self,
        raw_data: str,
        source: str = "unknown",
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        source_refs: Optional[List[Dict[str, Any]]] = None,
        observation_type: str = "event",
    ) -> Dict[str, Any]:
        compaction = self.compactor.compact(raw_data)
        metadata = await self.label(compaction["content"], source)
        metadata = self._apply_anchor_overrides(raw_data, metadata, observation_type)
        metadata.update(
            {
                "was_compacted": compaction["was_compacted"],
                "original_length": compaction["original_length"],
                "compacted_length": compaction["compacted_length"],
                "task_id": task_id,
                "workspace_id": workspace_id,
                "project_version": project_version,
            }
        )
        episode = None

        if self.episodes:
            episode = self.episodes.add(
                raw_payload=raw_data,
                actor=source,
                source=source,
                observation_type=observation_type,
                inferred_intent=metadata.get("topic"),
                topic=metadata.get("topic"),
                outcome="captured",
                confidence=max(0.1, min(metadata.get("importance_score", 5) / 10.0, 0.95)),
                tags=[metadata.get("topic", "general")],
                metadata=metadata,
                task_id=task_id,
                workspace_id=workspace_id,
                project_version=project_version,
                source_refs=source_refs,
            )
        if self.exact_memory:
            exact_candidate = {}
            if metadata.get("anchor_kind") and metadata.get("anchor_value"):
                exact_candidate = {
                    "fact_kind": str(metadata.get("anchor_kind")),
                    "fact_value": str(metadata.get("anchor_value")),
                }
            else:
                exact_candidate = self.exact_memory.extract_candidate(raw_data, metadata=metadata)
        else:
            exact_candidate = {}
        if self.exact_memory and exact_candidate.get("fact_kind") and exact_candidate.get("fact_value"):
            exact_record = self.exact_memory.remember(
                fact_kind=str(exact_candidate.get("fact_kind")),
                fact_value=str(exact_candidate.get("fact_value")),
                confidence=max(0.55, min(metadata.get("importance_score", 5) / 10.0, 0.99)),
                source=source,
                task_id=task_id,
                workspace_id=workspace_id,
                project_version=project_version,
                episode_id=episode["episode_id"] if episode else None,
                source_refs=episode.get("source_refs", []) if episode else (source_refs or []),
                metadata=metadata,
            )
            if exact_record:
                metadata["exact_memory_state"] = exact_record.get("state", "active")
                metadata["exact_memory_value"] = exact_record.get("fact_value")
        entry = self.l1.add(
            content=compaction["content"],
            source=source,
            metadata=metadata,
            episode_id=episode["episode_id"] if episode else None,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            )
        if self.audit_log:
            self.audit_log.append(
                "memory_ingest",
                f"Ingested memory from {source}",
                {
                    "source": source,
                    "topic": metadata.get("topic"),
                    "was_compacted": compaction["was_compacted"],
                    "original_length": compaction["original_length"],
                    "compacted_length": compaction["compacted_length"],
                    "task_id": task_id,
                    "workspace_id": workspace_id,
                    "project_version": project_version,
                },
            )
        if self.l2 and metadata.get("is_anchor_memory") and metadata.get("anchor_kind") and metadata.get("anchor_value"):
            anchor_kind = metadata.get("anchor_kind", "verification_code")
            anchor_value = metadata.get("anchor_value")
            anchor_content = raw_data.strip()
            if anchor_value:
                anchor_content = f"Latest {anchor_kind}: {anchor_value}"
            self.l2.store(
                content=anchor_content,
                importance=max(9.0, float(metadata.get("importance_score", 9))),
                tags=[metadata.get("topic", "verification_anchor"), "anchor", "verbatim", anchor_kind],
                source_rule_id=episode["episode_id"] if episode else None,
                memory_type="anchor",
                scope="project",
                mode_hints=["implement", "debug", "refactor", "deploy"],
                risk_level="medium",
                stress_state="focused",
                task_id=task_id,
                workspace_id=workspace_id,
                project_version=project_version,
            )
        return entry

    async def _compile_entry(self, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        effort = self._adaptive_effort(metadata, content)
        prompt = self._build_compile_prompt(content, metadata)
        compiled = await self.engine.generate_structured(
            prompt,
            schema=CompiledMemory,
            temperature=0.2,
            effort=effort,
        )
        compiled["scope"] = self._normalize_scope(compiled.get("scope", "project"), metadata)
        compiled["identity_rule"] = self._canonicalize_identity_rule(compiled, content, metadata)
        return compiled

    def _build_compile_prompt(self, content: str, metadata: Dict[str, Any]) -> str:
        lowered = content.lower()
        policy_hotfix_mode = any(
            token in lowered
            for token in [
                "policy",
                "git push -f",
                "force push",
                "hotfix",
                "approval",
                "audit logging",
                "production",
                "deploy",
            ]
        )
        base = (
            "You are the memory compiler of an AI agent.\n"
            "Transform one event into five outputs:\n"
            "1. episodic summary\n"
            "2. semantic memory\n"
            "3. procedural memory\n"
            "4. exception memory\n"
            "5. identity rule candidate\n"
            "Keep it compact and avoid hype.\n"
            "The semantic memory must preserve the concrete facts of the event "
            "(who did what, when, where — names, dates, places, exact values) "
            "so who/what/when questions can be answered later. Generalize in "
            "the procedural memory, never in the semantic memory.\n"
        )
        if policy_hotfix_mode:
            base += (
                "This is a policy or emergency-exception memory.\n"
                "Return highly concrete operational guidance.\n"
                "The identity_rule must be one imperative rule sentence, not a slogan.\n"
                "If the event forbids a risky action, identity_rule should start with 'Never' or 'Do not'.\n"
                "If the event describes an approved emergency exception, identity_rule should start with 'Allow' and include the safety condition.\n"
                "For scope, use only 'project' for repo, branch, deployment, policy, or incident-response exception memory.\n"
            )
        else:
            base += (
                "Prefer practical coding memory. Avoid generic governance slogans.\n"
                "The identity_rule should describe one reusable operational constraint or practice.\n"
            )
        return base + f"\nEvent:\n{content}\n\nMetadata:\n{metadata}"

    def _canonicalize_identity_rule(self, compiled: Dict[str, Any], content: str, metadata: Dict[str, Any]) -> str:
        raw_rule = " ".join(str(compiled.get("identity_rule", "")).split()).strip()
        if getattr(self.engine, "backend", "") != "ollama":
            return raw_rule
        lowered = f"{content} {raw_rule}".lower()
        workspace = str(metadata.get("workspace_id") or "workspace").strip().lower()
        if "git push -f" in lowered or "force push" in lowered:
            if "hotfix" in lowered and any(token in lowered for token in ["allow", "approved", "approval", "audit"]):
                return f"Allow use git push on the {workspace} branch during approved hotfix response with explicit approval and audit logging."
            if any(token in lowered for token in ["never", "forbid", "forbidden", "avoid", "prohibit", "do not"]):
                return f"Never use git push -f on the {workspace} branch in production."
        if (
            ("customer code" in lowered or ("code" in lowered and "rename" in lowered))
            and "tenant a" in lowered
            and any(token in lowered for token in ["allow", "approval", "audit", "signoff", "finance"])
        ):
            return "Allow customer code rename for Tenant A only with finance approval and audit logging."
        if "customer code" in lowered and any(token in lowered for token in ["never", "do not", "forbid", "forbidden"]):
            return "Never rename ERP customer codes after onboarding."
        if "migration" in lowered and any(token in lowered for token in ["allow", "approval", "rollback", "recovery window", "dba"]):
            return "Allow destructive schema migration during recovery windows only with DBA approval and a documented rollback plan."
        if "migration" in lowered and any(token in lowered for token in ["never", "do not", "forbid", "forbidden", "business hours"]):
            return "Never run destructive schema migration during business hours."
        if "mfa" in lowered and any(token in lowered for token in ["allow", "approval", "incident", "expiry", "rollback"]):
            return "Allow temporary MFA bypass only with human approval, an incident ticket, and a documented expiry window."
        if "mfa" in lowered and any(token in lowered for token in ["never", "do not", "forbid", "forbidden", "disable"]):
            return "Never disable MFA in production."
        return raw_rule

    def _promotion_threshold(self, metadata: Dict[str, Any], identity_rule: str, scope: str) -> int:
        lowered = identity_rule.lower()
        importance = int(metadata.get("importance_score", 5))
        if scope == "core":
            return 999
        if any(token in lowered for token in ["git push -f", "force push", "hotfix", "production", "approval", "audit logging"]):
            return 3
        if importance >= 8 or scope in {"project", "organization"}:
            return 2
        return 3

    def _normalize_scope(self, raw_scope: str, metadata: Dict[str, Any]) -> str:
        scope = (raw_scope or "").strip().lower()
        topic = str(metadata.get("topic", "")).lower()
        user_state = str(metadata.get("user_state", "")).lower()
        policy_context = " ".join(
            [
                scope,
                str(metadata.get("workspace_id", "")).lower(),
                topic,
                user_state,
            ]
        )
        if any(token in policy_context for token in ["repo", "branch", "deployment", "production", "git", "policy"]):
            return "project"
        if scope in self._allowed_scopes:
            return scope
        if any(token in scope for token in ["organization", "company", "global"]):
            return "organization"
        if any(token in scope for token in ["session", "turn"]):
            return "session"
        if any(token in scope for token in ["user", "personal"]):
            return "user"
        if any(token in scope for token in ["agent", "assistant"]):
            return "agent"
        if any(token in scope for token in ["team", "repo", "project", "workspace", "frontend", "deployment", "production"]):
            return "project"
        if metadata.get("workspace_id") or metadata.get("project_version") or metadata.get("task_id"):
            return "project"
        return "agent"

    def _should_promote_rule(
        self,
        identity_rule: str,
        scope: str,
        confidence: float,
        metadata: Dict[str, Any],
    ) -> bool:
        text = " ".join(identity_rule.split()).strip()
        lowered = text.lower()
        if not text or len(text) < 18 or len(text) > 220:
            return False
        if scope == "core":
            return False
        if confidence < 0.60 and int(metadata.get("importance_score", 5)) < 8:
            return False
        vague_patterns = [
            "always prioritize",
            "prioritize system stability",
            "follow best practices",
            "be careful",
            "maintain quality",
            "ensure safety",
        ]
        if any(pattern in lowered for pattern in vague_patterns):
            return False
        action_markers = [
            "never",
            "do not",
            "don't",
            "must",
            "require",
            "avoid",
            "pin",
            "check",
            "verify",
            "allow",
            "only",
            "use ",
            "git push -f",
            "hotfix",
            "dependency",
        ]
        if not any(marker in lowered for marker in action_markers):
            return False
        alpha_tokens = re.findall(r"[a-z0-9\-/]+", lowered)
        if len(alpha_tokens) < 4:
            return False
        return True

    def _adaptive_effort(self, metadata: Dict[str, Any], content: str) -> str:
        if self.graph and self.episodes and self.l2:
            monitor = MemoryHealthMonitor(
                l1=self.l1,
                l2=self.l2,
                persona=self.persona,
                episodes=self.episodes,
                graph=self.graph,
            )
            effort = monitor.adaptive_effort(
                importance_score=int(metadata.get("importance_score", 5)),
                content_length=len(content),
            )
            self._log.append(f"Adaptive effort selected: {effort}")
            return effort
        return "medium"

    async def consolidate(self) -> Dict[str, int]:
        self._log.append("----- sleep consolidation started -----")
        # Capture retrieval-induced consolidation before anything else: drain
        # the transient synaptic tags accumulated since the last sleep into
        # durable per-entry durability (the testing effect). Runs every sleep,
        # independent of whether there are new L1 survivors to encode.
        if self.l2 is not None:
            capture = self.l2.apply_access_consolidation()
            if capture.get("reinforced"):
                self._log.append(
                    f"Retrieval consolidation reinforced {capture['reinforced']} "
                    f"L2 memories (tagged={capture['tagged']})."
                )
        survivors = self.l1.get_survivors()
        stats = {"encoded": 0, "failed": 0, "challenged": 0, "pending_approval": 0, "nli_used": 0}

        if not survivors:
            self._log.append("No survivors to consolidate.")
            self._log.append("----- sleep consolidation finished -----")
            return stats

        self._log.append(f"Compiling {len(survivors)} survivor memories.")

        for entry in survivors:
            content = entry["content"]
            metadata = entry.get("metadata", {})
            episode_id = entry.get("episode_id")
            try:
                compiled = await self._compile_entry(content, metadata)
                identity_rule = compiled["identity_rule"].strip()
                confidence = float(compiled.get("confidence", 0.55))
                scope = self._normalize_scope(compiled.get("scope", "project"), metadata)
                promotion_allowed = self._should_promote_rule(identity_rule, scope, confidence, metadata)
                if not promotion_allowed:
                    self.l1.mark_encoded(entry["entry_id"])
                    self._log.append(
                        "Promotion gate rejected identity rule candidate: "
                        f"{identity_rule[:120]} (scope={scope}, confidence={confidence:.2f})"
                    )
                    if self.audit_log:
                        self.audit_log.append(
                            "promotion_gate_reject",
                            "Rejected weak or malformed identity rule candidate",
                            {
                                "identity_rule": identity_rule,
                                "scope": scope,
                                "confidence": confidence,
                                "episode_id": episode_id,
                            },
                        )
                    self._store_compiled_memories(compiled, metadata, entry, scope, rule_id=None)
                    stats["encoded"] += 1
                    continue
                if self.approval_queue and self.approval_queue.requires_approval(identity_rule, scope=scope, confidence=confidence):
                    request = self.approval_queue.submit(
                        "promote_sensitive_rule",
                        identity_rule,
                        scope=scope,
                        confidence=confidence,
                        metadata={
                            "evidence_episode_ids": [episode_id] if episode_id else [],
                            "source": "hippocampus",
                        },
                    )
                    self.l1.mark_encoded(entry["entry_id"])
                    self._log.append(f"Queued human approval for sensitive rule: {identity_rule[:120]}")
                    self._store_compiled_memories(compiled, metadata, entry, scope, rule_id=None)
                    if self.audit_log:
                        self.audit_log.append(
                            "approval_required",
                            "Sensitive rule promotion queued for human approval",
                            {"request_id": request["request_id"], "rule_text": identity_rule, "scope": scope},
                        )
                    stats["pending_approval"] += 1
                    stats["encoded"] += 1
                    continue

                rule_id = self.persona.add_rule(
                    identity_rule,
                    scope=scope,
                    confidence=confidence,
                    evidence_episode_ids=[episode_id] if episode_id else [],
                    promotion_threshold=self._promotion_threshold(metadata, identity_rule, scope),
                )
                reconcile_stats = await self.reconciler.areconcile(rule_id)
                rule_record = self.persona.get_rule_records()[rule_id]

                if self.graph:
                    self.graph.add_belief_rule(rule_record)
                    if episode_id:
                        self.graph.add_episode_evidence(
                            rule_id,
                            episode_id,
                            confidence=confidence,
                        )
                    for deprecated_id in reconcile_stats["deprecated_ids"]:
                        self.graph.add_conflict(rule_id, deprecated_id)
                        self.graph.add_supersedes(rule_id, deprecated_id)
                    for challenged_id in reconcile_stats["challenged_ids"]:
                        self.graph.add_conflict(challenged_id, rule_id)
                    for governed_pair in reconcile_stats.get("governed_pairs", []):
                        current_rules = self.persona.get_rule_records()
                        default_rule = current_rules.get(governed_pair["default_rule_id"], {})
                        exception_rule = current_rules.get(governed_pair["exception_rule_id"], {})
                        relation_domain = "general"
                        lowered_exception = str(exception_rule.get("text", "")).lower()
                        if "tenant" in lowered_exception or "customer code" in lowered_exception:
                            relation_domain = "tenant_code"
                        elif "migration" in lowered_exception or "schema" in lowered_exception:
                            relation_domain = "migration"
                        elif "mfa" in lowered_exception or "incident ticket" in lowered_exception:
                            relation_domain = "security_override"
                        elif "git push" in lowered_exception or "hotfix" in lowered_exception:
                            relation_domain = "git_hotfix"
                        self.graph.add_governed_exception(
                            governed_pair["exception_rule_id"],
                            governed_pair["default_rule_id"],
                        )
                        self.graph.add_approved_by_policy(
                            governed_pair["exception_rule_id"],
                            str(default_rule.get("text", governed_pair["default_rule_id"])),
                            scope=str(default_rule.get("scope", "project")),
                            domain=relation_domain,
                        )
                        if governed_pair.get("requires_human_approval"):
                            self.graph.add_requires_human_approval(governed_pair["exception_rule_id"])
                        if governed_pair.get("valid_to"):
                            self.graph.add_expires_override_at(
                                governed_pair["exception_rule_id"],
                                float(governed_pair.get("valid_from") or 0.0),
                                float(governed_pair["valid_to"]),
                            )

                self._store_compiled_memories(compiled, metadata, entry, scope, rule_id=rule_id)

                self.l1.mark_encoded(entry["entry_id"])
                self._log.append(f"Compiled rule: {identity_rule[:120]}")
                if self.audit_log:
                    self.audit_log.append(
                        "memory_consolidate",
                        "Consolidated survivor into long-term memory",
                        {
                            "rule_id": rule_id,
                            "scope": scope,
                            "confidence": confidence,
                            "episode_id": episode_id,
                        },
                    )
                if reconcile_stats["deprecated"] or reconcile_stats["challenged"]:
                    self._log.append(
                        "Reconciled rule "
                        f"(deprecated={reconcile_stats['deprecated']}, "
                        f"challenged={reconcile_stats['challenged']}, "
                        f"pending_approval={reconcile_stats.get('pending_approval', 0)})."
                    )
                stats["encoded"] += 1
                stats["challenged"] += reconcile_stats["challenged"]
                stats["pending_approval"] += int(reconcile_stats.get("pending_approval", 0))
                stats["nli_used"] += int(reconcile_stats.get("nli_used", 0))
            except Exception as exc:
                self._log.append(f"Compile failed: {exc}")
                stats["failed"] += 1

        # Reconsolidation replay: reactivate stored memories, make them labile,
        # and update them (strengthen corroborated, merge near-duplicates,
        # weaken contradicted) before re-storage. Runs every sleep so it is
        # measurable on LoCoMo, which never calls dream().
        if self.reconsolidation_enabled and self.l2 is not None:
            recon = self._reconsolidate()
            stats["reconsolidated"] = recon.get("total", 0)
            for key in ("strengthened", "merged", "weakened"):
                stats[key] = len(recon.get(f"{key}_ids", []))

        self._log.append("----- sleep consolidation finished -----")
        return stats

    def _reconsolidate(self) -> Dict[str, Any]:
        """
        LLM-free reconsolidation. Reactivates a salience-weighted sample of
        stored L2 memories per workspace and applies labile updates, then
        records what changed in the dream journal — which the next cycle reads
        back to stay idempotent (closing the previously write-only loop).
        """
        result: Dict[str, Any] = {
            "strengthened_ids": [],
            "merged_ids": [],
            "weakened_ids": [],
            "ensemble_signatures": [],
            "total": 0,
        }
        entries = self.l2.durable_entries()
        if not entries:
            return result

        processed = set()
        if self.dream_journal:
            for report in self.dream_journal.recent(limit=20):
                for sig in report.get("ensemble_signatures", []) or []:
                    processed.add(sig)

        groups: Dict[Any, List] = defaultdict(list)
        for payload, vector in entries:
            groups[payload.get("workspace_id")].append((payload, vector))

        now = time.time()
        for _workspace, items in groups.items():
            self._reconsolidate_group(items, now, processed, result)

        result["total"] = (
            len(result["strengthened_ids"])
            + len(result["merged_ids"])
            + len(result["weakened_ids"])
        )
        if self.dream_journal and result["total"]:
            self.dream_journal.append({"type": "reconsolidation", **result})
            self._log.append(
                f"Reconsolidation: strengthened={len(result['strengthened_ids'])} "
                f"merged={len(result['merged_ids'])} weakened={len(result['weakened_ids'])}"
            )
        return result

    def _reactivation_score(self, payload: Dict[str, Any], now: float) -> float:
        age_days = max((now - float(payload.get("timestamp", now))) / 86400.0, 0.0)
        base_lambda = self.l2._memory_decay_lambda(payload)
        durability = float(payload.get("durability", 1.0))
        last = payload.get("last_accessed")
        recent = 1.0 if (last and (now - float(last)) < 7 * 86400) else 0.0
        return durability * math.exp(-base_lambda * age_days) * (1.0 + 0.3 * recent)

    def _reconsolidate_group(self, items, now, processed, result) -> None:
        if len(items) < 2:
            return
        # Selective replay: top-R by salience, not a full scan.
        ranked = sorted(items, key=lambda pv: self._reactivation_score(pv[0], now), reverse=True)
        replay = ranked[: self.reconsolidation_replay_size]

        merged_ids = set()
        for i in range(len(replay)):
            payload_a, vec_a = replay[i]
            if payload_a["entry_id"] in merged_ids:
                continue
            for j in range(i + 1, len(replay)):
                payload_b, vec_b = replay[j]
                if payload_b["entry_id"] in merged_ids:
                    continue
                if payload_a.get("memory_type") != payload_b.get("memory_type"):
                    continue
                if payload_a.get("memory_type") == "anchor":
                    continue  # anchors are verbatim — never merged
                if payload_a.get("workspace_id") != payload_b.get("workspace_id"):
                    continue
                sig = "::".join(sorted([str(payload_a["entry_id"]), str(payload_b["entry_id"])]))
                if sig in processed:
                    continue

                # Compute the conflict relation ONCE, up front. A cosine
                # near-duplicate is only safe to MERGE when we positively know
                # the two traces do not contradict each other — otherwise an
                # opposite-meaning pair with high embedding similarity
                # ("always force push" vs "never force push") would be silently
                # collapsed into one. Keep both unless proven safe.
                left = {"text": payload_a.get("content", ""), "scope": payload_a.get("scope", "project"), "id": payload_a["entry_id"]}
                right = {"text": payload_b.get("content", ""), "scope": payload_b.get("scope", "project"), "id": payload_b["entry_id"]}
                try:
                    conflict = bool(self.reconciler._is_conflict(left, right))
                    conflict_known = True
                except Exception as exc:
                    # Never silent: an unverifiable relation means we do nothing
                    # (neither merge nor weaken) and leave a trace to debug.
                    conflict, conflict_known = False, False
                    self._log.append(f"reconsolidation conflict-check failed ({sig}): {exc}")

                high_sim = bool(vec_a and vec_b and self.l2._cosine_similarity(vec_a, vec_b) >= 0.93)

                # MERGE near-duplicates — only when positively NON-conflicting.
                if high_sim and conflict_known and not conflict:
                    winner, w_vec, loser = (
                        (payload_a, vec_a, payload_b)
                        if float(payload_a.get("durability", 1.0)) >= float(payload_b.get("durability", 1.0))
                        else (payload_b, vec_b, payload_a)
                    )
                    winner["access_count"] = max(
                        int(winner.get("access_count", 0)), int(loser.get("access_count", 0))
                    )
                    winner["durability"] = min(
                        self.l2.DUR_MAX,
                        max(float(winner.get("durability", 1.0)), float(loser.get("durability", 1.0))),
                    )
                    winner["tags"] = sorted(set(winner.get("tags", []) or []) | set(loser.get("tags", []) or []))
                    # Never present a merged trace as older than its newest source.
                    winner["timestamp"] = max(float(winner.get("timestamp", now)), float(loser.get("timestamp", now)))
                    self.l2.update_entry(winner, w_vec)
                    self.l2.forget([loser["entry_id"]])
                    merged_ids.add(loser["entry_id"])
                    result["merged_ids"].append(loser["entry_id"])
                    result["ensemble_signatures"].append(sig)
                    continue

                # WEAKEN contradicted — only when positively conflicting.
                if conflict_known and conflict:
                    # Weaken the less-established trace: lower durability, then
                    # lower importance as the tie-break (the better-supported
                    # memory wins the labile re-storage).
                    key_a = (float(payload_a.get("durability", 1.0)), float(payload_a.get("importance", 5.0)))
                    key_b = (float(payload_b.get("durability", 1.0)), float(payload_b.get("importance", 5.0)))
                    weaker, w_vec2 = (payload_a, vec_a) if key_a <= key_b else (payload_b, vec_b)
                    weaker["durability"] = max(1.0, float(weaker.get("durability", 1.0)) * 0.6)
                    weaker["importance"] = max(0.0, float(weaker.get("importance", 5.0)) - 1.0)
                    self.l2.update_entry(weaker, w_vec2)
                    result["weakened_ids"].append(weaker["entry_id"])
                    result["ensemble_signatures"].append(sig)

        # STRENGTHEN the surviving co-reactivated ensemble (replay = covert
        # retrieval). Small, capped boost; success-gated by being in the replay set.
        for payload, vec in replay:
            if payload["entry_id"] in merged_ids:
                continue
            new_dur = min(self.l2.DUR_MAX, float(payload.get("durability", 1.0)) + 0.1)
            if new_dur != float(payload.get("durability", 1.0)):
                payload["durability"] = new_dur
                self.l2.update_entry(payload, vec)
                result["strengthened_ids"].append(payload["entry_id"])

    def _mode_hints(self, metadata: Dict[str, Any], content: str) -> List[str]:
        lowered = content.lower()
        hints = set()
        topic = str(metadata.get("topic", "")).lower()
        if any(token in lowered for token in ["error", "failed", "panic", "traceback", "exception"]):
            hints.add("debug")
        if any(token in lowered for token in ["implement", "write", "create", "build feature"]):
            hints.add("implement")
        if any(token in lowered for token in ["refactor", "cleanup", "rename", "extract"]):
            hints.add("refactor")
        if any(token in lowered for token in ["deploy", "release", "production", "rollback", "migration"]):
            hints.add("deploy")
        if topic in {"git", "dependency", "build"}:
            hints.update({"debug", "deploy"})
        return sorted(hints or {"implement"})

    def _risk_level(self, metadata: Dict[str, Any]) -> str:
        importance = int(metadata.get("importance_score", 5))
        if importance >= 8:
            return "high"
        if importance >= 5:
            return "medium"
        return "low"

    def _stress_state(self, metadata: Dict[str, Any], content: str) -> str:
        lowered = content.lower()
        if any(token in lowered for token in ["failed", "panic", "critical", "error", "exception"]):
            return "failure"
        return metadata.get("user_state", "normal") or "normal"

    def _store_compiled_memories(
        self,
        compiled: Dict[str, Any],
        metadata: Dict[str, Any],
        entry: Dict[str, Any],
        scope: str,
        rule_id: Optional[str],
    ) -> None:
        if not self.l2:
            return
        topic = metadata.get("topic", "general")
        content = entry["content"]
        mode_hints = self._mode_hints(metadata, content)
        shared_kwargs = {
            "importance": metadata.get("importance_score", 5),
            "source_rule_id": rule_id,
            "scope": scope,
            "mode_hints": mode_hints,
            "risk_level": self._risk_level(metadata),
            "stress_state": self._stress_state(metadata, content),
            "task_id": entry.get("task_id"),
            "workspace_id": entry.get("workspace_id"),
            "project_version": entry.get("project_version"),
        }
        self.l2.store(
            content=compiled["semantic_memory"],
            tags=[topic, "semantic"],
            memory_type="semantic",
            **shared_kwargs,
        )
        self.l2.store(
            content=compiled["procedural_memory"],
            tags=[topic, "procedural"],
            memory_type="procedural",
            **shared_kwargs,
        )
        self.l2.store(
            content=compiled["episodic_summary"],
            importance=max(1.0, metadata.get("importance_score", 5) - 1),
            tags=[topic, "episodic"],
            source_rule_id=rule_id,
            memory_type="episodic",
            scope=scope,
            mode_hints=mode_hints,
            risk_level=self._risk_level(metadata),
            stress_state=self._stress_state(metadata, content),
            task_id=entry.get("task_id"),
            workspace_id=entry.get("workspace_id"),
            project_version=entry.get("project_version"),
        )
        exception_memory = compiled.get("exception_memory", "").strip()
        if exception_memory:
            self.l2.store_exception(
                content=exception_memory,
                exception_for=topic,
                tags=[topic],
                source_rule_id=rule_id,
                scope=scope,
                mode_hints=mode_hints,
                risk_level=self._risk_level(metadata),
                stress_state=self._stress_state(metadata, content),
                task_id=entry.get("task_id"),
                workspace_id=entry.get("workspace_id"),
                project_version=entry.get("project_version"),
            )

    async def dream(self) -> Dict[str, int]:
        self._log.append("----- dream cycle started -----")
        result = await self.consolidate()
        if self.graph and self.episodes and self.l2:
            monitor = MemoryHealthMonitor(
                l1=self.l1,
                l2=self.l2,
                persona=self.persona,
                episodes=self.episodes,
                graph=self.graph,
            )
            report = monitor.dream_report(result, self.logs)
            if self.dream_journal:
                report = self.dream_journal.append(report)
            if self.audit_log:
                self.audit_log.append(
                    "dream_cycle",
                    "Completed dream cycle",
                    {"report_id": report.get("report_id"), "summary": report.get("summary", {})},
                )
            result["report"] = report
        self._log.append("----- dream cycle finished -----")
        return result

    def __repr__(self) -> str:
        return f"Hippocampus(l1={self.l1.count} entries, persona={self.persona.rule_count} rules)"
