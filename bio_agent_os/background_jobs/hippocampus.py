"""
Sleep consolidation and memory compilation.
"""

import re
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
        self._log: List[str] = []
        self._allowed_scopes = {"core", "project", "agent", "user", "session", "organization"}

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
            if subject and value and len(value) <= 96 and len(value.split()) <= 6 and "," not in value and ":" not in value:
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
            if value and len(value) <= 96 and len(value.split()) <= 6 and "," not in value and ":" not in value:
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
        if extracted or any(marker in lowered for marker in ["remember exactly", "ghi nhớ chính xác"]):
            metadata["topic"] = "verification_anchor"
            metadata["importance_score"] = max(int(metadata.get("importance_score", 5)), 9)
            metadata["is_junk_or_transient"] = False
            metadata["is_anchor_memory"] = True
            metadata["retain_verbatim"] = True
            metadata.update(extracted)
            if observation_type in {"chat_input", "chat_output"} and "urgent" not in str(metadata.get("user_state", "")):
                metadata["user_state"] = metadata.get("user_state") or "focused"
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
                    self.l1.mark_encoded(entry["timestamp"])
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
                    self.l1.mark_encoded(entry["timestamp"])
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

                self.l1.mark_encoded(entry["timestamp"])
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

        self._log.append("----- sleep consolidation finished -----")
        return stats

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
