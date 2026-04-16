"""
background_jobs/graph_builder.py — Entity & Relationship Extractor.

Trích xuất Entities và Relationships từ Raw Text,
cập nhật vào Knowledge Graph (Đồ thị tri thức).

Chạy bất đồng bộ (background) khi hệ thống rảnh rỗi.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph


class ExtractedTriple(BaseModel):
    """Schema cho một bộ ba (Entity-Relation-Entity)."""
    source_entity: str = Field(description="Thực thể nguồn (vd: 'FastAPI', 'Tuấn Anh')")
    source_type: str = Field(description="Loại thực thể nguồn (person, technology, project, concept)")
    relation: str = Field(description="Mối quan hệ (vd: 'sử_dụng', 'quản_lý', 'là_phần_của')")
    target_entity: str = Field(description="Thực thể đích")
    target_type: str = Field(description="Loại thực thể đích")


class ExtractedTriples(BaseModel):
    """Schema cho danh sách bộ ba trích xuất được."""
    triples: List[ExtractedTriple] = Field(
        description="Danh sách các bộ ba Entity-Relation-Entity trích xuất từ văn bản"
    )


class GraphBuilder:
    """
    Extract entities and relationships from text, update Knowledge Graph.
    
    Flow:
      Raw text → LLM extracts triples → GraphBuilder inserts into KnowledgeGraph
    
    Usage:
        builder = GraphBuilder(engine=llm, graph=kg)
        stats = await builder.process("Tuấn Anh dùng FastAPI để build AgentOS")
        # → Adds: (Tuấn Anh) --[sử_dụng]--> (FastAPI)
        # → Adds: (Tuấn Anh) --[xây_dựng]--> (AgentOS)
    """

    def __init__(self, engine: LLMEngine, graph: KnowledgeGraph):
        self.engine = engine
        self.graph = graph
        self._log: List[str] = []

    @property
    def logs(self) -> List[str]:
        return list(self._log)

    def clear_logs(self):
        self._log.clear()

    async def extract_triples(self, text: str) -> List[Dict[str, str]]:
        """Extract entity-relation-entity triples from raw text using LLM."""
        prompt = f"""Bạn là bộ trích xuất tri thức (Knowledge Extractor).
Từ đoạn văn bản sau, hãy trích xuất các bộ ba (Entity - Relation - Entity).

Văn bản: "{text[:1000]}"

Quy tắc:
- Mỗi Entity phải có tên cụ thể (không dùng đại từ)
- Relation phải ngắn gọn, dùng gạch_dưới (vd: sử_dụng, tạo_ra, thuộc_về)
- source_type/target_type: person, technology, project, concept, organization, event
- Chỉ trích xuất những gì RÕ RÀNG từ văn bản, không suy đoán
"""
        try:
            result = await self.engine.generate_structured(
                prompt, schema=ExtractedTriples, temperature=0.1
            )
            return [t if isinstance(t, dict) else t for t in result.get("triples", [])]
        except Exception as e:
            self._log.append(f"Lỗi trích xuất: {e}")
            return []

    async def process(self, text: str) -> Dict[str, int]:
        """Extract triples from text and insert into Knowledge Graph."""
        self._log.append(f"GraphBuilder đang xử lý: '{text[:50]}...'")

        triples = await self.extract_triples(text)
        stats = {"entities_added": 0, "relations_added": 0}

        for triple in triples:
            if isinstance(triple, dict):
                src = triple.get("source_entity", "")
                src_type = triple.get("source_type", "concept")
                rel = triple.get("relation", "liên_quan")
                tgt = triple.get("target_entity", "")
                tgt_type = triple.get("target_type", "concept")
            else:
                # Pydantic model
                src, src_type = triple.source_entity, triple.source_type
                rel = triple.relation
                tgt, tgt_type = triple.target_entity, triple.target_type

            if not src or not tgt:
                continue

            if self.graph.add_entity(src, entity_type=src_type):
                stats["entities_added"] += 1
            if self.graph.add_entity(tgt, entity_type=tgt_type):
                stats["entities_added"] += 1
            if self.graph.add_relation(src, rel, tgt):
                stats["relations_added"] += 1

            self._log.append(f"  + ({src}) --[{rel}]--> ({tgt})")

        self._log.append(
            f"Hoàn tất: +{stats['entities_added']} entities, "
            f"+{stats['relations_added']} relations"
        )
        return stats

    async def process_batch(self, texts: List[str]) -> Dict[str, int]:
        """Process multiple texts and aggregate stats."""
        total = {"entities_added": 0, "relations_added": 0}
        for text in texts:
            stats = await self.process(text)
            total["entities_added"] += stats["entities_added"]
            total["relations_added"] += stats["relations_added"]
        return total

    def __repr__(self) -> str:
        return f"GraphBuilder(graph={self.graph})"
