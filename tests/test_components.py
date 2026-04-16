import os
import pytest
from bio_agent_os import L1WorkingMemory, L2SemanticMemory, KnowledgeGraph, Persona

def test_l1_memory():
    l1 = L1WorkingMemory(agent_name="test_agent", storage_dir="test_data")
    l1.clear()
    l1.add("Login endpoint failing", metadata={"importance_score": 8, "is_junk_or_transient": False})
    
    assert l1.count == 1
    assert "Login endpoint" in l1.build_context_string()

def test_l2_semantic_memory():
    # Uses in-memory Qdrant by default now!
    l2 = L2SemanticMemory(agent_name="test_agent", storage_dir="test_data")
    l2.store("Always use generic exception handling", importance=8.0, tags=["coding"])
    
    results = l2.search("exception handling", top_k=1)
    assert len(results) > 0
    assert results[0]['importance'] == 8.0

def test_persona_encryption():
    # Pass an encryption key to secure core identity
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    p = Persona(name="test_agent", storage_dir="test_data")
    rule_id = p.add_rule("Rule 1: Always check logs")
    
    rules = p.get_rules()
    assert rule_id in rules
    assert rules[rule_id] == "Rule 1: Always check logs"
