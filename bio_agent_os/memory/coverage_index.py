"""
Verified-Coverage Multi-Resolution Memory Index (VCMI)
======================================================

Đóng góp mới cho Bio-Agent OS — giải "chỗ trống" mà các hệ trí nhớ AI hiện nay
(Mem0, Zep/Graphiti, Letta/MemGPT, LangMem, ChatGPT/Claude memory) đều bỏ ngỏ:
tất cả đều tóm tắt/ trích xuất theo kiểu LOSSY (mất mát) và KHÔNG bảo chứng rằng
"không sót thông tin nào". Các khảo sát 2026 gọi đây là mục tiêu chưa ai đạt
("coverage guarantee", "provenance visibility", "reversibility").

Nguyên lý (được kiểm chứng bởi 2 kết quả 2026:
  • "Fidelity Before Structure" — văn bản gốc thắng trích xuất 15.9 điểm;
  • "What to Keep, What to Forget" (rate–distortion) — nén không mất mát dưới ngân
    sách là bất khả; thứ khả thi là REVERSIBILITY: nội dung bỏ ra phải tái tạo được):

  1. KHO THÔ BẤT TỬ (immortal ledger) = EpisodeStore. Không bao giờ xoá. Là nguồn
     sự thật duy nhất, lưu NGUYÊN VĂN.
  2. CÂY CHỈ MỤC NHIỀU TẦNG (disposable, rebuild được) phủ lên kho thô: mỗi node
     lưu ĐÚNG tập episode_id mà nó phủ (provenance/back-pointer, giải được về lá).
  3. BẤT BIẾN ĐỘ PHỦ (điểm mới, ĐO ĐƯỢC): mọi episode phải được ≥1 node phủ, và
     coverage của cha = hợp coverage của con. `coverage_report()` trả % phủ + danh
     sách "mồ côi" → biến "không sót" thành CON SỐ KIỂM CHỨNG ĐƯỢC, không phải lời hứa.
  4. ZOOM VỀ NGUỒN (reversibility): từ bất kỳ node tóm tắt nào → giải ra đúng các
     episode thô nguyên văn. Tóm tắt thô đến đâu vẫn phục hồi được chi tiết gốc.
  5. QUÊN = CHỈ HẠ HẠNG CHỈ MỤC, KHÔNG XOÁ DỮ LIỆU. Đường cong Ebbinghaus tác động
     lên thứ hạng truy hồi của node; kho thô bất tử. "Quên" ⇒ node bị xếp hạng thấp,
     nhưng vẫn zoom lại được nguyên văn. Tách bạch dứt khoát hai nghĩa của "forgetting"
     (ngừng chú ý ≠ mất dữ liệu) — điều gần như chưa hệ nào làm sạch.

Kết quả: một hệ trí nhớ mà bạn có thể CHỨNG MINH không sót thông tin (coverage=100%,
zoom phục hồi đúng nguồn) — chạy local, không phụ thuộc cloud.
"""
from __future__ import annotations

import math
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from bio_agent_os.core.sqlite_store import SQLiteStore


