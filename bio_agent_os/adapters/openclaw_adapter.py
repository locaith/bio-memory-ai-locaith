"""
adapters/openclaw_adapter.py — OpenClaw Integration.

Bộ chuyển đổi này đóng vai trò như cầu nối giữa hệ thống Action/Observation 
của OpenClaw (và OpenDevin) và hệ thống Biologically Inspired Memory của chúng ta.

Nó sẽ:
1. Parse log từ OpenClaw thành Raw Data cho Hippocampus.
2. Hook vào chu kỳ vòng lặp của OpenClaw để tự động chạy Garbage Collection.
3. Bắn ngược Core Identity (Persona) vào System Prompt của OpenClaw.
"""

from typing import Dict, Any, List
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.core.persona import Persona


class OpenClawBioAdapter:
    """
    Adapter để biến Bio-Agent OS thành Long-Term Memory backend cho OpenClaw.
    
    Tích hợp này giúp OpenClaw:
    - Tránh rác terminal (bằng cơ chế cắt tỉa).
    - Duy trì "Nhận Thức" về dự án thay vì quên sạch sau 100 token.
    """
    
    def __init__(self, hippocampus: Hippocampus, garbage_collector: GarbageCollector, persona: Persona):
        self.hippo = hippocampus
        self.gc = garbage_collector
        self.persona = persona
        self.action_counter = 0
        
    async def ingest_observation(self, action_type: str, observation_output: str) -> bool:
        """
        Nhận Observation thô cứng từ OpenClaw và nạp vào Hippocampus.
        Ví dụ: `TerminalOutput` dài 500 dòng sẽ được bộ não lọc.
        """
        # Nếu output quá dài, chúng ta chặn lại không cho nạp nguyên bản mà sẽ trích xuất
        if len(observation_output) > 2000:
            observation_output = observation_output[:1000] + "\n...[TRUNCATED TO PREVENT BLOAT]...\n" + observation_output[-1000:]
            
        raw_data = f"OpenClaw Action [{action_type}]:\nKết quả trả về:\n{observation_output}"
        
        # Real-time label & store vào L1
        await self.hippo.label_and_store(raw_data, source="OpenClaw-Worker")
        self.action_counter += 1
        
        # Cứ sau 10 action, ép OpenClaw nghỉ ngơi (Micro-Sleep)
        if self.action_counter >= 10:
            await self.trigger_micro_sleep()
            
        return True

    async def trigger_micro_sleep(self):
        """
        Hook kích hoạt giấc ngủ ngắn: Dọn rác (GC) và Nén (Consolidation).
        """
        print("[OpenClaw Adapter] Kích hoạt Micro-Sleep Cycle...")
        self.gc.run()
        await self.hippo.consolidate()
        self.action_counter = 0
        
    def inject_persona_to_openclaw(self) -> str:
        """
        Lấy các "Luật" vĩnh viễn đã học được và đưa vào System Prompt của OpenClaw.
        Chặn OpenClaw lặp lại sai lầm trong các task mới.
        """
        return self.persona.get_identity_prompt()