class CoverageIndex:
    def __init__(
        self,
        agent_name: str = "Bio-AI",
        storage_dir: str = "data",
        episodes=None,
        embedder=None,
        group_size: int = 6,
        summarizer: Optional[Callable[[List[str]], str]] = None,
    ):
        if episodes is None:
            raise ValueError("CoverageIndex requires an EpisodeStore (the immortal ledger).")
        self.agent_name = agent_name
        self.episodes = episodes
        self.embedder = embedder
        self.group_size = max(2, int(group_size))
        # Mặc định tóm tắt TRÍCH XUẤT (deterministic, không cần LLM) — provenance,
        # coverage và zoom là bất biến bất kể chất lượng tóm tắt. Có thể cắm LLM sau.
        self._summarize = summarizer or self._extractive_summary
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_memindex"
        self._ensure_table()

    # ── Lưu trữ chỉ mục ──────────────────────────────────────────────────────
    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                node_id TEXT PRIMARY KEY,
                workspace_id TEXT,
                level INTEGER NOT NULL,
                summary TEXT NOT NULL,
                covered_ids_json TEXT NOT NULL,   -- tập episode_id (transitive) node này phủ
                child_ids_json TEXT NOT NULL,      -- id các node con (level thấp hơn)
                created_at REAL NOT NULL,
                last_access REAL NOT NULL,
                strength REAL NOT NULL             -- độ bền Ebbinghaus (>=1), càng dùng càng bền
            )
            """
        )
        self._store.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self._table}_ws ON {self._table}(workspace_id, level)"
        )

    @staticmethod
    def _extractive_summary(texts: List[str]) -> str:
        parts = []
        for t in texts:
            t = " ".join(str(t).split())
            parts.append(t[:140])
        joined = " · ".join(parts)
        return joined[:900]

    def _ws_clause(self, workspace_id: Optional[str]) -> tuple[str, list]:
        if workspace_id is None:
            return "", []
        return "workspace_id = ?", [workspace_id]

    # ── Xây / cập nhật chỉ mục (incremental) ─────────────────────────────────
    def refresh(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """Phủ mọi episode CHƯA được phủ bằng node lá mới (level 1), rồi dựng lại
        các tầng tóm tắt phía trên. Giữ bất biến độ phủ = 100%."""
        eps = self.episodes.query(workspace_id=workspace_id, limit=1_000_000)
        eps = sorted(eps, key=lambda e: float(e.get("timestamp", 0.0)))
        all_ids = [e["episode_id"] for e in eps]

        covered = self._covered_leaf_ids(workspace_id)
        uncovered = [e for e in eps if e["episode_id"] not in covered]

        now = time.time()
        new_leaves = 0
        for i in range(0, len(uncovered), self.group_size):
            group = uncovered[i : i + self.group_size]
            ids = [e["episode_id"] for e in group]
            summary = self._summarize([e.get("raw_payload", "") for e in group])
            self._insert_node(workspace_id, level=1, summary=summary,
                              covered_ids=ids, child_ids=[], now=now)
            new_leaves += 1

        # Dựng lại các tầng cao (level >=2) từ toàn bộ node tầng dưới.
        self._rebuild_upper_levels(workspace_id, now)

        report = self.coverage_report(workspace_id)
        report["new_leaf_nodes"] = new_leaves
        report["total_episodes_seen"] = len(all_ids)
        return report

    def _covered_leaf_ids(self, workspace_id: Optional[str]) -> set:
        where, params = self._ws_clause(workspace_id)
        sql = f"SELECT covered_ids_json FROM {self._table} WHERE level = 1"
        if where:
            sql += f" AND {where}"
        rows = self._store.fetchall(sql, params)
        out: set = set()
        for row in rows:
            out.update(self._store.loads_json(row["covered_ids_json"], []))
        return out

    def _rebuild_upper_levels(self, workspace_id: Optional[str], now: float):
        # Xoá node level >=2 cũ rồi dựng lại từ level 1 (đảm bảo phủ nhất quán).
        where, params = self._ws_clause(workspace_id)
        del_sql = f"DELETE FROM {self._table} WHERE level >= 2"
        if where:
            del_sql += f" AND {where}"
        self._store.execute(del_sql, params)

        level = 1
        while True:
            lower = self._nodes_at_level(workspace_id, level)
            if len(lower) <= 1:
                break  # đã tới gốc
            level += 1
            for i in range(0, len(lower), self.group_size):
                group = lower[i : i + self.group_size]
                covered: List[str] = []
                child_ids: List[str] = []
                for node in group:
                    covered.extend(self._store.loads_json(node["covered_ids_json"], []))
                    child_ids.append(node["node_id"])
                summary = self._summarize([n["summary"] for n in group])
                # dedup giữ thứ tự
                seen: set = set()
                covered = [c for c in covered if not (c in seen or seen.add(c))]
                self._insert_node(workspace_id, level=level, summary=summary,
                                  covered_ids=covered, child_ids=child_ids, now=now)

    def _nodes_at_level(self, workspace_id: Optional[str], level: int) -> List[Dict[str, Any]]:
        where, params = self._ws_clause(workspace_id)
        sql = f"SELECT * FROM {self._table} WHERE level = ?"
        p = [level]
        if where:
            sql += f" AND {where}"
            p += params
        sql += " ORDER BY created_at ASC"
        return [dict(r) for r in self._store.fetchall(sql, p)]

    def _insert_node(self, workspace_id, level, summary, covered_ids, child_ids, now):
        node_id = f"n{level}-{uuid.uuid4().hex[:16]}"
        self._store.execute(
            f"""INSERT OR REPLACE INTO {self._table}
                (node_id, workspace_id, level, summary, covered_ids_json, child_ids_json,
                 created_at, last_access, strength)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [node_id, workspace_id, level, summary,
             self._store.dumps_json(covered_ids), self._store.dumps_json(child_ids),
             now, now, 1.0],
        )
        return node_id

    # ── BẤT BIẾN ĐỘ PHỦ (điểm mới, đo được) ──────────────────────────────────
    def coverage_report(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """Kiểm chứng: mọi episode trong kho thô có được ≥1 node lá phủ không?"""
        eps = self.episodes.query(workspace_id=workspace_id, limit=1_000_000)
        all_ids = {e["episode_id"] for e in eps}
        covered = self._covered_leaf_ids(workspace_id)
        orphans = sorted(all_ids - covered)
        total = len(all_ids)
        pct = 100.0 if total == 0 else round(len(all_ids & covered) / total * 100.0, 4)
        # Bất biến cha=hợp(con): kiểm tra mỗi node cấp cao phủ đúng hợp coverage của con.
        parent_ok = self._verify_parent_union(workspace_id)
        return {
            "workspace_id": workspace_id,
            "total_episodes": total,
            "covered_episodes": len(all_ids & covered),
            "coverage_pct": pct,
            "orphans": orphans,
            "lossless": (pct == 100.0 and not orphans),
            "parent_union_ok": parent_ok,
        }

    def _verify_parent_union(self, workspace_id: Optional[str]) -> bool:
        where, params = self._ws_clause(workspace_id)
        sql = f"SELECT * FROM {self._table} WHERE level >= 2"
        if where:
            sql += f" AND {where}"
        for row in self._store.fetchall(sql, params):
            covered = set(self._store.loads_json(row["covered_ids_json"], []))
            union: set = set()
            for cid in self._store.loads_json(row["child_ids_json"], []):
                child = self._store.fetchone(
                    f"SELECT covered_ids_json FROM {self._table} WHERE node_id = ?", [cid]
                )
                if child:
                    union.update(self._store.loads_json(child["covered_ids_json"], []))
            if covered != union:
                return False
        return True

    # ── ZOOM VỀ NGUỒN (reversibility / lossless recovery) ────────────────────
    def zoom(self, node_id: str) -> List[Dict[str, Any]]:
        """Giải một node tóm tắt về ĐÚNG các episode thô nguyên văn mà nó phủ."""
        row = self._store.fetchone(
            f"SELECT covered_ids_json FROM {self._table} WHERE node_id = ?", [node_id]
        )
        if not row:
            return []
        ids = self._store.loads_json(row["covered_ids_json"], [])
        return self.episodes.get_many(ids)

    # ── TRUY HỒI cấp cao + QUÊN kiểu Ebbinghaus (chỉ hạ hạng, không xoá) ──────
    def retrieve(self, query: str, workspace_id: Optional[str] = None,
                 top_k: int = 3, level: int = 1) -> List[Dict[str, Any]]:
        nodes = self._nodes_at_level(workspace_id, level)
        if not nodes:
            return []
        q_terms = {t for t in query.lower().split() if t}
        q_vec = None
        if self.embedder is not None:
            try:
                q_vec = self.embedder.embed(query)
            except Exception:
                q_vec = None
        now = time.time()
        scored = []
        for node in nodes:
            text = node["summary"].lower()
            overlap = len(q_terms & set(text.split()))
            dense = 0.0
            if q_vec is not None:
                try:
                    v = self.embedder.embed(node["summary"])
                    dense = max(0.0, sum(a * b for a, b in zip(q_vec, v)))
                except Exception:
                    dense = 0.0
            relevance = dense * 3.0 + overlap * 0.7
            # Ebbinghaus: R = exp(-Δt / S). CHỈ tác động thứ hạng, KHÔNG đụng kho thô.
            dt_days = max(0.0, (now - float(node["last_access"])) / 86400.0)
            retention = math.exp(-dt_days / max(1e-6, float(node["strength"])))
            scored.append({
                "node_id": node["node_id"],
                "summary": node["summary"],
                "covered_count": len(self._store.loads_json(node["covered_ids_json"], [])),
                "relevance": round(relevance, 3),
                "retention": round(retention, 4),
                "score": round(relevance * (0.2 + 0.8 * retention), 4),
            })
        scored.sort(key=lambda x: -x["score"])
        return scored[:top_k]

    def reinforce(self, node_id: str, amount: float = 1.0):
        """Dùng lại một ký ức → tăng độ bền (decay chậm hơn). Ebbinghaus: S tăng."""
        row = self._store.fetchone(
            f"SELECT strength FROM {self._table} WHERE node_id = ?", [node_id]
        )
        if not row:
            return
        new_strength = float(row["strength"]) + amount
        self._store.execute(
            f"UPDATE {self._table} SET strength = ?, last_access = ? WHERE node_id = ?",
            [new_strength, time.time(), node_id],
        )

    @property
    def node_count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS c FROM {self._table}")
        return int(row["c"]) if row else 0
