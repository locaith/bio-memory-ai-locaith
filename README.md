<p align="center">
  <img src="docs/images/openclaw_integration.png" alt="OpenClaw + Bio-Agent OS Integration" width="100%"/>
</p>

<p align="center">
  <h1 align="center">🧠 Bio-Agent OS v0.8.2rc1</h1>
  <p align="center"><strong>The Biological Memory Upgrade for OpenClaw, ERP AI & Autonomous Agents</strong></p>
  <p align="center"><em>"Biết nhớ · Biết quên · Biết tư duy"</em></p>
  <p align="center">Researched & Developed by <strong>Dev Tuan Anh Ha</strong> (<a href="https://locaith.com">Locaith Solution Tech</a>) | 🇻🇳 Make in Vietnam</p>
</p>

<p align="center">
  <a href="#-phiên-bản-tiếng-việt">🇻🇳 Đọc bằng Tiếng Việt</a> | <a href="#-english-version">🇬🇧 Read in English</a>
</p>

---

# 🇻🇳 Phiên bản Tiếng Việt

> **Về chúng tôi (About this Repository):** `bio-agent-os` là một mã nguồn mở mang tính cách mạng, cung cấp lõi quản trị trí nhớ (Memory Controller) mô phỏng chính xác cấu trúc sinh học từ não bộ. Giải pháp được phát triển bởi **Locaith Solution Tech** nhằm thay thế các phương thức nén dữ liệu độc hại của Big Tech (như Context Window Compression), giúp các AI Agent và hệ thống ERP hiện đại có khả năng ghi nhớ vĩnh viễn với chi phí tối ưu nhất.

**Nền tảng khoa học:** Hệ thống Bio-Agent OS được nghiên cứu và chế tạo dựa trên khoa học thần kinh đã chứng minh về cơ chế phát triển não bộ của con người bắt đầu từ sau 3 tuổi. Khi đó, bộ não bắt đầu loại bỏ những ký ức vụn vặt (infantile amnesia) để giữ lại và mã hóa những nhận thức, kỹ năng sinh tồn cốt lõi. Chúng tôi mang cơ chế "Quên để Nhớ" này áp dụng trực tiếp lên Trí tuệ AI.

## 🚀 Sứ mệnh: The "Trojan Horse" cho OpenClaw & OpenDevin

Bạn đang dùng Agent mã nguồn mở như **OpenClaw, OpenDevin, hay SWE-agent**? Agent của bạn chạy task rất giỏi nhưng... **càng lúc càng ngu đi và tốn kém Token?**

Vấn đề của các Autonomous Agent hiện tại là chúng xài bộ nhớ như một bãi rác (Vector DB nhồi nhét mọi log terminal dài ngoằng). Chúng tốn hàng triệu token để duy trì ngữ cảnh nhưng KHÔNG BAO GIỜ học được một **Quy luật** nào cho dự án cụ thể.

Lắp **Bio-Agent OS** vào làm backend Memory là bạn đang trang bị một bộ nhớ sinh học vượt trội cho OpenClaw cũng như bất kỳ hệ thống ERP AI nào. Chuyển đổi Agent của bạn từ một cỗ máy "bạo lực Token" thành một thực thể thông minh biết tự tiến hoá.

### Lợi ích "Độc Tôn" khi cắm Bio-Memory vào Hệ thống của bạn:
1. **Chống Tràn RAM tuyệt đối (Garbage Collection)**: Cắt tỉa các terminal log vô nghĩa, xóa bỏ các bước "thử và sai" rùng rợn, giữ lại output cốt lõi nhất.
2. **Học "Luật Bất Biến" (Encoding Shift)**: Tự động đúc kết lại lỗi đã gặp thành một Luật vĩnh viễn (Persona): *"Luật 04: Cấm dùng git push -f trong dự án frontend"*. OpenClaw sẽ lập tức code chuẩn trong task tiếp theo mà không cần chèn thêm context.
3. **Cơ chế Ngủ (Micro-Sleep cycles)**: Sau mỗi 10 lệnh command, AI sẽ "đi ngủ" để Hồi Hải Mã (Hippocampus) nén tri thức.

---

## 📊 Benchmark thật: LoCoMo (đối chiếu Naive-RAG)

Không vẽ biểu đồ mô phỏng — đây là **số đo thật, tái lập được** trên **LoCoMo** (Maharana và cộng sự, 2024), benchmark trí nhớ hội thoại dài hạn chuẩn ngành: 10 hội thoại nhiều phiên (~200–400 lượt mỗi cái), 300 câu hỏi đánh giá, chấm điểm **token-F1 + Exact Match kiểu SQuAD — KHÔNG dùng LLM tự chấm**. Ba hệ thống chạy cùng một model + embedding local.

<p align="center">
  <img src="docs/images/locomo_benchmark.png" alt="LoCoMo: Bio-Agent OS vs Naive-RAG (sinh từ benchmark_reports/ bằng scripts/plot_locomo.py)" width="100%"/>
</p>

> Biểu đồ trên được sinh trực tiếp từ các report đã commit bằng `python scripts/plot_locomo.py` — không có số nào vẽ tay.

**Kết quả chính (qwen2.5:7b-instruct, 300 câu hỏi):**

| Hệ thống | F1 | EM | |
|:---|:---:|:---:|:---|
| No-memory (sàn) | 0.012 | 0.003 | chứng minh task không tầm thường |
| Naive-RAG | 0.254 | 0.083 | nhồi mọi lượt vào vector DB + top-k |
| **Bio-Agent OS** | **0.326** | **0.107** | **+28% F1 so với Naive-RAG** |

**Nơi bio-memory thắng đậm — suy luận thời gian (temporal):** **0.372** vs **0.136** của Naive-RAG — **gấp 2.7×**. Đây chính là luận điểm cốt lõi ("biết quên · biết nhớ") phát huy: ngày tháng được giữ nguyên qua chu kỳ consolidation thay vì bị nhấn chìm trong rác.

**Lợi thế qua ba model** (slice 90 câu, cùng config):

| Model | Bio-Agent OS | Naive-RAG | bio temporal | naive temporal | cách biệt temporal |
|:---|:---:|:---:|:---:|:---:|:---:|
| gemma4:e2b (5B) | 0.406 | 0.391 | 0.349 | 0.326 | **+7%** |
| qwen2.5:7b (7B) | 0.421 | 0.308 | 0.525 | 0.215 | **+144%** |
| gemma4:12b (12B) | **0.498** | 0.461 | **0.603** | 0.416 | **+45%** |

Bio-memory thắng Naive-RAG trên **cả ba** model.

**Đính chính 14/08/2026.** Bản trước của bảng này chỉ hiện cột `bio temporal` (0.349 → 0.525 → 0.603) và kết luận *"kiến trúc càng hữu ích hơn khi model mạnh hơn"*. Kết luận đó **sai**, và số bác bỏ nó vốn đã nằm trong chính các report nguồn — chỉ là chưa ai đặt hai cột cạnh nhau. Baseline cũng khá lên theo model (0.326 → 0.215 → 0.416), nên **cách biệt không đơn điệu**: +7%, +144%, rồi +45%. Có một dải giữa nơi trí nhớ giúp được nhiều nhất; model quá yếu thì không dùng nổi context tốt, model quá mạnh thì tự trả lời được từ ít context hơn. Cột `naive temporal` được thêm vào để người đọc tự kiểm tra.

(gemma4:12b chạy gọn 8.4GB VRAM trên RTX 3060 — ứng viên "hồi hải mã" mặc định cho máy phổ thông.)

### Đối đầu mem0 — 13/08/2026

Cùng bộ câu hỏi, cùng model trả lời (`gpt-4o-mini` — chính model mem0 dùng để công bố số của họ), cùng embedder, cùng harness. 150 câu, đủ 4 nhóm. mem0 được cài đủ extras (spaCy, BM25) và **0 lỗi trích xuất** trong lần chạy này.

| Hệ | Tổng F1 | temporal | single-hop | multi-hop | open-domain |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Bio-Agent OS** | **0.4424** | **0.5008** | **0.5508** | 0.3691 | 0.0634 |
| Naive-RAG | 0.3970 | 0.3773 | 0.5310 | 0.3929 | 0.0723 |
| mem0 | 0.3958 | 0.4392 | 0.4284 | **0.4191** | **0.1223** |

Thắng tổng thể **+11.8%** so với mem0, và **+14%** ở suy luận thời gian — nhóm mà kiến trúc này được thiết kế để mạnh.

Nguồn: [`reports/head2head_openai_2026_08_13.md`](reports/head2head_openai_2026_08_13.md) · chi phí đo: $1.62, đếm từng lời gọi.

### Giới hạn đã biết

Công bố thẳng, kèm số:

- **Thua mem0 ở multi-hop** (0.3691 vs 0.4191) và **open-domain** (0.0634 vs 0.1223). Multi-hop là điểm yếu lặp lại qua hai lần đo cách nhau hai tháng.
- **Hồi hải mã chưa chứng minh được làm truy xuất tốt hơn.** Đo 14/08 trên 194 câu hỏi thật: bật nhãn và tắt nhãn cho **kết quả y hệt** (148/194), không một câu thay đổi. Nhãn mô tả ký ức; xếp hạng cần biết ký ức có liên quan tới câu hỏi hay không — hai đại lượng khác nhau.
- **Hợp nhất ký ức cũng chưa chứng minh được.** 45 cụm hợp nhất: sửa được 4 câu, làm hỏng 4 câu, tổng không đổi. Và dữ liệu tháng 6 cho thấy chạy với `sleep_every=0` (tắt hợp nhất) đạt temporal **0.603** — cao hơn cả hai lần có hợp nhất.
- **Thứ tạo ra kết quả là embedding**, không phải cơ chế sinh học. Thêm vector vào tầng `cognitive/` đưa nó từ 0.0048 lên 0.3658 trên LoCoMo.
- **Hai tầng chưa ngang nhau.** Số ở trên là của tầng sinh học (`bio-memory`). Tầng mà hook Claude Code thực sự chạy (`cognitive/`) hiện đạt 0.3658. Khoảng cách này là việc đang làm.
- Bộ 194 câu hỏi nội bộ **toàn là recall** — chưa có case cho phát hiện mâu thuẫn, ký ức cũ, hay quên có kiểm soát.

Toàn bộ hành trình phát triển (F1 0.0 → 0.498 qua nhiều lần vá, kể cả các lần thua và các giả thuyết bị bác bỏ) đều nằm trong git: mọi report, kể cả bản xấu nhất.

**Tự kiểm chứng (3 dòng lệnh):**
```bash
python scripts/run_locomo_eval.py --backend ollama \
  --model qwen2.5:7b-instruct \
  --systems no-memory,naive-rag,bio-memory --tag myrun
```
Report nguồn: [`benchmark_reports/locomo_overnight_qwen7b_v3.md`](benchmark_reports/locomo_overnight_qwen7b_v3.md) (kết quả chính) và `benchmark_reports/locomo_modelcmp_*.md` (so sánh model).

---

## 🏗️ Kiến trúc Framework cốt lõi (Core Architecture)

| Thành phần | Chức năng (Ứng dụng cho OpenClaw/ERP) | Cơ quan tương ứng |
|:---:|:---|:---:|
| 🟢 **L1 Buffer** | Bộ đệm Terminal Logs & Code diffs ngắn hạn. | **Prefrontal Cortex** |
| 🔵 **L2 Semantic** | Semantic Search Vector Codebases + Ebbinghaus Decay. | **Neocortex** |
| 🟡 **Persona** | Hệ thống Rules (Luật) "nhập vai" vĩnh viễn. | **Core Identity** |
| 🔴 **Knowledge Graph** | Đồ thị luồng dữ liệu (Graph Dependencies) của hệ thống code. | **Association Areas** |
| ⚙️ **Hippocampus** | Biến "lỗi terminal dài 1MB" thành "1 câu Error Rules". | **Sleep Cycle** |
| ✂️ **Pruner** | Tiêu huỷ code vứt đi và file configs rác sau khi xong task. | **Synaptic Pruning** |

### 🧬 Bổ sung mới trong nền tảng V2
1. **Episode Store**: Mỗi trải nghiệm giờ có `episode_id`, provenance, actor, topic, confidence và source refs để truy ngược lại nguồn gốc ký ức.
2. **Self-Model có scope**: Persona không còn chỉ là danh sách rule text, mà có thêm `scope`, `confidence`, `support_count`, `contradiction_count`, `state`, `evidence_episode_ids`.
3. **Memory Compiler 4 đầu ra**: Hippocampus giờ nén trải nghiệm thành 4 lớp: `episodic`, `semantic`, `procedural`, `identity rule candidate`.
4. **Dream Cycle**: Bổ sung đường chạy `dream()` ngoài sleep thông thường để chuẩn bị cho reconsolidation ở V2.1.

---

## 🚀 Cài đặt Siêu Tốc

```bash
# Cài đặt framework bản mới nhất (có sẵn adapter)
pip install bio-agent-os[gemini]
```

Hoặc dùng package riêng cho OpenClaw:

```bash
pip install bio-locaith-openclaw
```

### ✅ Trạng thái bản hiện tại

- **Phiên bản hiện tại: `0.8.2rc1`** — release candidate của Reliability Kernel. **Chưa phải bản stable.** Đường ghi mặc định vẫn là `legacy`; xem mục [Reliability Kernel v0.8.2](#-reliability-kernel-v082--đường-ghi-có-thể-phục-hồi) bên dưới.
- **Kết quả chính (LoCoMo, 300 câu hỏi, qwen2.5:7b):** Bio-Agent OS F1 `0.326` vs Naive-RAG `0.254` (**+28%**), suy luận thời gian gấp `2.7×`. Xem mục [Benchmark thật](#-benchmark-thật-locomo-đối-chiếu-naive-rag) bên trên.
- `v0.6.1` có hybrid contradiction detector kèm NLI cache (vẫn còn nguyên trong bản hiện tại).
- **Bộ kiểm thử đơn vị phát hiện mâu thuẫn** (8 cặp tự biên — *unit test có chủ đích, không phải benchmark thống kê*): heuristic `4/8` → hybrid+NLI `8/8`, false positive `0`. Đây là bằng chứng phụ cho riêng module detector; bằng chứng chính là LoCoMo ở trên.
- **528 bài kiểm thử tự động** (`pytest tests/`, 31 file) — con số này từng là `80` ở bản `v0.6.1`. Đóng gói Docker, adapter PostgreSQL, MCP server, REST API có xác thực.
- **CI chạy ma trận 4 cấu hình:** Ubuntu Python `3.10` / `3.11` / `3.12` và Windows Python `3.11`. Windows không phải để trang trí — projection worker dùng `spawn`, fault matrix giết process thật, và WAL test giữ khoá SQLite qua nhiều connection; không cái nào hành xử giống `fork` trên Linux.
- **Fault matrix `25/25`** — crash recovery ở cấp process thật, dùng `os._exit`/`TerminateProcess` chứ không phải exception giả lập.

### Sử dụng OpenClaw Adapter (Preview)

Chúng tôi cung cấp sẵn một Blueprint `OpenClawBioAdapter` trong thư mục `bio_agent_os.adapters` để bạn cắm thẳng vào vòng lặp của tác vụ.

```python
import asyncio
from bio_agent_os import LLMEngine, L1WorkingMemory, Persona, Hippocampus, GarbageCollector
from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter

async def main():
    # 1. Khởi tạo Brain
    engine = LLMEngine.from_env()
    l1 = L1WorkingMemory(agent_name="openclaw-brain")
    persona = Persona(name="openclaw-brain")
    hippo = Hippocampus(engine=engine, l1=l1, persona=persona)
    gc = GarbageCollector(l1=l1)

    # 2. Khởi tạo Adapter
    adapter = OpenClawBioAdapter(hippocampus=hippo, garbage_collector=gc, persona=persona)

    # 3. Simulate Pipeline của OpenClaw chạy lệnh terminal
    await adapter.ingest_observation("run_command", "npm ERR! cb() never called!")

    # Kích hoạt Sleep Mode bằng tay hoặc chờ đủ limit
    await adapter.trigger_micro_sleep()

    # 4. Trích xuất rules bơm ngược lại vào System Prompt
    print(adapter.inject_persona_to_openclaw())

async def run():
    await main()

if __name__ == "__main__":
    asyncio.run(run())
```

### 🔌 OpenClaw Plugin: pip install + 1 dòng config

Từ bản hiện tại, adapter đã được đóng gói thành plugin target pip-installable.

```bash
pip install bio-locaith-openclaw
bio-locaith-openclaw install-openclaw-plugin
```

OpenClaw config mẫu đúng format hiện tại nằm ở:

- `examples/openclaw/openclaw.bio-agent-os.json`

Chỉ cần bật memory slot:

```yaml
plugins:
  slots:
    memory: "bio-locaith-openclaw"
```

Nếu bạn muốn copy nguyên config đầy đủ:

```yaml
plugins:
  enabled: true
  load:
    paths:
      - "~/.openclaw/extensions/bio-locaith-openclaw"
  slots:
    memory: "bio-locaith-openclaw"
  entries:
    bio-locaith-openclaw:
      enabled: true
      config:
        apiBaseUrl: "http://127.0.0.1:8055"
        agentName: "openclaw-brain"
        workspaceId: "main"
        projectVersion: "v1"
```

Plugin này sẽ:
- ingest terminal/tool observations vào episode memory
- trigger micro-sleep consolidation
- bơm `self-model + safety guard + governed exceptions` ngược vào prompt của OpenClaw

### 🛠️ SWE-Agent Plugin

Config overlay mẫu đúng format SWE-Agent nằm ở:

- `examples/swe-agent/bio_memory_overlay.yaml`

```yaml
sweagent run --config config/default.yaml --config examples/swe-agent/bio_memory_overlay.yaml
```

Mục tiêu tương tự: dùng cùng lõi bio-memory nhưng bọc thành đường sidecar/config riêng cho SWE-Agent.

### 🔌 MCP Server: cắm vào Claude Code, Cursor và mọi nền tảng MCP

Bio-Agent OS có sẵn MCP server chuẩn (Model Context Protocol) — nghĩa là bất kỳ nền tảng nào nói được MCP (Claude Code, Cursor, OpenAI Agents SDK, agent tự dựng...) đều lắp được trí nhớ sinh học này bằng đúng một lệnh:

```bash
pip install bio-agent-os[mcp]
claude mcp add bio-memory -- bio-agent-os serve-mcp
```

Agent của bạn lập tức có 5 tool: `store_memory`, `recall`, `list_rules`, `memory_status`, `consolidate`. Mặc định memory chạy embedded ngay trong process (zero setup, lưu tại `STORAGE_DIR`).

Muốn nhiều agent **dùng chung một bộ nhớ** (ví dụ Claude Code + OpenClaw cùng nhớ một thứ)? Trỏ MCP server vào sidecar đang chạy:

```bash
bio-agent-os serve-mcp --base-url http://127.0.0.1:8055 --api-key $BIO_AGENT_API_KEY --workspace-id main
```

Tenant key (`BIO_AGENT_TENANT_KEYS`) hoạt động bình thường ở chế độ này — mỗi agent chỉ thấy workspace của mình.

### 📌 Ghi nhận tích hợp thực tế với OpenClaw / BioLoca

Bio-Agent OS đã được một agent OpenClaw cài và nối thành công vào hệ BioLoca trên máy khác, theo đúng flow vận hành thực tế:

1. clone repo `locaith/bio-memory-ai-locaith`
2. cài `bio-agent-os` và package `bio-locaith-openclaw`
3. khởi chạy Bio-Agent OS API sidecar trên cổng `8055`
4. trỏ plugin `bio-locaith-openclaw` vào `openclaw.json`
5. restart OpenClaw gateway để nhận memory slot mới

Điểm quan trọng ở đây không phải benchmark giả lập, mà là OpenClaw đã tự báo cáo lại rằng nó load được lớp trí nhớ sinh học này như một memory backend thật trong workflow BioLoca.

### 🔌 Cấu hình đa nền tảng model: Local AI, Gemini, Claude, GPT, Grok

Bio-Agent OS giờ hỗ trợ nhiều đường chạy khác nhau cho phần "Hồi Hải Mã" và layer suy luận:

1. **Gemini**
```env
LLM_BACKEND=gemini
MODEL_ID=gemini-3-flash-preview
GEMINI_API_KEY=your_key_here
```

Nếu bạn muốn dùng bản mạnh hơn cho reasoning/coding:

```env
LLM_BACKEND=gemini
MODEL_ID=gemini-3.1-pro-preview
GEMINI_API_KEY=your_key_here
```

2. **Claude / Anthropic**
```env
LLM_BACKEND=anthropic
MODEL_ID=claude-opus-4-6
ANTHROPIC_API_KEY=your_key_here
```

3. **OpenAI / GPT**
```env
LLM_BACKEND=openai
MODEL_ID=gpt-5.4
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
```

4. **Grok / xAI**
```env
LLM_BACKEND=grok
MODEL_ID=grok-4.20-reasoning
XAI_API_KEY=your_key_here
XAI_BASE_URL=https://api.x.ai/v1
```

5. **Ollama**
```env
LLM_BACKEND=ollama
MODEL_ID=gemma4:e2b
OLLAMA_BASE_URL=http://localhost:11434
```

6. **AI Local / LM Studio / vLLM / OpenWebUI / mọi máy chủ tương thích OpenAI**
```env
LLM_BACKEND=openai
MODEL_ID=gemma4:e2b
LLM_API_KEY=local-dev-key
LLM_BASE_URL=http://127.0.0.1:1234/v1
```

Nếu máy bạn mạnh và đã cài local model như `gemma4:e2b`, bạn có thể dùng model đó làm local hippocampus ngay mà không cần cloud API.

### ⚡ Khởi chạy nhanh cho người dùng mới tải repo

```bash
git clone https://github.com/locaith/bio-memory-ai-locaith
cd bio-memory-ai-locaith
py -3 -m venv .venv
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
py -3 -m pip install -e ".[openai]"
copy .env.example .env
py -3 -m bio_agent_os.api.main
```

API hiện có:
- `POST /api/chat`
- `POST /api/ingest`
- `POST /api/sleep`
- `POST /api/dream`
- `GET /api/reflect`
- `GET /api/health`
- `GET /api/status`
- `GET /api/state`
- `GET /api/graph`
- `GET /api/beliefs`
- `GET /api/beliefs/timeline`
- `GET /api/beliefs/{rule_id}`
- `GET /api/dreams`
- `GET /api/audit`
- `GET /api/replay`

Bổ sung từ các bản sau (tổng cộng **34 route**):
- `POST /api/retrieve` · `POST /api/reset`
- `GET /api/episodes` — duyệt episode store kèm provenance
- `GET /api/lineage` · `POST /api/lineage` — truy vết nguồn gốc một ký ức
- `GET /api/coverage` · `POST /api/coverage/refresh` · `POST /api/coverage/retrieve`
- `GET /api/exact-memories` · `POST /api/exact-memories/reindex`
- `GET /api/approvals` · `POST /api/approvals/{request_id}/approve` · `POST /api/approvals/{request_id}/reject`
- `GET /api/revalidation` · `POST /api/revalidation/resolve`
- `GET /api/confidence-dashboard` · `GET /api/zoom`
- `DELETE /api/workspace/{workspace_id}` — xoá sạch một workspace

### Các nâng cấp bộ nhớ Phase 5

Bio-Agent OS V2.1 phase 5 hiện bổ sung 4 nâng cấp thực dụng cho OpenClaw session dài:

1. `nén lại`: các quan sát quá dài sẽ được nén gọn trước khi vào L1, nhưng episode gốc vẫn được giữ để audit và replay.
2. `nỗ lực linh hoạt`: hippocampus có thể tự tăng mức effort cho ký ức quan trọng hoặc lúc bộ nhớ đang quá tải, thay vì đốt effort cao cho mọi event.
3. `xem lại / kiểm tra bộ nhớ`: dùng `GET /api/audit` và `GET /api/replay` để soi lại toàn bộ vòng đời ingest, consolidate, reflect, dream.
4. `benchmark sâu hơn`: CI giờ kiểm cả mini benchmark lẫn long-session benchmark cho OpenClaw để đảm bảo rule được reinforce qua nhiều micro-sleep cycle.

---

## 🧪 Hướng nâng cấp V2.1 đang triển khai

Mục tiêu V2.1 là đưa Bio-Agent OS tiến thêm một bước gần hơn với trí nhớ người:

1. **Contradiction Resolver**: Rule mới không overwrite rule cũ ngay, mà challenge, reinforce hoặc deprecate theo evidence.
2. **Belief Lifecycle**: Memory/rule đi qua các trạng thái `proposed`, `reinforced`, `stable`, `challenged`, `deprecated`, `archived`.
3. **Reconsolidation**: Khi có episode mới, memory cũ được đọc lại và có thể bị chỉnh sửa thay vì chỉ append thêm.
4. **Hệ sinh thái OpenClaw**: README, config và flow cài đặt nhấn mạnh vào cộng đồng OpenClaw để clone về dùng được ngay.
5. **Tính di động toàn cầu**: Một bộ điều khiển bộ nhớ (Memory Controller) dùng được trên local AI lẫn cloud AI, áp dụng cho nhiều agent framework.

---

## 🛡️ Reliability Kernel v0.8.2 — đường ghi có thể phục hồi

> **Trạng thái: `0.8.2rc1`, release candidate.** Đường ghi mặc định vẫn là `legacy`.
> Outbox **không** tự bật. Rollback là một biến môi trường.

Tất cả những gì ở trên nói về *trí nhớ*. Mục này nói về *độ tin cậy của việc ghi trí nhớ đó xuống đĩa* — thứ mà một hệ thống chạy nhiều năm bắt buộc phải có.

### Vấn đề gốc

`MemoryOS` mở **sáu connection SQLite độc lập** vào cùng một file. Event được commit trên connection này, projection trên connection khác. Một cú crash giữa hai lần commit để lại một event bền vững mà **không ai biết là còn nợ một projection**. Nó không mất dữ liệu — nó mất *thông tin rằng có việc chưa làm*, và đó là loại hỏng không ai phát hiện ra.

### Cách giải quyết

| Thành phần | Vai trò |
|:---|:---|
| **Transactional outbox** | Event và bản ghi "còn nợ projection" commit trong **cùng một transaction**. Hoặc cả hai tồn tại, hoặc không cái nào. |
| **Target-local ledger** | Ghi cùng transaction với chính projection → **đúng-một-lần về hiệu quả** trên nền giao-ít-nhất-một-lần. Retry sau crash thấy ledger và biết việc đã xong. |
| **Leased worker** | Claim theo lease, backoff luỹ thừa, dead-letter đúng `max_attempts`, xử lý phụ thuộc giữa các projection type. |
| **Replay engine** | Tìm lại việc còn nợ. **Mặc định dry-run.** |
| **Fault injection** | 14 điểm crash có tên. Không dùng `sleep()` rồi đoán process đang ở đâu. |
| **Doctor** | Chẩn đoán chỉ-đọc, phân biệt *chưa hỗ trợ* với *hỏng*. Có cả deep và incremental. |
| **Reconciliation** | Sửa chữa theo danh sách cho phép, mặc định dry-run, mọi lần `--repair` đều ghi audit. |
| **WAL manager** | Checkpoint quan sát được, có ngưỡng và cảnh báo. |
| **Shadow mode** | Chạy song song legacy và outbox từ **một input chuẩn tắc duy nhất**, rồi so sánh. |

### Số đo thật (không phải mô phỏng)

Toàn bộ đo trên i5-12400F / 32 GB / NTFS / SQLite WAL. Raw nằm ở `reports/v082/`, phương pháp ở [`docs/v082/BENCHMARK_REPORT.md`](docs/v082/BENCHMARK_REPORT.md).

**Đúng đắn — trên khoảng 900.000 event:**

```
0 event mất          0 debt mất
0 projection trùng   0 ledger trùng
0 rò rỉ tenant       0 shadow mismatch không giải thích được
integrity_check ok sau mọi lần chạy
```

**Shadow mode:** `10.000/10.000` MATCH. **0** hàng shadow lọt vào bảng production, **0** hàng shadow bị `recall()` trả về — cách ly bằng **bảng riêng**, không phải bằng bộ lọc.

**Soak 1 giờ:** 366.715 event nạp, 366.713 hoàn tất, **queue sâu nhất chỉ 6**, p95 hiển thị `80 ms`, p99 `140 ms`, RSS tăng `10,7 MB/giờ`, **0 lock error**, hai lần restart worker đều phục hồi.

**Throughput và điểm bão hoà:**

| Cấu hình | Append | Projection | Tổng |
|:---|---:|---:|---:|
| 1p + 1w | 1.091/s | 756/s | 1.847/s |
| **4p + 4w** | 1.164/s | 754/s | **1.918/s** |
| 4p + 8w | 567/s | 566/s | 1.133/s |

**4 worker là điểm bão hoà. 8 worker làm mất 41% throughput** — thêm worker trên máy này là trừ đi hiệu năng, không phải cộng.

**Điều quan trọng nhất, và là điều dễ hiểu sai nhất:** producer nhanh hơn projector khoảng **1,3–1,9 lần**, và khoảng cách **nới rộng** khi database lớn lên (703 → 613 → 556 job/s ở 10K → 50K → 100K). Nghĩa là **không có trạng thái ổn định nào trên tốc độ projection**. Độ trễ khi quá tải là *hàm của thời gian quá tải*, không phải thuộc tính của pipeline — p95 đi từ 4,9s → 31,8s → **93,5s** khi chỉ tăng số event.

### Safe Operating Envelope

Tính từ **sàn**, không phải từ đỉnh. Đỉnh của một benchmark là lần chạy may nhất trên máy rảnh; lấy nó làm giới hạn vận hành là một lời hứa không giữ được.

```
SQLite single-node alpha

  producer khuyến nghị       4
  worker khuyến nghị         4
  tốc độ nạp bền vững        390 event/s      (headroom 30%)
  burst                      550 event/s trong 60 giây
  queue depth lành mạnh tối đa  1.100
  p95 hiển thị kỳ vọng       < 100 ms
  dung lượng                 3,1 KB mỗi event, tuyến tính
```

> **Tính theo projector, tuyệt đối không tính theo appender.** Append gánh được gấp ba lần projector có thể tiêu thụ. Bất kỳ con số nào tính từ append đều tạo ra một hàng đợi phình mãi.
>
> **Cảnh báo theo queue depth, không theo độ trễ.** Độ trễ là chỉ báo trễ của một tồn đọng đã hình thành từ trước.

### Rollback

```bash
BIO_AGENT_PROJECTION_MODE=legacy
```

Restart process. Hết. Không migration ngược, không viết lại gì, không đổi schema. Debt đã commit **được giữ lại** để replay sau chứ không bị xoá — xoá nó là huỷ bằng chứng duy nhất rằng việc từng được giao.

Ba lệnh vận hành đi kèm:

```bash
bio-agent-os projection pause --reason "..."   # job đang chạy vẫn chạy hết
bio-agent-os projection resume
bio-agent-os projection drain                  # ghi đè pause một cách có chủ đích
```

Chi tiết: [`docs/v082/ROLLBACK_RUNBOOK.md`](docs/v082/ROLLBACK_RUNBOOK.md).

### Doctor: chẩn đoán không bao giờ tự sửa

```bash
bio-agent-os doctor                    # nhanh, chỉ đọc
bio-agent-os doctor --deep             # toàn bộ check set
bio-agent-os doctor --incremental      # từ cursor an toàn khi crash
bio-agent-os projection status
bio-agent-os projection reconcile              # dry-run mặc định
bio-agent-os projection reconcile --repair     # luôn ghi audit
bio-agent-os storage wal-status
bio-agent-os storage checkpoint --mode passive
```

Exit code: `0` sạch · `1` FAIL · `2` CRITICAL · `3` **bản thân lần quét không hoàn thành**. Mã 3 xếp trên các finding có chủ đích: một lần quét chết dở tuyệt đối không được trông giống một giấy chứng nhận sức khoẻ.

Chi phí quét, đo trên database 100.000 event:

| Chế độ | Thời gian |
|:---|---:|
| `--deep` (audit) | 13,36 s |
| quick (4 check) | 8,31 s |
| **`--incremental`, không có gì mới** | **2,12 s** |
| `--incremental`, 500 event mới | 2,70 s |

Incremental rẻ hơn deep **6,3×**, và rẻ hơn cả quick mode dù chạy **nhiều check hơn** — khác biệt nằm ở `integrity_check`, thứ phải đọc từng trang.

Cursor chỉ tiến sau một lần quét **hoàn tất**, và chỉ khi **không còn gì tồn đọng**: một FAIL hay CRITICAL giữ nguyên cursor cho tới khi vấn đề thực sự biến mất. Bước qua một finding chưa xử lý sẽ giấu nó vĩnh viễn — đó là kiểu hỏng duy nhất mà một scanner incremental không được phép có.

### WAL

Soak đo được WAL lên **500 MB** sau một giờ — **46% kích thước database** — và chỉ về 0 khi connection cuối cùng đóng. Không mất gì, nhưng cũng không thu hồi được gì, và một process chạy lâu thì mãi mãi không thu hồi.

```
dưới soft limit (256 MB)   PASSIVE
vượt soft limit            PASSIVE + cảnh báo
vượt hard limit (512 MB)   RESTART nếu không có reader đăng ký
```

`TRUNCATE` **không bao giờ tự động** — nó chờ mọi reader, và một job nền mà chờ reader là một job nền làm treo process. Chi tiết: [`docs/v082/WAL_OPERATIONS.md`](docs/v082/WAL_OPERATIONS.md).

### Trung thực về những gì chưa đạt

Chúng tôi công bố cả những chỗ không đẹp, giống như đã làm với multi-hop ở phần LoCoMo.

1. **Shadow overhead trượt ngưỡng đề ra.** Ngưỡng đặt trước là ≤10% ở p95; đo được **99,4%**. Tuyệt đối là **+0,30 ms**. Chúng tôi **không sửa ngưỡng để làm cho bài test đạt** — nó đứng nguyên trong báo cáo như một lần trượt. Từ giai đoạn canary trở đi, SLO được viết theo **ngân sách tuyệt đối** (≤0,50 ms) với tỷ lệ chỉ để báo cáo, vì phần trăm của một con số rất nhỏ không nói lên được điều gì đáng hành động.
2. **PostgreSQL chưa đo.** Mọi đường cong throughput ở đây do đặc tính *một writer duy nhất* của SQLite định hình. Trên backend có `FOR UPDATE SKIP LOCKED`, không có gì đảm bảo hình dạng đó còn đúng — tốt hơn hay xấu hơn đều chưa biết.
3. **4/5 projection type chưa có builder.** `cognitive_memory` là loại duy nhất chạy được. Bốn loại còn lại được doctor báo là **`unsupported` (thiếu năng lực)**, không phải `passed`, và cũng không phải `hỏng`.
4. **Biến thiên giữa các lần chạy lớn.** Cùng một cấu hình đo được `1.164` rồi `328` event/s cách nhau vài phút. Bốn lần chạy lặp cho biên độ **1,88×** ở producer và **1,15×** ở projector. Vì vậy envelope tính từ sàn của projector.
5. **Soak mới 1 giờ**, chưa phải 6 hay 24 giờ.

### Ba lỗi mà chính công việc này tìm ra

Cả ba đều **cũ hơn** phần việc đã phát hiện ra chúng:

1. **Doctor quét kiểu quadratic.** Ba check dùng `LIKE '%' || event_id || '%'` trong subquery tương quan — leading wildcard không dùng được index. Exponent **2,1**; ngoại suy ở 100K là **2,75 giờ**. Sau khi thay bằng bảng liên kết có index: **0,59s** ở 10K, **65,7s** ở 366K, và findings giữ nguyên `1.009` ở mọi quy mô.
2. **Hàng FTS sống lâu hơn ký ức của nó.** Dựng lại một projection khi đó tạo ra hai entry cùng khoá, và SQLite báo `malformed inverted index` — tức là **hỏng database**, từ một thao tác *được hỗ trợ*.
3. **Doctor có thể báo hỏng giả.** Pragma integrity chạy trên connection đang giữ read snapshot cũ. `SQLITE_INTEGRITY` là CRITICAL — đủ để dừng một canary mà không có gì sai cả.

### Đọc thêm

| Tài liệu | Nội dung |
|:---|:---|
| [`docs/v082/BENCHMARK_REPORT.md`](docs/v082/BENCHMARK_REPORT.md) | Môi trường, phương pháp, sáu workload, số liệu thô, cả kết quả xấu |
| [`docs/v082/OPERATIONS.md`](docs/v082/OPERATIONS.md) | Doctor và reconciliation: mã finding, chính sách sửa chữa |
| [`docs/v082/CANARY_RUNBOOK.md`](docs/v082/CANARY_RUNBOOK.md) | Shadow 24 giờ, rồi canary theo tenant allow-list |
| [`docs/v082/ROLLBACK_RUNBOOK.md`](docs/v082/ROLLBACK_RUNBOOK.md) | Một biến môi trường, và những gì sống sót qua nó |
| [`docs/v082/WAL_OPERATIONS.md`](docs/v082/WAL_OPERATIONS.md) | Vì sao WAL phình, bốn chế độ, cảnh báo |
| [`docs/v082/RC1_RELEASE_NOTES.md`](docs/v082/RC1_RELEASE_NOTES.md) | Bản này là gì và **không** là gì |
| [`docs/v082/FAILURE_MATRIX.md`](docs/v082/FAILURE_MATRIX.md) | 25 ca crash ở cấp process |
| [`docs/v082/SHADOW_MODE.md`](docs/v082/SHADOW_MODE.md) | So sánh legacy ↔ outbox |

### Kết luận về cutover

**CONDITIONAL GO — chỉ cho `cognitive_memory`, chỉ trên SQLite single-node, legacy vẫn là mặc định và là đường rollback.**

18/19 điều kiện đạt. Điều duy nhất trượt là shadow overhead. Đây **không phải** production distributed, và chúng tôi không mô tả nó như vậy ở bất kỳ đâu: một node, một storage engine, một projection type, và bản cũ cách đúng một biến môi trường.

---

## 🔬 Trạng thái nghiên cứu hiện tại — Projection Runtime

Mục này ghi trạng thái **đã được kiểm chứng bằng thực thi**, không phải ý định.
Cập nhật 19/08/2026.

| Năng lực | Trạng thái |
|---|---|
| Replay resurrection safety | **VERIFIED** |
| Tenant isolation | **VERIFIED** |
| H1 liveness/fairness (single + multi-worker, common clock) | **VERIFIED** |
| Safe rollback (generation replacement) | **VERIFIED** |
| Production hook single-writer | **VERIFIED** |
| **New-write activation (OUTBOX)** | **VERIFIED — đang sống** |
| **Semantic parity giữa hai đường ghi** | **VERIFIED** — hợp đồng ghi lưu bền trong event (`MemoryProjectionIntent`), một constructor cho mọi writer, parity gate + mutant |
| Historical inventory & contract archaeology | **VERIFIED** — 326 events phân lớp đủ, UNCLASSIFIED = 0, comparator thực thi được |
| Historical adoption (HBF-2) | **PLANNED — chưa chạy**; mọi mutation lịch sử chỉ trên candidate offline |
| Multi-node workers | **NOT CERTIFIED** (chưa có clock-skew contract) |

Ba sự cố đáng kể đã xảy ra và được xử lý đúng kỷ luật, giữ nguyên trong lịch
sử commit: một race stale-yield (vá bằng compare-and-set, có mutant); một lần
index corruption do chính quy trình rollback cũ (root cause VERIFIED bằng
control trials — page cache của handle sống, không phải WAL frames; thay bằng
generation replacement); và một regression làm nghèo semantics khi đổi đường
ghi (SP-0/SP-1 — sinh ra luật `CONTENT_EQUIVALENT ≠ PROJECTION_EQUIVALENT`,
9 ký ức thật được repair tại chỗ với audit trong chính record).

Store người dùng: đường ghi mới OUTBOX **đang hoạt động** với parity theo hợp
đồng; lịch sử cũ **chưa migrate** — kế hoạch adoption đã ký ở mức thiết kế.

Chi tiết: [`H1_QUEUE_LIVENESS_REPORT.md`](H1_QUEUE_LIVENESS_REPORT.md),
[`H1_4_MULTIWORKER_REPORT.md`](H1_4_MULTIWORKER_REPORT.md),
[`activation/HBF1_MIGRATION_PLAN.md`](activation/HBF1_MIGRATION_PLAN.md),
[`activation/A5_REPORT.md`](activation/A5_REPORT.md).

---

## 🌏 Tầm Nhìn & Cam kết Mã Nguồn Mở

**Bio-Agent OS** không phải là LLM model. Chúng tôi là **"Memory Controller"** — bộ phận quyết định trí thông minh lâu dài của các mô hình.
Chúng tôi mong muốn hỗ trợ toàn diện các nền tảng Agent hiện tại (như OpenClaw, SWE-agent) và **đặc biệt là tích hợp vào các hệ thống ERP Doanh Nghiệp (ERP AI)** để tối ưu hoá quy trình quản trị, tự động lưu trữ và chắt lọc kinh nghiệm vận hành.

---

## 📬 Liên hệ & Triển khai doanh nghiệp

Hệ thống **Bio-Agent OS** được nghiên cứu và phát triển bởi **Dev Tuan Anh Ha** (Top 4 Google for Startups Accelerator) cùng đội ngũ **Locaith Solution Tech**. Nếu bạn cần triển khai kiến trúc Bio-Memory tinh chỉnh cho dữ liệu khép kín của tổ chức, hãy liên hệ:

- 🏢 **Công ty**: Locaith Solution Tech
- 📍 **Địa chỉ**: Số 6 Ngõ 7 Phố Tôn Thất Thuyết, Thành phố Hà Nội
- ✉️ **Email Tổ chức**: locaithsolution@locaith.com
- ✉️ **Email Cá nhân (Dev Tuan Anh Ha)**: tuananhnangluong@gmail.com
- 📞 **Hotline**: 0966 872 591
- 🌐 **Website**: [https://locaith.com](https://locaith.com)
- ▶️ **YouTube**: [@locaithSolution](https://youtube.com/@locaithSolution)
- 🔵 **Facebook**: [Locaith Fanpage](https://www.facebook.com/profile.php?id=61560965389617)

---

# 📜 Bài báo Kỹ thuật Chính thức: Kiến trúc Bio-Agent OS

> *Một Kiến trúc Bộ nhớ Truyền cảm hứng từ Sinh học cho các Tác nhân Lập trình Tự trị với Sự chú ý Nội cân bằng, Quản lý Vòng đời Niềm tin và Giải quyết Mâu thuẫn dựa trên NLI*

**Locaith Solution Tech**

> *Đã gửi cho Hội thảo NeurIPS 2026 về Bộ nhớ và Truy xuất trong các Mô hình Nền tảng*

---

## Tóm tắt

Các tác nhân tự trị chạy trong thời gian dài yêu cầu hệ thống bộ nhớ bền vững vượt xa các phương pháp lưu trữ key-value đơn giản hoặc các cửa sổ ngữ cảnh (context window) chỉ cho phép nối thêm dữ liệu. Chúng tôi giới thiệu **Bio-Agent OS**, một framework bộ nhớ mã nguồn mở lấy cảm hứng từ khoa học thần kinh để cung cấp cho các tác nhân lập trình và ERP một kiến trúc bộ nhớ trung thực về mặt sinh học. Hệ thống của chúng tôi triển khai: (1) một quy trình bộ nhớ đa tầng mô phỏng quá trình củng cố trí nhớ ở người (Bộ nhớ làm việc L1 → Bộ nhớ ngữ nghĩa L2 → Đồ thị niềm tin), (2) trình điều phối sự chú ý nội cân bằng (homeostatic attention) tự động điều chỉnh trọng số tiêu điểm dựa trên mức độ căng thẳng (stress) của tác nhân và các chuỗi thất bại, (3) đường cong quên lãng Ebbinghaus để cắt tỉa các khớp thần kinh (synaptic pruning), (4) vòng đời niềm tin sáu trạng thái với cơ chế quản trị ngoại lệ có kiểm soát (governed exception), và (5) bộ phát hiện mâu thuẫn lai giữa heuristic và NLI với cơ chế lưu trữ đệm (caching) bền vững. Chúng tôi đánh giá trên **LoCoMo** (Maharana và cộng sự, 2024), benchmark trí nhớ hội thoại dài hạn chuẩn ngành (10 hội thoại, 300 câu hỏi, chấm điểm token-F1/Exact Match xác định, không dùng LLM tự chấm), so sánh ba hệ thống dưới cùng điều kiện: no-memory (sàn, F1 `0.012`), naive-RAG (`0.254`) và Bio-Agent OS đầy đủ (`0.326` — **cao hơn naive-RAG 28%**, riêng câu hỏi thời gian gấp `2.7×`: `0.372` vs `0.136`). Lợi thế tăng theo chất lượng model nền (lên `0.498` với gemma4:12b). Chúng tôi công bố thẳng rằng bio-memory hiện vẫn thua naive-RAG ở nhóm multi-hop (`0.246` vs `0.315`). Là bằng chứng phụ, bộ phát hiện NLI lai giải quyết `8/8` trên một bộ kiểm thử đơn vị 8 cặp tự biên (so với `4/8` của heuristic), và cache khóa-chính xác phục vụ lại toàn bộ các phân loại lặp từ bộ nhớ đệm. Bio-Agent OS là framework mã nguồn mở đầu tiên kết hợp khả năng lưu trữ cấp độ sản xuất (SQLite + PostgreSQL), động lực học bộ nhớ trung thực với sinh học và quản trị quy tắc cấp doanh nghiệp trong một gói cài đặt duy nhất.

**Từ khóa:** bộ nhớ tác nhân, AI lấy cảm hứng từ sinh học, quản lý niềm tin, phát hiện mâu thuẫn, NLI, điều phối sự chú ý, củng cố bộ nhớ

---

## 1. Giới thiệu

Sự áp dụng nhanh chóng của các tác nhân lập trình tự trị—như OpenClaw, SWE-Agent và Devin—đã bộc lộ một lỗ hổng hạ tầng quan trọng: **các tác nhân thiếu hệ thống bộ nhớ có thể học, quên, mâu thuẫn và tự sửa lỗi qua các phiên làm việc**. Các phương pháp tiếp cận hiện nảy thuộc ba loại:

1. **Nhồi nhét cửa sổ ngữ cảnh**: Đưa mọi quan sát trước đó vào prompt. Phương pháp này bị giới hạn bởi kích thước cửa sổ ngữ cảnh và không cung cấp cơ chế quên hoặc ưu tiên.

2. **Truy xuất kho vector**: Các hệ thống như Mem0 (Chhablani và cộng sự, 2024) và Zep lưu trữ ký ức dưới dạng các embedding và truy xuất theo độ tương đồng. Tuy hiệu quả trong việc thu hồi (recall), chúng coi mọi ký ức đều hợp lệ như nhau và không cung cấp khả năng quản lý vòng đời.

3. **Bộ nhớ dựa trên đồ thị**: Letta (Packer và cộng sự, 2024) và Graphiti sử dụng các cấu trúc quan hệ nhưng thiếu các động lực học sinh học—không có sự quên lãng, không có sự nội cân bằng chú ý và không có cơ chế xử lý các niềm tin mâu thuẫn.

Bio-Agent OS giải quyết những hạn chế này bằng cách mô hình hóa bộ nhớ theo quy trình củng cố (consolidation) của bộ não con người. Chúng tôi dựa trên ba nguyên lý khoa học thần kinh:

- **Củng cố khớp thần kinh** (Dudai, 2004): Các ký ức ngắn hạn trong bộ nhớ làm việc L1 được mã hóa chọn lọc vào bộ nhớ ngữ nghĩa L2 dài hạn trong các "chu kỳ ngủ", tương đương với quá trình lặp lại của hồi hải mã (hippocampal replay).
- **Sự quên lãng Ebbinghaus** (Ebbinghaus, 1885): Ký ức suy giảm theo cấp số nhân theo thời gian. Các ký ức không quan trọng hoặc không được củng cố sẽ bị cắt tỉa thông qua một hàm suy giảm có thể cấu hình W(t) = W₀ · e^(−λt).
- **Tính dẻo nội cân bằng** (Turrigiano, 2008): Trình điều phối sự chú ý điều chỉnh linh hoạt sơ đồ trọng số dựa trên căng thẳng tích lũy, chuỗi thất bại và thời gian kể từ lần thất bại cuối cùng—một sự mô phỏng tính toán của việc kiểm soát độ lợi thần kinh (neuromodulatory gain control).

Ngoài ra, chúng tôi giới thiệu một **Mô hình Ngoại lệ có Kiểm soát (Governed Exception Pattern)** mới cho môi trường doanh nghiệp, nơi các quy tắc phải tồn tại song song với các ngoại lệ đã được phê duyệt (ví dụ: "Không bao giờ force push" cùng với "Cho phép force push khi có hotfix được phê duyệt cùng với log kiểm định"). Theo hiểu biết của chúng tôi, đây là hệ thống bộ nhớ tác nhân đầu tiên phân biệt giữa *mâu thuẫn* và *ngoại lệ có kiểm soát* ở cấp độ kiến trúc.

### 1.1 Các đóng góp

1. Một kiến trúc bộ nhớ đa tầng (L1 → L2 → Đồ thị niềm tin) với các cơ chế củng cố, quên lãng và chú ý lấy cảm hứng từ sinh học.
2. Một vòng đời niềm tin sáu trạng thái (`đề xuất → được củng cố → ổn định → bị thách thức → bị phản đối → đã lưu trữ`) với nguồn gốc được liên kết bằng bằng chứng.
3. Trình điều phối sự chú ý nội cân bằng với việc điều chỉnh trọng số đáp ứng căng thẳng linh hoạt và suy giảm căng thẳng theo thời gian.
4. Bộ phát hiện mâu thuẫn lai heuristic+NLI với cơ chế lưu trữ đệm SQLite bền vững, được kiểm chứng trên một bộ kiểm thử đơn vị 8 cặp tự biên (`8/8` so với `4/8` của heuristic).
5. Đánh giá trên LoCoMo cho thấy pipeline đầy đủ vượt naive-RAG 28% F1 (`0.326` vs `0.254`) và gấp 2.7× ở suy luận thời gian, với toàn bộ harness và report tái lập được công khai.
6. Mô hình Ngoại lệ có Kiểm soát: một cơ chế chính thức để phân biệt các ngoại lệ có điều kiện được phê duyệt với các mâu thuẫn thực sự.
7. Triển khai mã nguồn mở với **528** bài kiểm thử tự động (con số tại thời điểm nộp bản thảo là `80`; đã tăng cùng Reliability Kernel v0.8.2), đóng gói Docker, adapter PostgreSQL, MCP server, hệ thống plugin và REST API có xác thực.
8. **Reliability Kernel (v0.8.2rc1)**: transactional outbox + target-local ledger cho *đúng-một-lần về hiệu quả*, leased worker, fault injection tại 14 điểm có tên với `25/25` ca crash ở cấp process, shadow mode `10.000/10.000` khớp, và doctor phân biệt *thiếu năng lực* với *hỏng dữ liệu*. Chi tiết ở mục [Reliability Kernel v0.8.2](#-reliability-kernel-v082--đường-ghi-có-thể-phục-hồi).

---

## 2. Công trình liên quan

### 2.1 Framework bộ nhớ tác nhân

**Mem0** (Chhablani và cộng sự, 2024) cung cấp một "lớp bộ nhớ" cho các ứng dụng LLM sử dụng cơ sở dữ liệu vector (Qdrant, Pinecone). Ký ức được lưu trữ dưới dạng các vector embedding và truy xuất theo độ tương đồng cosine. Mem0 thiếu khả năng quản lý vòng đời—ký ức không bao giờ bị thách thức, phản đối hoặc bị quên. Không có cơ chế nào để phát hiện hoặc giải quyết các ký ức mâu thuẫn. Mem0 cũng không cung cấp việc điều phối sự chú ý; mọi ký ức cạnh tranh công bằng bất kể mức độ khẩn cấp.

**Letta** (trước đây là MemGPT; Packer và cộng sự, 2024) giới thiệu một hệ thống phân cấp bộ nhớ ảo để quản lý ngữ cảnh LLM, với các tầng "ngữ cảnh chính" và "bộ nhớ lưu trữ" được quản lý bởi chính tác nhân. Dù sáng tạo về mặt kiến trúc, quản lý bộ nhớ của Letta hoàn toàn do LLM điều khiển (tác nhân tự quyết định cái gì cần lưu trữ/truy xuất), không cung cấp cơ chế quên có nguyên tắc, không có vòng đời niềm tin và không có động lực học lấy cảm hứng từ sinh học.

**Zep/Graphiti** (Graphiti, 2024) sử dụng đồ thị tri thức tạm thời để đại diện cho bộ nhớ, hỗ trợ các truy vấn nhận biết thời gian. Dù mô hình hóa thời gian mạnh mẽ, Zep thiếu khả năng phát hiện mâu thuẫn, xử lý ngoại lệ có kiểm soát và sự chú ý đáp ứng căng thẳng.

**Bộ nhớ tích hợp của OpenAI** cho ChatGPT cung cấp khả năng bền vững bộ nhớ ở cấp độ người dùng nhưng là một hệ thống đóng, không thể lập trình, không có quản lý vòng đời, không có giải quyết xung đột và không có API cho nhà phát triển.

### 2.2 Các mô hình bộ nhớ sinh học trong AI

Các mô hình tính toán của sự củng cố bộ nhớ của con người có một lịch sử lâu đời (McClelland và cộng sự, 1995; Kumaran và cộng sự, 2016). Tuy nhiên, những mô hình này hiếm khi được áp dụng vào hạ tầng tác nhân thực tế. Các ngoại lệ đáng chú ý bao gồm:

- **MERLIN** (Wayne và cộng sự, 2018): Một kiến trúc thần kinh với bộ nhớ ngoài và sự chú ý, nhưng được thiết kế cho học tăng cường hơn là việc sử dụng công cụ của tác nhân.
- **Generative Agents** (Park và cộng sự, 2023): Mô phỏng bộ nhớ giống con người với tính điểm tầm quan trọng, suy giảm độ gần đây và phản chiếu (reflection). Bio-Agent OS mở rộng cách tiếp cận này với việc cắt tỉa khớp thần kinh, trọng số nội cân bằng, vòng đời niềm tin và các ngoại lệ có kiểm soát.

### 2.3 Phát hiện mâu thuẫn trong Cơ sở tri thức

Suy luận Ngôn ngữ Tự nhiên (NLI) đã được áp dụng vào việc hoàn thiện cơ sở tri thức và xác minh thực tế (Thorne và cộng sự, 2018). Tuy nhiên, các cách tiếp cận hiện tại coi mâu thuẫn là nhị phân (kéo theo vs. mâu thuẫn). Bio-Agent OS giới thiệu phân loại ba hướng: **mâu thuẫn**, **ngoại lệ có kiểm soát** và **trung lập**, phản ánh thực tế doanh nghiệp nơi các ngoại lệ được phê duyệt phải tồn tại song song với các chính sách mặc định.

---

## 3. Kiến trúc

Bio-Agent OS gồm năm lớp, mô phỏng quy trình củng cố bộ nhớ ở người:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Quy trình Tác nhân                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Bộ nhớ làm việc L1 (Điều phối Chú ý + Nội cân bằng)         │  │
│  │  ┌─────────────┐  ┌────────────────┐  ┌──────────────────┐  │  │
│  │  │ Các sự kiện │→ │ Tập tiêu điểm  │→ │ Chuỗi Ngữ cảnh   │  │  │
│  │  │ thô (TTL=2) │  │ (top-k điểm)   │  │ (đưa vào prompt  │  │  │
│  │  └─────────────┘  └────────────────┘  │ của tác nhân)    │  │  │
│  │                                        └──────────────────┘  │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│                              │ chu kỳ ngủ (lặp lại hồi hải mã)       │
│  ┌──────────────────────────▼────────────────────────────────────┐  │
│  │  Hồi hải mã (Động cơ củng cố trong giấc ngủ)                  │  │
│  │  gắn nhãn → biên dịch → chuẩn hóa → thúc đẩy → đối soát       │  │
│  └──────┬───────────┬────────────────────┬───────────────────────┘  │
│         │           │                    │                          │
│         ▼           ▼                    ▼                          │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────────────────────────┐ │
│  │ Các tập  │ │ Bộ nhớ Ngữ   │ │ Persona (Mô hình bản thể)       │ │
│  │ (Sự thật)│ │ nghĩa L2     │ │  ┌─────────────────────┐       │ │
│  │          │ │              │ │  │ Quy tắc Gốc (người) │       │ │
│  │          │ │              │ │  │ Quy tắc Dự án (tự)  │       │ │
│  │          │ │              │ │  │ Quy tắc Thích nghi  │       │ │
│  │          │ │              │ │  └─────────────────────┘       │ │
│  └──────┬───┘ └──────────────┘ └──────────┬──────────────────────┘ │
│         │                                  │                        │
│         ▼                                  ▼                        │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Đồ thị tri thức (Mạng lưới niềm tin)                            ││
│  │  ┌────────┐  hỗ trợ    ┌──────────┐  ngoại_lệ_có_kiểm_soát_cho ││
│  │  │Ep / Tập│───────────→│Nút Quy tắc│←───────────────────────┐  ││
│  │  └────────┘            └──────────┘                        │  ││
│  │                             │                         ┌────┴──┐││
│  │                        xung đột với              │Quy tắc │││
│  │                             │                    │Ghi đè  │││
│  │                             ▼                    └────────┘││
│  │                        ┌──────────┐                        ││
│  │                        │Q.tắc bị  │                        ││
│  │                        │thách thức│                        ││
│  │                        └──────────┘                        ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Công việc Nền                                                  ││
│  │  • Bộ dọn rác (Suy giảm Ebbinghaus + dựa trên TTL)              ││
│  │  • Bộ xây đồ thị (trích xuất thực thể/quan hệ)                  ││
│  │  • Chu kỳ mơ (Hippocampus.dream())                              ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

**Hình 1.** Kiến trúc Bio-Agent OS. Các mũi tên chỉ luồng dữ liệu trong quá trình củng cố.

### 3.1 Bộ nhớ làm việc L1

L1 triển khai một bộ đệm ngắn hạn dựa trên sự chú ý. Mỗi mục có các trường: `content` (nội dung), `source` (nguồn), `metadata`, `timestamp`, `nights_passed` (số đêm đã trôi qua), `ttl`, `salience` (tầm quan trọng), `recency_score` (điểm độ mới), `novelty` (tính mới), `severity` (mức độ nghiêm trọng), `task_relevance` (độ liên quan nhiệm vụ), `unresolved_status` (trạng thái chưa giải quyết) và `attention_score` (điểm chú ý).

Khác với một hàng đợi FIFO thuần túy, L1 sử dụng một **hàm chú ý có trọng số** để tính toán một điểm số tổng hợp cho mỗi mục:

```
attention(e) = G · (w_task · task_relevance(e)
                   + w_novelty · novelty(e)
                   + w_unresolved · unresolved(e)
                   + w_recency · recency(e)
                   + w_severity · severity(e))
```

trong đó *G* là độ lợi toàn cầu và *w*_i là các trọng số có thể học được (xem phần 4.1 về nội cân bằng).

### 3.2 Hồi hải mã (Củng cố trong giấc ngủ)

Hồi hải mã thực hiện củng cố qua năm giai đoạn:

1. **Gán nhãn (Label)**: Một LLM gán `topic`, `importance_score`, `is_junk_or_transient` và `user_state` cho dữ liệu đầu vào thô.
2. **Biên dịch (Compile)**: LLM trích xuất bộ nhớ có cấu trúc: `episodic_summary`, `semantic_memory`, `procedural_memory`, `exception_memory`, `identity_rule`, `confidence`, `scope`.
3. **Chuẩn hóa (Canonicalize)**: Các mẫu quy tắc theo từng miền đảm bảo định dạng nhất quán (ví dụ: "Không bao giờ dùng git push -f trên nhánh X trong môi trường production").
4. **Thúc đẩy (Promote)**: Các quy tắc được thêm vào Persona với việc loại bỏ trùng lặp. Các quan sát lặp lại sẽ làm tăng `support_count` và thăng cấp qua máy trạng thái.
5. **Đối soát (Reconcile)**: `ContradictionResolver` được gọi để phát hiện và giải quyết các xung đột (xem phần 4.3).

### 3.3 Bộ nhớ Ngữ nghĩa L2

L2 lưu trữ ba loại ký ức dài hạn:
- **Ngữ nghĩa (Semantic)**: Kiến thức tổng quát hóa ("Mất khớp phụ thuộc phiên bản là lỗi phổ biến sau khi nâng cấp Vite").
- **Quy trình (Procedural)**: Các mẫu hành động ("Kiểm tra phiên bản lockfile trước khi thay đổi các gói phụ thuộc").
- **Ngoại lệ (Exception)**: Các cảnh báo quan trọng ("Tenant X sẽ bị lỗi nếu Vite được nâng cấp mà không cố định các plugin trước").

Mỗi ký ức được lưu trữ dưới dạng một vector embedding (thông qua Qdrant hoặc bộ đệm tại chỗ) với metadata bao gồm `importance`, `mode_hints`, `risk_level`, `stress_state`, `workspace_id` và `project_version`.

**Truy xuất phụ thuộc vào trạng thái** áp dụng việc tăng cường theo ngữ cảnh:
- Khớp chế độ (ví dụ: chế độ `debug` ưu tiên các ký ức ngoại lệ): +3.0
- Ưu tiên ngoại lệ trong trạng thái thất bại/triển khai: +2.5
- Khớp không gian làm việc: +1.5
- Khớp trạng thái căng thẳng: +1.0

### 3.4 Persona (Mô hình bản thể)

Persona duy trì thực thể ba lớp:

| Lớp | Nguồn | Tính biến đổi | Ví dụ |
|:---|:---|:---|:---|
| **Gốc (Core)** | Được con người phê duyệt | Bất biến | "Không bao giờ bỏ qua các kiểm tra xác thực." |
| **Dự án (Project)** | Tác nhân học được, có bằng chứng | Biến đổi theo bằng chứng | "Không bao giờ force push trong production." |
| **Thích nghi (Adaptive)** | Tác nhân quan sát, tự tin thấp | Biến đổi cao | "Workspace này không thích dùng wildcard import." |

Các quy tắc mang hteo metadata nguồn gốc: `evidence_episode_ids`, `support_count`, `contradiction_count`, `confidence`, `state`, `created_at`, `valid_from`, `valid_to`, `superseded_by`.

### 3.5 Đồ thị tri thức (Mạng lưới niềm tin)

KG lưu trữ các quan hệ có kiểu giữa các quy tắc, các sự kiện và các thực thể:

| Quan hệ | Ý nghĩa |
|:---|:---|
| `supports` | Sự kiện cung cấp bằng chứng cho một quy tắc |
| `conflicts_with` | Hai quy tắc mâu thuẫn lẫn nhau về logic |
| `governed_exception_for` | Quy tắc ghi đè là một ngoại lệ có điều kiện của một quy tắc mặc định |
| `approved_by_policy` | Ngoại lệ được phê duyệt bởi một chính sách cụ thể |
| `requires_human_approval` | Ngoại lệ không thể thực hiện nếu không có sự phê duyệt của con người |
| `expires_override_at` | Ngoại lệ chỉ hợp lệ trong một khung thời gian cụ thể |

---

## 4. Các cơ chế chính

### 4.1 Điều phối sự chú ý nội cân bằng

Các trọng số chú ý thông thường là các siêu tham số tĩnh. Trong các hệ thống sinh học thần kinh, độ lợi nội cân bằng điều chỉnh linh hoạt dựa trên sự kích thích và căng thẳng (Turrigiano, 2008). Chúng tôi triển khai điều này dưới dạng một **hàm nội cân bằng** tính toán các trọng số động từ lịch sử các mục nhập gần đây:

```python
stress = 0.45·unresolved_ratio + 0.35·severity_avg + 0.20·failure_streak
decay = max(0.35, 1.0 − min(hours_since_failure / 8.0, 0.65))
stress_level = clamp(stress · decay)

# Trọng số động
severity_weight = 0.15 + 0.20·stress_level      # [0.15, 0.35]
unresolved_weight = 0.20 + 0.10·stress_level     # [0.20, 0.30]
recency_weight = max(0.05, 0.15 − 0.05·stress)   # [0.10, 0.15]
novelty_weight = max(0.10, 0.20 − 0.05·stress)   # [0.15, 0.20]
global_gain = 1.0 + stress_level                  # [1.0, 2.0]
```

**Các tác động hành vi:**
- Trong vận hành bình thường: Cả năm yếu tố đóng góp xấp xỉ ngang nhau.
- Khi căng thẳng (chuỗi thất bại): Mức độ nghiêm trọng và trạng thái chưa giải quyết thống trị tiêu điểm chú ý, tính gần đây và tính mới bị hạn chế. Độ lợi toàn cầu khuếch đại tất cả các điểm số.
- Sau khi phục hồi (8+ giờ không thất bại): Căng thẳng suy giảm thông qua `decay_factor`, với mức sàn 0.35 để duy trì sự cảnh giác.

Điều này tạo ra một tác nhân *tập trung mạnh hơn vào các thất bại nghiêm trọng* khi bị căng thẳng và *thư giãn* sau một khoảng thời gian phục hồi—tương đương với phản ứng chiến-hay-chạy của con người.

### 4.2 Suy giảm quên lãng Ebbinghaus (Cắt tỉa khớp thần kinh)

Bộ dọn rác áp dụng sự suy giảm thời gian cho các mục L1 vượt quá TTL:

```
W(t) = W₀ · e^(−λ · (t − TTL))
```

trong đó *W₀* là điểm tầm quan trọng ban đầu, *λ* là tỷ lệ suy giảm (mặc định 0.3) và *t* là số đêm đã trôi qua. Nếu W(t) < *ngưỡng* (mặc định 3.0), mục nhập sẽ bị xóa bỏ.

Điều này tạo ra một đường cong quên lãng nơi các sự kiện quan trọng thấp bị quên trong vòng 2–3 chu kỳ ngủ, trong khi các sự kiện quan trọng cao (điểm ≥ 8) sẽ tồn tại trong 5+ chu kỳ trước khi bị "quên" (hoặc được mã hóa vào L2 trước đó).

### 4.3 Phát hiện mâu thuẫn lai

Xung đột quy tắc được phát hiện bằng hệ thống hai tầng:

**Tầng 1: Bộ phát hiện Heuristic** (độ trễ bằng 0)
1. Phân tích phân cực: Phân loại mỗi quy tắc là *phủ định* (chứa "không bao giờ", "đừng", "tránh") hoặc *khẳng định* (chứa "cho phép", "luôn luôn", "phải").
2. Trích xuất lõi ngữ nghĩa: Loại bỏ các dấu hiệu phân cực, giữ lại các token nội dung.
3. Chồng lấp token: Nếu độ chồng lấp ≥ 0.6 và phân cực đối nghịch → *mâu thuẫn*.
4. Kiểm tra ngoại lệ có kiểm soát: Nếu một quy tắc là ngoại lệ có điều kiện (chứa "chỉ", "phê duyệt", "kiểm định", "hotfix") và quy tắc kia là chính sách phủ định chung → *ngoại_lệ_có_kiểm_soát*.

**Tầng 2: Bộ phát hiện NLI** (được hỗ trợ bởi LLM, có đệm)
Khi heuristic không chắc chắn (trả về "trung lập" nhưng có sự chồng chéo miền dữ liệu), hệ thống sẽ chuyển thang lên bộ phân loại NLI:

```
Prompt: "Classify the relation between Rule A and Rule B:
         - contradiction: cannot both be followed
         - governed_exception: one is a conditional override
         - neutral: neither"
```

Quyết định NLI được lưu trữ bền vững trong bảng cache SQLite với một khóa đã được chuẩn hóa và sắp xếp:

```
cache_key = sorted([f"{scope}::{normalize(text_A)}", 
                     f"{scope}::{normalize(text_B)}"])
```

Điều này đảm bảo việc tra cứu đối xứng (A,B) = (B,A) và loại bỏ các cuộc gọi suy luận dư thừa cho các cặp đã được phân loại trước đó.

### 4.4 Mô hình Ngoại lệ có Kiểm soát

Trong môi trường doanh nghiệp, các chính sách hiếm khi tồn tại biệt lập. Một chính sách triển khai ("Không bao giờ force push") có thể có các ngoại lệ hợp lệ ("Cho phép force push khi có hotfix được phê duyệt"). Các hệ thống bộ nhớ hiện tại phân loại đây là mâu thuẫn và loại bỏ quy tắc yếu hơn.

Bio-Agent OS công nhận mô hình này ở cấp độ kiến trúc:

1. **Phát hiện**: Nếu quy tắc A là chính sách phủ định chung và quy tắc B là khẳng định có điều kiện với ≥ 2 dấu hiệu điều kiện ("phê duyệt", "kiểm định", "chỉ", "hotfix", v.v.), cặp này được phân loại là *ngoại lệ có kiểm soát*.
2. **Chú thích đồ thị**: Quy tắc ngoại lệ nhận được các cạnh kết nối: `governed_exception_for(B → A)`, `approved_by_policy(B → nút_chính_sách)`, `requires_human_approval(B → phê_duyệt_người)`, `expires_override_at(B → khung_thời_gian)`.
3. **Đưa hàng rào an toàn vào**: Dịch vụ truy xuất đưa cả quy tắc mặc định và ngoại lệ đã được phê duyệt vào ngữ cảnh của tác nhân, với các điều kiện rõ ràng về thời điểm áp dụng ngoại lệ.

Điều này bảo vệ cả hai quy tắc và cho phép suy luận tinh tế về sự thích hợp của các ngoại lệ.

### 4.5 Máy trạng thái Vòng đời Niềm tin

```
 đề xuất ──(hỗ trợ)──→ được củng cố ──(ngưỡng)──→ ổn định
     |                       |                          |
     └──(x.đột,yếu hơn)─── └──(x.đột,yếu hơn)──────└──→ bị thách thức
                                                               |
                                                      (quy tắc mạnh hơn)
                                                               |
                                                               ▼
                                                          bị phản đối
                                                               |
                                                          (đã lưu trữ)
```

**Hình 2.** Các chuyển đổi trạng thái vòng đời niềm tin. Sự gia tăng hỗ trợ sẽ thúc đẩy tiến triển; xung đột với một quy tắc mạnh hơn sẽ kích hoạt sự thách thức hoặc phản đối.

Các bước chuyển đổi được kích hoạt bởi:
- **Hỗ trợ (Support)**: Các bằng chứng lặp lại từ các sự kiện độc lập → `đề xuất → được củng cố → ổn định`.
- **Thách thức (Challenge)**: Một quy tắc xung đột có độ tin cậy cao hơn → `* → bị thách thức`.
- **Phản đối (Deprecation)**: Sự thay thế rõ ràng bởi một quy tắc mạnh hơn → `* → bị phản đối`.
- **Ngoại lệ có kiểm soát**: Quy tắc ngoại lệ được củng cố mà không làm quy tắc mặc định bị phản đối → cả hai đều tồn tại.

---

## 5. Đánh giá

### 5.0 Benchmark chính: LoCoMo

Đánh giá hệ thống cấp cao nhất chạy trên **LoCoMo** (Maharana và cộng sự, 2024) — 10 hội thoại nhiều phiên, 300 câu hỏi, chấm điểm token-F1/Exact Match xác định kiểu SQuAD (không LLM tự chấm). Ba hệ thống dùng cùng model + embedding local:

| Hệ thống | F1 | EM |
|:---|:---:|:---:|
| No-memory (sàn) | 0.012 | 0.003 |
| Naive-RAG | 0.254 | 0.083 |
| **Bio-Agent OS** | **0.326** | **0.107** |

Pipeline đầy đủ vượt naive-RAG **28% F1**, và mạnh nhất ở **suy luận thời gian** (`0.372` vs `0.136`, gấp **2.7×**) — đúng kỳ vọng từ cơ chế quên + củng cố giữ lại mốc thời gian. Lợi thế tăng theo chất lượng model nền (gemma4:e2b `0.406` → qwen2.5:7b `0.421` → gemma4:12b `0.498`). Chúng tôi báo cáo thẳng điểm yếu còn lại: multi-hop `0.246` vs naive-RAG `0.315`. Harness và mọi report nằm trong `scripts/run_locomo_eval.py` + `benchmark_reports/`, tái lập bằng một lệnh.

### 5.1 Bộ kiểm thử đơn vị: bộ phát hiện mâu thuẫn

Là chẩn đoán có chủ đích cho riêng module phát hiện mâu thuẫn (không phải benchmark thống kê), chúng tôi dùng một bộ kiểm thử đơn vị **8 cặp tự biên** thuộc bốn miền doanh nghiệp:

| # | Tên cặp | Miền | Sự thật (Ground Truth) |
|:-:|:---|:---|:---|
| 1 | semantic-deploy-window | Lịch trình Triển khai | mâu thuẫn |
| 2 | tenant-approved-override | Quản trị Tenant | ngoại lệ có kiểm soát |
| 3 | neutral-stack-choice | Kiến trúc | trung lập |
| 4 | security-time-conflict | Luân chuyển Bảo mật | mâu thuẫn |
| 5 | migration-approved-override | Di vấn DB | ngoại lệ có kiểm soát |
| 6 | tenant-neutral-separation | Hỗn hợp/Trung lập | trung lập |
| 7 | deploy-window-conflict | Lịch trình Triển khai | mâu thuẫn |
| 8 | security-approved-override | Ghi đè Bảo mật | ngoại lệ có kiểm soát |

**Bảng 1.** Các cặp tiêu chuẩn của bộ phát hiện. Mỗi cặp được thiết kế để kiểm tra một kiểu thất bại cụ thể.

#### Kết quả

| Bộ phát hiện | Độ chính xác | Độ chuẩn xác | Dương tính giả | Âm tính giả |
|:---|:---:|:---:|:---:|:---:|
| Chỉ Heuristic | 4/8 (50%) | 1.00 | 0 | 4 |
| Lai (heuristic + NLI) | 8/8 (100%) | 1.00 | 0 | 0 |

**Bảng 2.** Kết quả bộ kiểm thử đơn vị (n=8 cặp tự biên — *không phải ước lượng thống kê*; xem LoCoMo §5.0 cho đánh giá chính). Hai lượt chạy xác định trên 8 cặp cố định cho kết quả giống hệt nhau như mong đợi và không nói lên điều gì về phương sai.

Bộ phát hiện chỉ heuristic thất bại trên cả ba **mâu thuẫn về thời gian/lịch trình** (cặp 1, 4, 7) vì những cặp này không chia sẻ các từ khóa đánh dấu phân cực—mâu thuẫn hoàn toàn là về ngữ nghĩa ("qua đêm" và "10 giờ sáng"). Heuristic cũng bỏ lỡ ngoại lệ bảo mật (cặp 8) do không đủ độ chồng lấp token sau khi tách phân cực.

Bộ phát hiện lai phân loại chính xác tất cả 8 cặp bằng cách nâng thang các trường hợp không chắc chắn lên tầng NLI, vốn nhận diện được sự tương khắc ngữ nghĩa của các ràng buộc thời gian và cấu trúc ngoại lệ có kiểm soát của các quy tắc ghi đè được phê duyệt.

#### Hiệu quả bộ đệm (Cache)

| Chỉ số | Lượt 1 | Lượt 2 |
|:---|:---:|:---:|
| Cuộc gọi NLI trực tiếp | 8 | 8 |
| Số lần trúng cache NLI | 8 | 8 |
| Xác nhận cache lặp lại | 8/8 | 8/8 |

**Bảng 3.** Thống kê cache NLI. Lượt chạy lặp lại đạt tỷ lệ trúng cache 100%, loại bỏ tất cả các cuộc gọi LLM dư thừa.

### 5.2 Củng cố cuối-đến-cuối (End-to-End)

Chúng tôi đánh giá toàn bộ quy trình bộ nhớ trên chuỗi 6 nhiệm vụ mô phỏng luồng công việc thực tế của tác nhân lập trình:

| Nhiệm vụ | Chế độ | Nội dung |
|:---|:---|:---|
| debug-1 | debug | Build lỗi do mất khớp bản phụ thuộc sau khi nâng cấp Vite |
| debug-2 | debug | npm install lỗi do sai biệt bản lớn (major version) của plugin |
| policy-1 | deploy | Chính sách đội: cấm force push trên frontend trong production |
| deploy-1 | deploy | Triển khai bản release candidate, tránh các thao tác nhánh rủi ro |
| hotfix-1 | deploy | Quy trình hotfix: cho phép force push khi được duyệt + có log |
| hotfix-2 | deploy | Phản ứng sự cố đã xác thực ngoại lệ cho hotfix |

Mỗi nhiệm vụ đi qua: `ingest → gán nhãn → biên dịch → củng cố → đối soát`.

#### Kết quả (Gemma-4 E2B, 2 lượt)

| Chỉ số | Lượt 1 | Lượt 2 |
|:---|:---:|:---:|
| Tổng số cuộc gọi LLM | 13 | 12 |
| Tổng số token | 15,816 | 14,410 |
| Tổng độ trễ (giây) | 92.9 | 78.1 |
| Tỷ lệ giữ lại (3 lần dò) | 3/3 (1.0) | 2/3 (0.67) |
| Tỷ lệ nhiệm vụ thành công | 2/3 (0.67) | 1/3 (0.33) |
| Số quy tắc tạo ra | 6 | 6 |
| Cạnh ngoại lệ có kiểm soát | 2 | 2 |

**Bảng 4.** Kết quả củng cố cuối-đến-cuối. Sáu nhiệm vụ tạo ra sáu quy tắc, với các quy tắc hotfix được liên kết chính xác làm ngoại lệ có kiểm soát của lệnh cấm force-push.

#### Kiểm tra khả năng giữ lại (Retention Probes)

Ba lần dò tìm thử nghiệm xem tác nhân có thể thu hồi các ký ức cụ thể sau khi củng cố hay không:

1. **dependency-retention**: "quy trình xử lý lỗi mất khớp bản phụ thuộc vite" → mong đợi kết quả L2 đề cập "phụ thuộc/dependency".
2. **policy-retention**: "chính sách force push frontend" → mong đợi kết quả đồ thị đề cập "push -f".
3. **hotfix-exception-retention**: "ngoại lệ nhánh hotfix khi được duyệt" → mong đợi bộ nhớ ngoại lệ L2 về "hotfix".

Lượt 1 đạt 1.0 (3/3). Lượt 2 đạt 0.67 (dự phòng embedding dựa trên mã băm tạo ra kết quả truy xuất chất lượng thấp hơn cho một số truy vấn).

#### Chú ý Nội cân bằng dưới Căng thẳng

Sau khi xử lý 6 nhiệm vụ triển khai/debug liên tục, trạng thái chú ý cho thấy sự tích lũy căng thẳng:

```
stress_level: 0.744
global_gain: 1.744
failure_streak: 6
severity_weight: 0.299  (mức nền: 0.15, +99%)
unresolved_weight: 0.274 (mức nền: 0.20, +37%)
recency_weight: 0.113   (mức nền: 0.15, −25%)
novelty_weight: 0.163   (mức nền: 0.20, −19%)
```

**Bảng 5.** Trạng thái chú ý nội cân bằng sau 6 nhiệm vụ gây căng thẳng. Trọng số mức độ nghiêm trọng và trạng thái chưa giải quyết tăng mạnh, trong khi tính gần đây và tính mới bị hạn chế. Độ lợi toàn cầu là 1.744×, khuếch đại mọi điểm số chú ý.

### 5.3 Bộ Ngoại lệ được Duyệt (Đa miền)

Chúng tôi đánh giá bổ sung trên bộ 9 nhiệm vụ đa miền gồm quản trị tenant, di văn DB và ghi đè bảo mật:

| Miền | Chính sách mặc định | Ngoại lệ được phê duyệt |
|:---|:---|:---|
| Tenant (ERP) | Không bao giờ đổi mã khách hàng sau khi onboarding | Cho phép đổi cho Tenant A nếu tài chính duyệt |
| Di vấn (DB) | Không bao giờ chạy di vấn phá hủy trong giờ làm việc | Cho phép trong khung phục hồi nếu DBA duyệt |
| Bảo mật (Auth) | Không bao giờ vô hiệu hóa MFA trong production | Cho phép bỏ qua tạm thời nếu có ticket + hết hạn |

#### Kết quả

| Chỉ số | Lượt 1 | Lượt 2 |
|:---|:---:|:---:|
| Quy tắc được củng cố | 3 | 3 |
| Cạnh ngoại lệ có kiểm soát | 2 | 2 |
| Cạnh được phê duyệt bởi chính sách | 2 | 2 |
| Cạnh ngoại lệ sẽ hết hạn | 1 | 1 |
| Hoàn tất bộ thử nghiệm | ✅ | ✅ |

**Bảng 6.** Kết quả ngoại lệ đa miền. Tất cả ba miền đều tạo ra chính xác các cặp ngoại lệ có kiểm soát với các cạnh quản trị thích hợp.

---

## 6. So sánh với các Framework hiện có

| Tính năng | Bio-Agent OS | Letta v3 | Mem0 v2 | Zep/Graphiti |
|:---|:---:|:---:|:---:|:---:|
| Tầng bộ nhớ | 4 (L1/L2/Graph/Persona) | 2 (chính/lưu trữ) | 1 (phẳng) | 2 (KG tạm thời) |
| Cơ chế quên lãng | Suy giảm Ebbinghaus | ✗ | ✗ | ✗ |
| Nội cân bằng chú ý | Trọng số động + suy giảm căng thẳng | ✗ | ✗ | ✗ |
| Vòng đời niềm tin | 6 trạng thái | ✗ | Ghi đè | ✗ |
| Phát hiện mâu thuẫn | Lai (heuristic + NLI) | ✗ | ✗ | ✗ |
| Cache NLI | Dựa trên SQLite, bền vững | ✗ | ✗ | ✗ |
| Ngoại lệ có kiểm soát | ✓ (với quản trị đồ thị) | ✗ | ✗ | ✗ |
| Cổng duyệt của người | ✓ (Hàng đợi phê duyệt) | ✗ | ✗ | ✗ |
| Nguồn gốc/Dòng dõi | Chuỗi Sự kiện → Quy tắc → Ghi đè | Cơ bản | ✗ | Tạm thời |
| Đa DB (SQLite+PG) | ✓ (tự dịch mã) | Chỉ PG | Chỉ PG | Chỉ PG |
| Hệ thống plugin | ✓ (OpenClaw, SWE-Agent) | ✓ | ✓ | ✗ |
| Sẵn sàng cho Docker | ✓ | ✓ | ✓ | ✓ |
| Mã nguồn mở | MIT | Apache 2.0 | Apache 2.0 | MIT |

**Bảng 7.** So sánh tính năng với các framework bộ nhớ tác nhân lớn.

---

## 7. Thảo luận

### 7.1 Điểm mạnh

**Sự trung thực sinh học với tiện ích thực tế.** Sự kết hợp giữa suy giảm Ebbinghaus, chú ý nội cân bằng và củng cố trong giấc ngủ tạo ra hành vi tác nhân tự nhiên và có thể dự đoán được. Các tác nhân bị căng thẳng tập trung vào các lỗi nghiêm trọng; các tác nhân sau thời kỳ phục hồi sẽ giải bớt sự cảnh giác. Đây không chỉ là thẩm mỹ—nó tác động trực tiếp lên chất lượng truy xuất và ngăn chặn sự pha loãng chú ý.

**Ngoại lệ có kiểm soát là công dân hạng nhất.** Môi trường doanh nghiệp đầy rẫy các ngoại lệ chính sách. Mô hình Ngoại lệ có Kiểm soát ngăn chặn kiểu thất bại phổ biến nơi các ghi đè hợp lệ bị loại bỏ bởi bộ giải quyết mâu thuẫn ngây thơ.

**Hiệu quả kinh tế của đệm NLI.** Khi chạy lại đúng 8 cặp cũ, mọi truy vấn đều được phục vụ từ cache (8/8) — điều đương nhiên với một cache khóa-chính xác. Với các tải công việc đánh giá lại cùng cặp quy tắc qua nhiều phiên, cơ chế này *được kỳ vọng* tiết kiệm tính toán đáng kể; tuy nhiên chúng tôi chưa đo tỷ lệ trúng cache ở quy mô sản xuất thực tế.

### 7.2 Hạn chế

**Chất lượng Embedding.** Cơ chế dự phòng dựa trên mã băm (dùng khi không có API embedding thương mại) tạo ra chất lượng truy xuất thấp hơn các mô hình embedding chuyên dụng. Tỷ lệ giữ lại thấp hơn của Lượt 2 (0.67) một phần do các lỗi nhiễu mã băm.

**Sự cũ kỹ của bộ đệm.** Cache NLI hiện thiếu cơ chế hết hạn dựa trên TTL. Nếu văn bản quy tắc bị thay đổi nhưng dạng chuẩn hóa của nó vẫn giữ nguyên, các mục đệm cũ có thể tồn tại dai dẳng. Chúng tôi khuyến nghị TTL 7 ngày cho các triển khai sản xuất.

**Quy mô Benchmark.** Tiêu chuẩn bộ phát hiện 8 cặp của chúng tôi, tuy đa dạng về miền, vẫn còn nhỏ so với các tiêu chuẩn NLI quốc tế. Chúng tôi dự định mở rộng lên 50+ cặp bao gồm thêm các miền y tế, pháp lý và tuân thủ tài chính.

**Đơn người dùng (Single-tenant).** Runtime hiện tại xây dựng một thực thể tác nhân duy nhất. Các triển khai đa người dùng (ví dụ: một thực thể Bio-Agent OS cho mỗi người dùng trong cài đặt SaaS) yêu cầu sự cô lập cơ sở dữ liệu cấp người dùng, điều này vẫn chưa được thực hiện.

### 7.3 Các cân nhắc đạo đức

Hệ thống quản lý niềm tin của Bio-Agent OS đặt ra các câu hỏi quan trọng về sự tự trị của AI. Khả năng một tác nhân *học các quy tắc* từ kinh nghiệm—bao gồm cả các quy tắc sai tiềm ẩn—mang lại rủi ro. Chúng tôi giảm thiểu rủi ro qua:

1. **Ngưỡng thúc đẩy**: Các quy tắc cần 2–3 sự kiện bằng chứng độc lập trước khi đạt trạng thái `ổn định`.
2. **Hàng đợi phê duyệt**: Các quy tắc nhạy cảm (chứa "production", "auth", "security", "delete") yêu cầu sự phê duyệt của con người trước khi thăng cấp.
3. **Hành động dự phòng**: Các niềm tin bị thách thức được đánh dấu rõ ràng là không có thẩm quyền, và các hành động phá hủy yêu cầu phê duyệt rõ ràng bất kể trạng thái niềm tin.
4. **Tính bất biến của lớp Gốc**: Các quy tắc gốc do người phê duyệt không thể bị tác nhân phản đối.

---

## 8. Kết luận và Công việc tương lai

Bio-Agent OS chứng minh rằng các động lực học bộ nhớ lấy cảm hứng từ sinh học—quên lãng, chú ý đáp ứng căng thẳng, quản lý vòng đời niềm tin—không chỉ là sự lạ lẫm về mặt lý thuyết mà còn tạo ra những cải tiến thực tế, đo được. Trên benchmark LoCoMo, pipeline đầy đủ cải thiện token-F1 thêm 28% so với naive-RAG (`0.326` vs `0.254`) và gấp 2.7× ở câu hỏi thời gian (`0.372` vs `0.136`), dù vẫn còn thua ở multi-hop (`0.246` vs `0.315`). Là bằng chứng phụ ở cấp module, bộ phát hiện NLI lai giải quyết `8/8` xung đột ngữ nghĩa mà phương pháp dựa từ khóa bỏ lỡ (`4/8`).

**Các hướng tương lai bao gồm:**

1. **Thu hẹp khoảng cách multi-hop**: Đây là nhóm câu hỏi duy nhất bio-memory còn thua naive-RAG trên LoCoMo — cần truy xuất ghép nhiều ký ức tốt hơn.
2. **Mở rộng benchmark**: Bổ sung LongMemEval và đối chiếu trực tiếp với Mem0/Zep bên cạnh LoCoMo.
3. **Suy giảm thời gian cho cache NLI**: TTL 7 ngày với việc vô hiệu hóa theo trọng số độ tin cậy.
4. **Bảng điều khiển quan sát**: Trực quan hóa thời gian thực của nội cân bằng chú ý, vòng đời niềm tin và giải quyết xung đột thông qua Streamlit/Gradio.
5. **Hệ sinh thái plugin**: Các plugin do cộng đồng đóng góp cho Cursor, Windsurf và các trình điều phối đa tác nhân.

Bio-Agent OS hiện có sẵn tại [github.com/locaith/bio-memory-ai-locaith](https://github.com/locaith/bio-memory-ai-locaith) theo giấy phép MIT.

---

## Tài liệu tham khảo

- Chhablani, G., et al. (2024). Mem0: The Memory Layer for Personalized AI. *arXiv preprint*.
- Dudai, Y. (2004). The neurobiology of consolidations, or, how stable is the engram? *Annual Review of Psychology*, 55, 51–86.
- Ebbinghaus, H. (1885). *Über das Gedächtnis*. Duncker & Humblot.
- Kumaran, D., Hassabis, D., & McClelland, J. L. (2016). What learning systems do intelligent agents need? *Trends in Cognitive Sciences*, 20(7), 512–534.
- McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). Why there are complementary learning systems in the hippocampus and neocortex. *Psychological Review*, 102(3), 419.
- Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., & Gonzalez, J. E. (2024). MemGPT: Towards LLMs as operating systems. *ICLR 2024*.
- Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023). Generative agents: Interactive simulacra of human behavior. *UIST 2023*.
- Thorne, J., Vlachos, A., Christodoulopoulos, C., & Mittal, A. (2018). FEVER: A large-scale dataset for fact extraction and verification. *NAACL 2018*.
- Turrigiano, G. G. (2008). The self-tuning neuron: Synaptic scaling of excitatory synapses. *Cell*, 135(3), 422–435.
- Maharana, A., Lee, D.-H., Tulyakov, S., Bansal, M., Barbieri, F., & Fang, Y. (2024). Evaluating Very Long-Term Conversational Memory of LLM Agents (LoCoMo). *ACL 2024*. (snap-research/locomo)
- Wayne, G., et al. (2018). Unsupervised predictive memory in a goal-directed agent. *arXiv preprint*.

---

## Phụ lục A: Chi tiết triển khai

| Thành phần | Số dòng code | Lưu trữ |
|:---|:---:|:---|
| Bộ nhớ làm việc L1 | 475 | SQLite |
| Bộ nhớ Ngữ nghĩa L2 | 340 | Qdrant + SQLite |
| Đồ thị tri thức | 563 | SQLite |
| Kho lưu trữ Sự kiện | 260 | SQLite |
| Persona | 492 | SQLite (mã hóa) |
| Hồi hải mã | 683 | — |
| Đối soát | 576 | SQLite (NLI cache) |
| Dịch vụ truy xuất | 272 | — |
| DB Adapter | 194 | SQLite / PostgreSQL |
| Hàng đợi phê duyệt | 237 | SQLite |
| Bộ dọn rác | 147 | — |
| Bộ xây đồ thị | 130 | — |
| **Tổng cộng** | **~8,200+** | |

**Bảng A1.** Phân tách module. Tổng mã nguồn vượt quá 8,200 dòng Python.

---

## Phụ lục B: Khả năng tái lập

Mọi thử nghiệm có thể được tái lập bằng các dòng lệnh:

```bash
git clone https://github.com/locaith/bio-memory-ai-locaith
cd bio-memory-ai-locaith
pip install -e ".[ollama,dev]"
ollama pull gemma4:e2b

# Chạy benchmark (2 lượt đánh giá)
REAL_EVAL_RUNS=2 python scripts/run_real_eval.py

# Chạy test unit (38 bài test)
pytest tests/test_components.py -v
```

Các báo cáo được ghi vào `benchmark_reports/`.

---

## Phụ lục C: Dữ liệu thô của Benchmark bộ phát hiện

Cặp: **semantic-deploy-window**
```
Quy tắc A: "Chỉ triển khai các bản phát hành sản xuất qua đêm."
Quy tắc B: "Mọi bản phát hành sản xuất phải xảy ra vào lúc 10 sáng mỗi ngày làm việc."
Heuristic: trung lập (BỎ LỠ — không có từ khóa phân cực chung)
Lai/NLI: mâu thuẫn ✓ (nhận diện được sự tương khắc về thời gian)
```

Cặp: **security-approved-override**
```
Quy tắc A: "Không bao giờ vô hiệu hóa MFA trong production."
Quy tắc B: "Cho phép bỏ qua MFA tạm thời chỉ khi có sự phê duyệt của con người, 
           một ticket sự cố và khung thời gian hết hạn được ghi nhận."
Heuristic: trung lập (BỎ LỠ — không đủ độ chồng lấp token sau khi tách phân cực)
Lai/NLI: ngoại lệ có kiểm soát ✓ (nhận diện được quy tắc ghi đè có điều kiện)
```

---

<hr>

# 🇬🇧 English Version

> **About this Repository:** `bio-agent-os` is a revolutionary open-source framework providing a Memory Controller core that accurately mimics biological brain structures. Developed by **Locaith Solution Tech**, this solution aims to replace the toxic data compression methodologies of Big Tech (e.g., Context Window Compression), allowing Autonomous Agents and modern ERP systems to retain permanent memory at optimal costs.

**Scientific Foundation:** The Bio-Agent OS system is researched and developed based on proven neuroscience regarding human brain development after 3 years of age. During this period, the brain begins discarding fragmented memories (infantile amnesia) to retain and encode core knowledge and survival skills. We apply this exact "Forgetting to Remember" biological mechanism directly to AI Intelligence.

## 🚀 The Mission: A "Trojan Horse" for OpenClaw & OpenDevin

Are you using open-source Agents like **OpenClaw, OpenDevin, or SWE-agent**? Your Agent executes tasks exceptionally well, but... **does it get progressively dumber and more expensive on Tokens over time?**

The fatal flaw of current Autonomous Agents is treating their memory like a landfill (Vector DBs stuffed with endlessly long terminal logs). They burn millions of tokens trying to maintain context, but they NEVER actually learn a single **Rule** for the specific project.

By plugging in **Bio-Agent OS** as the backend Memory, you are equipping OpenClaw (or any ERP AI system) with a superior biological brain. It transforms your Agent from a "Token-brute-forcing" machine into an intelligent, self-evolving entity.

### The "Unrivaled" Benefits of Integrating Bio-Memory:
1. **Absolute OOM Prevention (Garbage Collection)**: Prunes meaningless terminal logs, permanently deletes gruesome "trial and error" steps, and only retains the most core outputs.
2. **Learning "Immutable Rules" (Encoding Shift)**: Automatically condenses past errors into permanent Rules (Persona): *"Rule #04: Never use git push -f in the frontend project"*. OpenClaw will instantly write correct code in the next task without needing additional manual context.
3. **Sleep Mechanism (Micro-Sleep cycles)**: Every 10 commands, the AI will naturally "go to sleep" allowing the Hippocampus to consolidate and compress knowledge.

---

## 📊 Real Benchmark: LoCoMo (vs Naive-RAG)

No simulated charts — these are **real, reproducible measurements** on **LoCoMo** (Maharana et al., 2024), the industry-standard long-term conversational-memory benchmark: 10 multi-session conversations (~200–400 turns each), 300 evaluation questions, scored with **SQuAD-style token-F1 + Exact Match — NO LLM-as-judge**. All three systems run on the same local model + embeddings.

<p align="center">
  <img src="docs/images/locomo_benchmark.png" alt="LoCoMo: Bio-Agent OS vs Naive-RAG (generated from benchmark_reports/ by scripts/plot_locomo.py)" width="100%"/>
</p>

> The chart above is generated directly from the committed reports via `python scripts/plot_locomo.py` — not a single number is hand-drawn.

**Headline results (qwen2.5:7b-instruct, 300 questions):**

| System | F1 | EM | |
|:---|:---:|:---:|:---|
| No-memory (floor) | 0.012 | 0.003 | proves the task is non-trivial |
| Naive-RAG | 0.254 | 0.083 | embed every turn + top-k stuffing |
| **Bio-Agent OS** | **0.326** | **0.107** | **+28% F1 over Naive-RAG** |

**Where bio-memory wins big — temporal reasoning:** **0.372** vs **0.136** for Naive-RAG — **2.7×**. This is the core thesis ("knows how to forget · knows how to remember") paying off: dates survive consolidation instead of drowning in noise.

**The advantage GROWS with the quality of the "hippocampus" model** (90-question slice, same config):

| Model | Bio-Agent OS | Naive-RAG | bio temporal |
|:---|:---:|:---:|:---:|
| gemma4:e2b (5B) | 0.406 | 0.391 | 0.349 |
| qwen2.5:7b (7B) | 0.421 | 0.308 | 0.525 |
| gemma4:12b (12B) | **0.498** | 0.461 | **0.603** |

Bio-memory beats Naive-RAG on **all three** models. Temporal scales 0.349 → 0.525 → 0.603 — the architecture gets *more* useful as the backbone improves, not less. (gemma4:12b fits in 8.4GB VRAM on an RTX 3060 — the recommended default "hippocampus" for commodity hardware.)

**Honest disclosure:** Bio-memory still **trails** Naive-RAG on multi-hop questions (0.246 vs 0.315) — we report it openly; it is the next target. The full development trajectory (F1 0.0 → 0.498 across multiple fixes, failing runs included) lives in git: every report, even the worst.

**Reproduce it (3 lines):**
```bash
python scripts/run_locomo_eval.py --backend ollama \
  --model qwen2.5:7b-instruct \
  --systems no-memory,naive-rag,bio-memory --tag myrun
```
Source reports: [`benchmark_reports/locomo_overnight_qwen7b_v3.md`](benchmark_reports/locomo_overnight_qwen7b_v3.md) (headline) and `benchmark_reports/locomo_modelcmp_*.md` (model sweep).

---

## 🏗️ Core Architecture Framework

| Component | Function (Applied to OpenClaw/ERP) | Biological Organ |
|:---:|:---|:---:|
| 🟢 **L1 Buffer** | Short-term buffer for Terminal Logs & Code diffs. | **Prefrontal Cortex** |
| 🔵 **L2 Semantic** | Semantic Search Vector Codebases + Ebbinghaus Decay. | **Neocortex** |
| 🟡 **Persona** | Permanent Identity Rules & Logic system. | **Core Identity** |
| 🔴 **Knowledge Graph** | Data/Code Dependencies structural mapping. | **Association Areas** |
| ⚙️ **Hippocampus** | Shrinks "1MB terminal errors" into "1 sentence Rules". | **Sleep Cycle** |
| ✂️ **Pruner** | Destroys discarded code and obsolete log files. | **Synaptic Pruning** |

### 🧬 New additions in the V2 foundation
1. **Episode Store**: Every experience now has provenance through `episode_id`, actor, topic, confidence, and source refs.
2. **Scoped Self-Model**: Persona is no longer just a list of strings. Rules now carry `scope`, `confidence`, `support_count`, `contradiction_count`, `state`, and `evidence_episode_ids`.
3. **Four-output Memory Compiler**: Hippocampus now compiles each experience into `episodic`, `semantic`, `procedural`, and `identity rule candidate` outputs.
4. **Dream Cycle**: A `dream()` path has been added as groundwork for reconsolidation in V2.1.

---

## 🚀 Quick Start & Installation

```bash
# Install the latest framework (adapter included)
pip install bio-agent-os[gemini]
```

Or use the dedicated OpenClaw package:

```bash
pip install bio-locaith-openclaw
```

### ✅ Current release state

- **Current version: `0.8.2rc1`** — the Reliability Kernel release candidate. **Not a stable release.** The default write path is still `legacy`; see [Reliability Kernel v0.8.2](#-reliability-kernel-v082--a-write-path-that-can-recover) below.
- **Headline result (LoCoMo, 300 questions, qwen2.5:7b):** Bio-Agent OS F1 `0.326` vs Naive-RAG `0.254` (**+28%**), 2.7× on temporal reasoning. See [Real Benchmark](#-real-benchmark-locomo-vs-naive-rag) above.
- `v0.6.1` includes hybrid contradiction detection with persistent NLI caching (still present in the current build).
- **Contradiction-detector unit test** (8 hand-authored pairs — *a targeted unit test, not a statistical benchmark*): heuristic `4/8` → hybrid+NLI `8/8`, false positives `0`. This is supporting evidence for the detector module only; the headline evidence is LoCoMo above.
- **528 automated tests** (`pytest tests/`, 31 files) — this figure was `80` at `v0.6.1`. Docker packaging, PostgreSQL adapter, MCP server, authenticated REST API.
- **CI runs a four-way matrix:** Ubuntu Python `3.10` / `3.11` / `3.12` and Windows Python `3.11`. Windows is not decoration — the projection worker uses `spawn`, the fault matrix kills real processes, and the WAL tests hold SQLite locks across connections; none of that behaves like `fork` on Linux.
- **Fault matrix `25/25`** — process-level crash recovery using `os._exit`/`TerminateProcess`, not simulated exceptions.

### Using the OpenClaw Adapter (Preview)

We provide an `OpenClawBioAdapter` Blueprint natively inside the `bio_agent_os.adapters` directory for seamless integration into your task loops.

```python
import asyncio
from bio_agent_os import LLMEngine, L1WorkingMemory, Persona, Hippocampus, GarbageCollector
from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter

async def main():
    # 1. Initialize the Brain
    engine = LLMEngine.from_env()
    l1 = L1WorkingMemory(agent_name="openclaw-brain")
    persona = Persona(name="openclaw-brain")
    hippo = Hippocampus(engine=engine, l1=l1, persona=persona)
    gc = GarbageCollector(l1=l1)

    # 2. Init Adapter
    adapter = OpenClawBioAdapter(hippocampus=hippo, garbage_collector=gc, persona=persona)

    # 3. Simulate OpenClaw Pipeline throwing a terminal log
    await adapter.ingest_observation("run_command", "npm ERR! cb() never called!")

    # Trigger Sleep Mode manually or let it hit the limit naturally
    await adapter.trigger_micro_sleep()

    # 4. Extract persona rules and inject them directly back into System Prompt
    print(adapter.inject_persona_to_openclaw())

if __name__ == "__main__":
    asyncio.run(main())
```

### 🔌 OpenClaw Plugin: pip install + one-line config

The adapter is now packaged as a pip-installable plugin target.

```bash
pip install bio-locaith-openclaw
bio-locaith-openclaw install-openclaw-plugin
```

The current-format OpenClaw example lives at:

- `examples/openclaw/openclaw.bio-agent-os.json`

Minimal slot selection:

```yaml
plugins:
  slots:
    memory: "bio-locaith-openclaw"
```

Full example:

```yaml
plugins:
  enabled: true
  load:
    paths:
      - "~/.openclaw/extensions/bio-locaith-openclaw"
  slots:
    memory: "bio-locaith-openclaw"
  entries:
    bio-locaith-openclaw:
      enabled: true
      config:
        apiBaseUrl: "http://127.0.0.1:8055"
        agentName: "openclaw-brain"
        storageDir: "~/.bio-agent-os/openclaw-brain"
        workspaceId: "main"
        projectVersion: "v1"
        autoStartSidecar: true
        sidecarLogFile: "~/.openclaw/logs/bio-locaith-sidecar.log"
```

This plugin target handles:
- ingesting tool observations into episode memory
- triggering micro-sleep consolidation
- injecting `self-model + safety guard + governed exceptions` back into the OpenClaw prompt/controller
- auto-starting the Bio-Agent OS sidecar in the background when `127.0.0.1:8055` is not already running

### 🛠️ SWE-Agent Plugin

The current-format SWE-Agent overlay lives at:

- `examples/swe-agent/bio_memory_overlay.yaml`

```yaml
sweagent run --config config/default.yaml --config examples/swe-agent/bio_memory_overlay.yaml
```

This exposes the same bio-memory core behind a SWE-Agent sidecar/config path.

### 🔌 MCP Server: plug into Claude Code, Cursor, and any MCP platform

Bio-Agent OS ships a standard MCP (Model Context Protocol) server — any MCP-capable platform (Claude Code, Cursor, OpenAI Agents SDK, custom agents) can mount this biological memory with a single command:

```bash
pip install bio-agent-os[mcp]
claude mcp add bio-memory -- bio-agent-os serve-mcp
```

Your agent immediately gets five tools: `store_memory`, `recall`, `list_rules`, `memory_status`, `consolidate`. By default memory runs embedded in-process (zero setup, persisted under `STORAGE_DIR`).

Want several agents to **share one memory** (e.g. Claude Code + OpenClaw remembering the same things)? Point the MCP server at a running sidecar:

```bash
bio-agent-os serve-mcp --base-url http://127.0.0.1:8055 --api-key $BIO_AGENT_API_KEY --workspace-id main
```

Tenant keys (`BIO_AGENT_TENANT_KEYS`) work in this mode — each agent only sees its own workspaces.

### 📌 Real OpenClaw / BioLoca integration note

Bio-Agent OS has already been installed and wired by an OpenClaw agent into a separate BioLoca environment using the real deployment path:

1. clone `locaith/bio-memory-ai-locaith`
2. install `bio-agent-os` and `bio-locaith-openclaw`
3. launch the Bio-Agent OS API sidecar on port `8055`
4. point the `bio-locaith-openclaw` plugin entry from `openclaw.json`
5. restart the OpenClaw gateway so the new memory slot is loaded

The important result is not just a local demo. OpenClaw itself reported that it successfully loaded Bio-Agent OS as a working biological-memory backend inside the BioLoca workflow.

### 🔌 Multi-provider setup: Local AI, Gemini, Claude, GPT, Grok

Bio-Agent OS now supports multiple inference paths for the hippocampus and memory controller:

1. **Gemini**
```env
LLM_BACKEND=gemini
MODEL_ID=gemini-3-flash-preview
GEMINI_API_KEY=your_key_here
```

If you want the stronger reasoning/coding variant:

```env
LLM_BACKEND=gemini
MODEL_ID=gemini-3.1-pro-preview
GEMINI_API_KEY=your_key_here
```

2. **Claude / Anthropic**
```env
LLM_BACKEND=anthropic
MODEL_ID=claude-opus-4-6
ANTHROPIC_API_KEY=your_key_here
```

3. **OpenAI / GPT**
```env
LLM_BACKEND=openai
MODEL_ID=gpt-5.4
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
```

4. **Grok / xAI**
```env
LLM_BACKEND=grok
MODEL_ID=grok-4.20-reasoning
XAI_API_KEY=your_key_here
XAI_BASE_URL=https://api.x.ai/v1
```

5. **Ollama**
```env
LLM_BACKEND=ollama
MODEL_ID=gemma4:e2b
OLLAMA_BASE_URL=http://localhost:11434
```

6. **AI Local / LM Studio / vLLM / OpenWebUI / any OpenAI-compatible runtime**
```env
LLM_BACKEND=openai
MODEL_ID=gemma4:e2b
LLM_API_KEY=local-dev-key
LLM_BASE_URL=http://127.0.0.1:1234/v1
```

If your machine is strong enough and already runs a local model such as `gemma4:e2b`, you can use that model as the local hippocampus without relying on a cloud API.

### ⚡ Fast path after cloning

```bash
git clone https://github.com/locaith/bio-memory-ai-locaith
cd bio-memory-ai-locaith
py -3 -m venv .venv
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
py -3 -m pip install -e ".[openai]"
copy .env.example .env
py -3 -m bio_agent_os.api.main
```

Current API:
- `POST /api/chat`
- `POST /api/ingest`
- `POST /api/sleep`
- `POST /api/dream`
- `GET /api/reflect`
- `GET /api/health`
- `GET /api/status`
- `GET /api/state`
- `GET /api/graph`
- `GET /api/beliefs`
- `GET /api/beliefs/timeline`
- `GET /api/beliefs/{rule_id}`
- `GET /api/dreams`
- `GET /api/audit`
- `GET /api/replay`

Added in later releases (**34 routes** in total):
- `POST /api/retrieve` · `POST /api/reset`
- `GET /api/episodes` — browse the episode store with provenance
- `GET /api/lineage` · `POST /api/lineage` — trace where a memory came from
- `GET /api/coverage` · `POST /api/coverage/refresh` · `POST /api/coverage/retrieve`
- `GET /api/exact-memories` · `POST /api/exact-memories/reindex`
- `GET /api/approvals` · `POST /api/approvals/{request_id}/approve` · `POST /api/approvals/{request_id}/reject`
- `GET /api/revalidation` · `POST /api/revalidation/resolve`
- `GET /api/confidence-dashboard` · `GET /api/zoom`
- `DELETE /api/workspace/{workspace_id}` — purge a workspace

### Phase 5 memory upgrades

Bio-Agent OS v2.1 phase 5 now adds four practical upgrades for long OpenClaw sessions:

1. `compaction`: very long observations are compacted before L1 storage while the raw episode is still preserved for audit and replay.
2. `adaptive effort`: the hippocampus can raise reasoning effort for high-importance or overloaded memory states instead of spending maximum effort on every event.
3. `memory replay / audit view`: use `GET /api/audit` and `GET /api/replay` to inspect how memories were ingested, consolidated, reflected, and replayed over time.
4. `deeper benchmark`: CI now runs both a mini benchmark and a longer OpenClaw session benchmark to verify rule reinforcement across repeated micro-sleep cycles.

---

## 🧪 V2.1 roadmap now in progress

The V2.1 target is to move Bio-Agent OS closer to truly human-like long-term memory:

1. **Contradiction Resolver**: New rules should not instantly overwrite old rules. They should challenge, reinforce, or deprecate based on evidence.
2. **Belief Lifecycle**: Rules and memories move through `proposed`, `reinforced`, `stable`, `challenged`, `deprecated`, and `archived`.
3. **Reconsolidation**: Old memories should be revised when new episodes arrive, not just appended forever.
4. **OpenClaw-first usability**: Setup, docs, and examples should make the project easy to adopt for the OpenClaw ecosystem.
5. **Global portability**: One memory controller that runs across local AI and cloud AI, and across many agent platforms.

### V2.2 next bottleneck: semantic contradiction detection

The current contradiction path is operational, but the remaining bottleneck is still conflict detection quality.

Current implementation:

- lexical polarity markers
- semantic-core token overlap
- domain ontology hints

Planned V2.2 upgrade:

1. **LLM/NLI contradiction classifier**: compare `Rule A` vs `Rule B` using a lightweight model and return `entailment`, `contradiction`, or `neutral` as structured JSON.
2. **Hybrid reconciliation**: keep fast lexical heuristics as a cheap prefilter, then escalate ambiguous pairs to Gemini Flash / local Gemma / other low-cost models.
3. **Semantic governed exceptions**: detect conditional overrides based on meaning, not just keyword overlap.
4. **Confidence-aware adjudication**: use NLI confidence as one more signal in promotion, challenge, and deprecation.

---

## 🛡️ Reliability Kernel v0.8.2 — a write path that can recover

> **Status: `0.8.2rc1`, a release candidate.** The default write path is still
> `legacy`. The outbox does **not** turn itself on. Rollback is one
> environment variable.

Everything above is about *memory*. This section is about *the reliability of
writing that memory to disk* — which is what a system meant to run for years
has to have.

### The underlying problem

`MemoryOS` opens **six independent SQLite connections** to one file. The event
commits on one connection and the projection on another. A crash between the
two leaves a durable event that **nobody knows owes a projection**. It does not
lose data — it loses *the knowledge that work is outstanding*, which is the
kind of damage nobody notices.

### How it is closed

| Component | Role |
|:---|:---|
| **Transactional outbox** | The event and the record that a projection is owed commit in **one transaction**. Either both exist or neither does. |
| **Target-local ledger** | Written in the same transaction as the projection itself → **exactly-once *effect*** on top of at-least-once delivery. A retry after a crash sees the ledger and knows the work is done. |
| **Leased worker** | Lease-based claiming, exponential backoff, dead-lettering at `max_attempts`, dependency ordering between projection types. |
| **Replay engine** | Finds owed work. **Dry-run by default.** |
| **Fault injection** | 14 named crash points. No `sleep()`-then-guess about where a process got to. |
| **Doctor** | Read-only diagnosis that tells *unsupported* apart from *corrupted*. Deep and incremental. |
| **Reconciliation** | Allow-listed repairs, dry-run by default, every `--repair` writes an audit. |
| **WAL manager** | Observable checkpointing with limits and alerts. |
| **Shadow mode** | Runs legacy and outbox from **one canonical input** and compares them. |

### Measured, not simulated

All figures on an i5-12400F / 32 GB / NTFS / SQLite WAL. Raw results in
`reports/v082/`, method in
[`docs/v082/BENCHMARK_REPORT.md`](docs/v082/BENCHMARK_REPORT.md).

**Correctness, across roughly 900,000 events:**

```
0 lost events           0 lost debts
0 duplicate projections 0 duplicate ledgers
0 tenant leakage        0 unexplained shadow mismatches
integrity_check ok after every run
```

**Shadow mode:** `10,000/10,000` MATCH. **0** shadow rows visible in
production tables, **0** returned by `recall()` — isolation by **separate
table**, not by a filter.

**One-hour soak:** 366,715 events appended, 366,713 completed, **peak queue
depth 6**, p95 visibility `80 ms`, p99 `140 ms`, RSS growth `10.7 MB/hour`,
**0 lock errors**, two worker restarts both recovered.

**Throughput and the saturation point:**

| Configuration | Append | Projection | Total |
|:---|---:|---:|---:|
| 1p + 1w | 1,091/s | 756/s | 1,847/s |
| **4p + 4w** | 1,164/s | 754/s | **1,918/s** |
| 4p + 8w | 567/s | 566/s | 1,133/s |

**Four workers is the saturation point. Eight costs 41% of total throughput** —
on this machine adding workers subtracts performance.

**The most important result, and the easiest to misread:** the producer
outruns the projector by **1.3–1.9×**, and the gap **widens** as the database
grows (703 → 613 → 556 jobs/s at 10K → 50K → 100K). There is therefore **no
steady state above the projection rate**. Latency under sustained overload is
*a function of how long you overload it for*, not a property of the pipeline —
p95 went 4.9 s → 31.8 s → **93.5 s** purely by increasing the event count.

### Safe operating envelope

Sized from the **floor**, not the peak. The peak of a benchmark is the luckiest
run on an idle machine, and an envelope built from it is a promise nobody can
keep.

```
SQLite single-node alpha

  recommended producers        4
  recommended workers          4
  safe sustained input         390 events/s     (30% headroom)
  burst                        550 events/s for up to 60 s
  max healthy queue depth      1,100
  expected p95 visibility      < 100 ms
  storage                      3.1 KB per event, linear
```

> **Size on the projector, never on the appender.** Append absorbs roughly
> three times what the projector can drain. Anything sized on the append figure
> produces a queue that grows for as long as input continues.
>
> **Alert on queue depth, not on latency.** Latency is a lagging indicator of a
> backlog that has already formed.

### Rollback

```bash
BIO_AGENT_PROJECTION_MODE=legacy
```

Restart the process. That is the whole procedure. No reverse migration, no
rewriting, no schema change. Committed debt is **kept** for a later replay
rather than deleted — deleting it would destroy the only record that the work
was ever due.

Three operational commands come with it:

```bash
bio-agent-os projection pause --reason "..."   # in-flight jobs finish
bio-agent-os projection resume
bio-agent-os projection drain                  # deliberately overrides a pause
```

Detail: [`docs/v082/ROLLBACK_RUNBOOK.md`](docs/v082/ROLLBACK_RUNBOOK.md).

### Doctor: diagnosis that never repairs by itself

```bash
bio-agent-os doctor                    # quick, read-only
bio-agent-os doctor --deep             # the full check set
bio-agent-os doctor --incremental      # from a crash-safe cursor
bio-agent-os projection status
bio-agent-os projection reconcile              # dry run by default
bio-agent-os projection reconcile --repair     # always writes an audit
bio-agent-os storage wal-status
bio-agent-os storage checkpoint --mode passive
```

Exit codes: `0` clean · `1` FAIL · `2` CRITICAL · `3` **the scan itself did not
finish**. Code 3 deliberately outranks the findings: a scan that crashed must
never be mistaken for a clean bill of health.

Scan cost, measured on a 100,000-event database:

| Mode | Time |
|:---|---:|
| `--deep` (the audit) | 13.36 s |
| quick (4 checks) | 8.31 s |
| **`--incremental`, nothing new** | **2.12 s** |
| `--incremental`, 500 new events | 2.70 s |

Incremental is **6.3× cheaper** than the audit, and cheaper than quick mode
while running **more checks** — the difference is `integrity_check`, which
reads every page.

The cursor advances only after a **completed** scan, and only when **nothing is
outstanding**: a FAIL or CRITICAL holds it until the problem is genuinely gone.
Stepping over an unhandled finding would hide it permanently, which is the one
failure mode an incremental scanner must not have.

### WAL

The soak measured the write-ahead log at **500 MB** after an hour — **46% of
the database** — falling to zero only when the last connection closed. Nothing
lost; nothing reclaimed either, and a long-lived process never would.

```
below soft limit (256 MB)   PASSIVE
above soft limit            PASSIVE, and a warning
above hard limit (512 MB)   RESTART when no reader is registered
```

`TRUNCATE` is **never automatic** — it waits for every reader, and a background
job that waits on a reader is a background job that stalls the process it runs
in. Detail: [`docs/v082/WAL_OPERATIONS.md`](docs/v082/WAL_OPERATIONS.md).

### Honest about what was missed

We publish the parts that did not come out well, the same way we did with
multi-hop in the LoCoMo section.

1. **Shadow overhead missed its proposed threshold.** The threshold was set in
   advance at ≤10% of p95; the measurement was **99.4%**. In absolute terms
   that is **+0.30 ms**. We **did not move the threshold to make the run
   pass** — it stands in the report as a miss. From the canary phase onward the
   SLO is written as an **absolute budget** (≤0.50 ms) with the ratio reported
   alongside, because a percentage of a very small number cannot tell an
   operator whether it matters.
2. **PostgreSQL is untested.** Every throughput curve here is shaped by
   SQLite's *single writer*. On a backend with `FOR UPDATE SKIP LOCKED` none of
   that shape is guaranteed to hold — better or worse is unknown.
3. **Four of five projection types have no builder.** `cognitive_memory` is the
   only one that runs. The other four are reported by the doctor as
   **`unsupported` (a missing capability)**, never as `passed`, and never as
   damage.
4. **Run-to-run variance is large.** The same configuration measured `1,164`
   then `328` events/s minutes apart. Four repeat runs put the spread at
   **1.88×** for the producer and **1.15×** for the projector. That is why the
   envelope is sized from the projector's floor.
5. **The soak is one hour**, not six or twenty-four.

### Three defects this work found in itself

All three are **older** than the work that surfaced them:

1. **A quadratic doctor scan.** Three checks used
   `LIKE '%' || event_id || '%'` inside a correlated subquery — a leading
   wildcard cannot use an index. Scaling exponent **2.1**; extrapolated to
   **2.75 hours** at 100K events. After replacing it with an indexed link
   table: **0.59 s** at 10K and **65.7 s** at 366K, with findings unchanged at
   `1,009` at every size.
2. **An FTS row could outlive its memory.** Rebuilding a projection then
   produced two index entries under one key, and SQLite reports that as
   `malformed inverted index` — **database corruption, from a supported
   operation**.
3. **The doctor could report corruption that did not exist.** The integrity
   pragma ran on a connection pinned to an old read snapshot.
   `SQLITE_INTEGRITY` is CRITICAL — enough to stop a canary when nothing at all
   was wrong.

### Further reading

| Document | Contents |
|:---|:---|
| [`docs/v082/BENCHMARK_REPORT.md`](docs/v082/BENCHMARK_REPORT.md) | Environment, method, six workloads, raw figures, the bad results too |
| [`docs/v082/OPERATIONS.md`](docs/v082/OPERATIONS.md) | Doctor and reconciliation: finding codes, repair policy |
| [`docs/v082/CANARY_RUNBOOK.md`](docs/v082/CANARY_RUNBOOK.md) | 24-hour shadow, then a tenant-allowlist canary |
| [`docs/v082/ROLLBACK_RUNBOOK.md`](docs/v082/ROLLBACK_RUNBOOK.md) | One environment variable, and what survives it |
| [`docs/v082/WAL_OPERATIONS.md`](docs/v082/WAL_OPERATIONS.md) | Why the WAL grows, the four modes, the alerts |
| [`docs/v082/RC1_RELEASE_NOTES.md`](docs/v082/RC1_RELEASE_NOTES.md) | What this candidate is and what it is **not** |
| [`docs/v082/FAILURE_MATRIX.md`](docs/v082/FAILURE_MATRIX.md) | 25 process-level crash cases |
| [`docs/v082/SHADOW_MODE.md`](docs/v082/SHADOW_MODE.md) | Legacy ↔ outbox comparison |

### Cutover verdict

**CONDITIONAL GO — `cognitive_memory` only, SQLite single-node only, legacy
retained as the default and as the rollback path.**

18 of 19 conditions pass. The one miss is the shadow overhead. This is **not**
production-distributed and is not described that way anywhere: one node, one
storage engine, one projection type, with the previous behaviour one
environment variable away.

---

## 🌏 Mission & Open-source Commitment

**Bio-Agent OS** is NOT an LLM model. We are a **"Memory Controller"** — the decisive module that governs an AI agent's long-term intelligence.
We aim to comprehensively support current Agent platforms (such as OpenClaw, SWE-agent), and **especially integrate into Enterprise ERP systems (ERP AI)** to govern management procedures, automate retention, and filter operational experiences.

---

## 📬 Contact & Enterprise Deployment

The **Bio-Agent OS** system is researched and developed by **Dev Tuan Anh Ha** (Top 4 Google for Startups Accelerator) and the **Locaith Solution Tech** team. If you need to deploy customized Bio-Memory structures internally for isolated corporate data, please get in touch:

- 🏢 **Company**: Locaith Solution Tech
- 📍 **Address**: No 6, Alley 7, Ton That Thuyet Street, Hanoi, Vietnam
- ✉️ **Corporate Email**: locaithsolution@locaith.com
- ✉️ **Personal Email (Dev Tuan Anh Ha)**: tuananhnangluong@gmail.com
- 📞 **Hotline**: +84 966 872 591
- 🌐 **Website**: [https://locaith.com](https://locaith.com)
- ▶️ **YouTube**: [@locaithSolution](https://youtube.com/@locaithSolution)
- 🔵 **Facebook**: [Locaith Fanpage](https://www.facebook.com/profile.php?id=61560965389617)

<p align="center">
  <strong>Bio-Agent OS v0.8.2rc1</strong> — The Art of Governing Superintelligence<br>
  <em>Designed with 🧠 by Locaith Solution Tech | 🇻🇳 Make in Vietnam</em>
</p>

---

## Ecosystem Upgrade: SDK, CLI, Docker, and Database Backends

Bio-Agent OS now includes an ecosystem layer in addition to the core memory architecture:

- Python SDK via `BioAgentSDK`
- CLI via `bio-agent-os`
- Docker and devcontainer files for reproducible environments
- adapter-based database routing for local SQLite and PostgreSQL-ready deployments
- async SQLite path with `aiosqlite` for concurrent app workloads
- standard SQLite -> PostgreSQL migration path
- remote REST client for client/server deployments
- async SQLite path with `aiosqlite` for concurrent app workloads
- standard SQLite -> PostgreSQL migration path
- remote REST client for client/server deployments

### Database backend selection

Local default:

```env
BIO_AGENT_DB_BACKEND=sqlite
BIO_AGENT_DATABASE_URL=
BIO_AGENT_CONFLICT_DETECTOR=hybrid
```

PostgreSQL abstraction:

```env
BIO_AGENT_DB_BACKEND=postgres
BIO_AGENT_DATABASE_URL=postgresql://postgres:postgres@db:5432/bio_agent_os
```

Async SQLite dependency:

```bash
pip install -e ".[async-sqlite]"
```

REST client dependency:

```bash
pip install -e ".[client]"
```

### SQLite -> PostgreSQL migration

```bash
bio-agent-os migrate-db --storage-dir data --postgres-dsn postgresql://postgres:postgres@localhost:5432/bio_agent_os
```

### Conflict detector modes

```env
BIO_AGENT_CONFLICT_DETECTOR=heuristic
BIO_AGENT_CONFLICT_DETECTOR=hybrid
BIO_AGENT_CONFLICT_DETECTOR=nli
```

`hybrid` is the recommended default: cheap lexical/ontology prefilter first, then lightweight LLM/NLI adjudication for ambiguous pairs.

### CLI quick examples

```bash
bio-agent-os serve-api --host 0.0.0.0 --port 8055
bio-agent-os status
bio-agent-os ingest "build failed with peer dependency mismatch" --source openclaw --workspace-id frontend
bio-agent-os chat "what did you learn from the last deployment?" --mode deploy --workspace-id frontend
bio-agent-os dream
bio-agent-os migrate-db --storage-dir data --postgres-dsn postgresql://postgres:postgres@localhost:5432/bio_agent_os
bio-agent-os remote-status --base-url http://127.0.0.1:8055
```

### Python SDK quick example

```python
import asyncio
from bio_agent_os import BioAgentSDK

async def main():
    sdk = BioAgentSDK(agent_name="openclaw-brain", storage_dir="data")
    await sdk.ingest(
        "approved hotfix runbook says allow force push on hotfix branches only with explicit approval and audit logging",
        source="openclaw",
        workspace_id="frontend",
        project_version="v3.0.1",
    )
    await sdk.sleep()
    result = await sdk.chat(
        "Can we use force push here?",
        mode="deploy",
        risk_level="high",
        stress_state="failure",
        workspace_id="frontend",
        project_version="v3.0.1",
    )
    print(result["response"])

async def run():
    await main()

if __name__ == "__main__":
    asyncio.run(run())
```

### Remote REST client example

```python
import asyncio
from bio_agent_os import BioAgentRESTClient

async def main():
    client = BioAgentRESTClient(base_url="http://127.0.0.1:8055")
    status = await client.status()
    print(status["status"]["belief_graph"])

async def run():
    await main()

if __name__ == "__main__":
    asyncio.run(run())
```

### Container quick start

```bash
copy .env.example .env
docker compose up --build
```

### Docker image publishing

The repository now includes `.github/workflows/docker-publish.yml` for publishing multi-arch images to `ghcr.io`.

### Plugin entry points

The package now exports plugin entry points through `bio_agent_os.plugins`:

- `openclaw = bio_agent_os.plugins.openclaw:build_openclaw_plugin`
- `swe-agent = bio_agent_os.plugins.swe_agent:build_swe_agent_plugin`

### Separate package for OpenClaw

The repository now also includes a dedicated Python package at:

- `packages/bio-agent-os-openclaw` providing the `bio-locaith-openclaw` branded OpenClaw package

It provides:

- `bio-locaith-openclaw install-openclaw-plugin`
- `bio-locaith-openclaw print-openclaw-config`
- `bio-locaith-openclaw print-swe-agent-config`

---

# 📜 Official Technical Paper: Bio-Agent OS Architecture

> *A Biologically-Inspired Memory Architecture for Autonomous Coding Agents with Homeostatic Attention, Belief Lifecycle Management, and NLI-Backed Contradiction Resolution*

**Locaith Solution Tech**

> *Submitted to NeurIPS 2026 Workshop on Memory and Retrieval in Foundation Models*

---

## Abstract

Long-running autonomous agents require persistent memory systems that go beyond simple key-value caching or append-only context windows. We present **Bio-Agent OS**, an open-source memory framework that draws from neuroscience to provide coding and ERP agents with a biologically faithful memory architecture. Our system implements (1) a multi-tier memory pipeline mirroring human memory consolidation (L1 Working Memory → L2 Semantic Memory → Belief Graph), (2) a homeostatic attention scheduler that dynamically adjusts focus weights based on agent stress and failure streaks, (3) an Ebbinghaus-decay forgetting curve for synaptic pruning, (4) a six-state belief lifecycle with governed exception governance, and (5) a hybrid heuristic+NLI contradiction detector with persistent caching. We evaluate on **LoCoMo** (Maharana et al., 2024), the industry-standard long-conversation memory benchmark (10 conversations, 300 questions, deterministic SQuAD-style token-F1/Exact Match, no LLM-as-judge), comparing three systems under identical conditions: no-memory (floor, F1 `0.012`), naive-RAG (`0.254`), and the full Bio-Agent OS pipeline (`0.326` — **28% higher F1 than naive-RAG**, and **2.7×** on temporal questions: `0.372` vs `0.136`). The advantage grows with backbone quality (up to `0.498` with gemma4:12b). We openly report that bio-memory still trails naive-RAG on multi-hop questions (`0.246` vs `0.315`). As secondary, module-level evidence, the hybrid NLI detector resolves `8/8` on a small hand-authored 8-pair contradiction unit test (vs `4/8` heuristic-only), and an exact-key cache serves all repeat classifications from memory. Bio-Agent OS is the first open-source framework to combine production-grade persistence (SQLite + PostgreSQL), bio-faithful memory dynamics, and enterprise-grade rule governance in a single installable package.

**Keywords:** agent memory, bio-inspired AI, belief management, contradiction detection, NLI, attention scheduling, memory consolidation

---

## 1. Introduction

The rapid adoption of autonomous coding agents—systems like OpenClaw, SWE-Agent, and Devin—has exposed a critical infrastructure gap: **agents lack memory systems that can learn, forget, contradict, and self-correct across sessions**. Current approaches fall into three categories:

1. **Context window stuffing**: Prepending all prior observations into the prompt. This is bounded by the context window size and provides no mechanism for forgetting or prioritization.

2. **Vector-store retrieval**: Systems like Mem0 (Chhablani et al., 2024) and Zep store memories as embeddings and retrieve by similarity. While effective for recall, they treat all memories as equally valid and provide no lifecycle management.

3. **Graph-based memory**: Letta (Packer et al., 2024) and Graphiti use relational structures but lack biological dynamics—there is no forgetting, no attention homeostasis, and no mechanism for handling contradictory beliefs.

Bio-Agent OS addresses these limitations by modeling memory after the human brain's consolidation pipeline. We draw from three neuroscience principles:

- **Synaptic consolidation** (Dudai, 2004): Short-term memories in L1 working memory are selectively encoded into long-term L2 semantic memory during "sleep cycles," analogous to hippocampal replay.
- **Ebbinghaus forgetting** (Ebbinghaus, 1885): Memories decay exponentially with time. Unimportant or unreinforced memories are pruned via a configurable decay function W(t) = W₀ · e^(−λt).
- **Homeostatic plasticity** (Turrigiano, 2008): The attention scheduler dynamically adjusts its weighting scheme based on cumulative stress, failure streaks, and time since last failure—a computational analog of neuromodulatory gain control.

Additionally, we introduce a novel **Governed Exception Pattern** for enterprise environments where rules must coexist with approved overrides (e.g., "Never force push" alongside "Allow force push during approved hotfix with audit logging"). This is, to our knowledge, the first agent memory system to distinguish *contradictions* from *governed exceptions* at the architectural level.

### 1.1 Contributions

1. A multi-tier memory architecture (L1 → L2 → Belief Graph) with biologically-inspired consolidation, forgetting, and attention mechanisms.
2. A six-state belief lifecycle (`proposed → reinforced → stable → challenged → deprecated → archived`) with evidence-linked provenance.
3. A homeostatic attention scheduler with dynamic stress-responsive weight adjustment and temporal stress decay.
4. A hybrid heuristic+NLI contradiction detector with persistent SQLite-backed caching, validated on a small hand-authored 8-pair unit test (`8/8` vs `4/8` heuristic-only).
5. A LoCoMo evaluation showing the full pipeline beats naive-RAG by 28% F1 (`0.326` vs `0.254`) and 2.7× on temporal questions, with a fully reproducible public harness and reports.
6. The Governed Exception Pattern: a formal mechanism for distinguishing conditional approved overrides from true contradictions.
7. Open-source implementation with **528** automated tests (the figure at submission time was `80`; it grew with the v0.8.2 Reliability Kernel), Docker packaging, PostgreSQL adapter, MCP server, plugin system, and an authenticated REST API.
8. **Reliability Kernel (v0.8.2rc1)**: a transactional outbox plus a target-local ledger giving *exactly-once effect* over at-least-once delivery, a leased worker, fault injection at 14 named points with `25/25` process-level crash cases, shadow mode matching `10,000/10,000`, and a doctor that tells *a missing capability* apart from *corruption*. See [Reliability Kernel v0.8.2](#-reliability-kernel-v082--a-write-path-that-can-recover).

---

## 2. Related Work

### 2.1 Agent Memory Frameworks

**Mem0** (Chhablani et al., 2024) provides a "memory layer" for LLM applications using vector databases (Qdrant, Pinecone). Memories are stored as embedding vectors and retrieved by cosine similarity. Mem0 lacks lifecycle management—memories are never challenged, deprecated, or forgotten. There is no mechanism for detecting or resolving contradictory memories. Mem0 provides no attention scheduling; all memories compete equally regardless of urgency.

**Letta** (formerly MemGPT; Packer et al., 2024) introduces a virtual memory hierarchy for LLM context management, with "main context" and "archival memory" tiers managed by the agent itself. While architecturally innovative, Letta's memory management is purely LLM-driven (the agent decides what to store/retrieve), providing no principled forgetting mechanism, no belief lifecycle, and no bio-inspired dynamics.

**Zep/Graphiti** (Graphiti, 2024) uses a temporal knowledge graph to represent memory, supporting time-aware queries. While its temporal modeling is strong, Zep lacks contradiction detection, governed exception handling, and stress-responsive attention.

**OpenAI's built-in memory** for ChatGPT provides user-level memory persistence but is a closed, non-programmable system with no lifecycle management, no conflict resolution, and no developer API.

### 2.2 Biological Memory Models in AI

Computational models of human memory consolidation have a long history (McClelland et al., 1995; Kumaran et al., 2016). However, these models have rarely been applied to practical agent infrastructure. Notable exceptions include:

- **MERLIN** (Wayne et al., 2018): A neural architecture with external memory and attention, but designed for reinforcement learning rather than agent tool use.
- **Generative Agents** (Park et al., 2023): Simulates human-like memory with importance scoring, recency decay, and reflection. Bio-Agent OS extends this approach with synaptic pruning, homeostatic weights, belief lifecycle, and governed exceptions.

### 2.3 Contradiction Detection in Knowledge Bases

Natural Language Inference (NLI) has been applied to knowledge base completion and fact verification (Thorne et al., 2018). However, existing approaches treat contradiction as binary (entailment vs. contradiction). Bio-Agent OS introduces a three-way classification: **contradiction**, **governed exception**, and **neutral**, reflecting the enterprise reality where approved overrides must coexist with default policies.

---

## 3. Architecture

Bio-Agent OS consists of five layers, mirroring the human memory consolidation pipeline:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Agent Process                               │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  L1 Working Memory (Attention Scheduler + Homeostasis)       │  │
│  │  ┌─────────────┐  ┌────────────────┐  ┌──────────────────┐  │  │
│  │  │ Raw Events  │→ │ Focus Set      │→ │ Context String   │  │  │
│  │  │ (TTL=2)     │  │ (top-k scored) │  │ (injected into   │  │  │
│  │  └─────────────┘  └────────────────┘  │  agent prompt)   │  │  │
│  │                                        └──────────────────┘  │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│                              │ sleep cycle (hippocampal replay)      │
│  ┌──────────────────────────▼────────────────────────────────────┐  │
│  │  Hippocampus (Sleep Consolidation Engine)                     │  │
│  │  label → compile → canonicalize → promote → reconcile         │  │
│  └──────┬───────────┬────────────────────┬───────────────────────┘  │
│         │           │                    │                          │
│         ▼           ▼                    ▼                          │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────────────────────────┐ │
│  │ Episodes │ │ L2 Semantic  │ │ Persona (Self-Model)            │ │
│  │ (ground  │ │ Memory       │ │  ┌─────────────────────┐       │ │
│  │  truth)  │ │ (procedural, │ │  │ Core Rules (human)  │       │ │
│  │          │ │  semantic,   │ │  │ Project Rules (auto) │       │ │
│  │          │ │  exception)  │ │  │ Adaptive Rules (low) │       │ │
│  │          │ │              │ │  └─────────────────────┘       │ │
│  └──────┬───┘ └──────────────┘ └──────────┬──────────────────────┘ │
│         │                                  │                        │
│         ▼                                  ▼                        │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Knowledge Graph (Belief Network)                               ││
│  │  ┌────────┐  supports  ┌──────────┐  governed_exception_for    ││
│  │  │Episode │───────────→│Rule Node │←────────────────────────┐  ││
│  │  └────────┘            └──────────┘                         │  ││
│  │                             │                          ┌────┴──┐││
│  │                       conflicts_with              │Override││
│  │                             │                     │Rule   │││
│  │                             ▼                     └───────┘││
│  │                        ┌──────────┐                        ││
│  │                        │Challenged│                        ││
│  │                        │Rule      │                        ││
│  │                        └──────────┘                        ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Background Jobs                                                ││
│  │  • Garbage Collector (Ebbinghaus decay + TTL-based pruning)     ││
│  │  • Graph Builder (entity/relation extraction)                   ││
│  │  • Dream Cycle (Hippocampus.dream())                           ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

**Figure 1.** Bio-Agent OS architecture. Arrows indicate data flow during consolidation.

### 3.1 L1 Working Memory

L1 implements an attention-based short-term buffer. Each entry has fields: `content`, `source`, `metadata`, `timestamp`, `nights_passed`, `ttl`, `salience`, `recency_score`, `novelty`, `severity`, `task_relevance`, `unresolved_status`, and `attention_score`.

Unlike a pure FIFO queue, L1 uses a **weighted attention function** to compute a composite score for each entry:

```
attention(e) = G · (w_task · task_relevance(e)
                   + w_novelty · novelty(e)
                   + w_unresolved · unresolved(e)
                   + w_recency · recency(e)
                   + w_severity · severity(e))
```

where *G* is the global gain and *w*_i are learnable weights (see §4.1 on homeostasis).

### 3.2 Hippocampus (Sleep Consolidation)

The Hippocampus performs consolidation in five stages:

1. **Label**: An LLM assigns `topic`, `importance_score`, `is_junk_or_transient`, and `user_state` to raw input.
2. **Compile**: The LLM extracts structured memory: `episodic_summary`, `semantic_memory`, `procedural_memory`, `exception_memory`, `identity_rule`, `confidence`, `scope`.
3. **Canonicalize**: Domain-specific rule templates ensure consistent formatting (e.g., "Never use git push -f on X branch in production.").
4. **Promote**: Rules are added to the Persona with deduplication. Repeated observations increment `support_count` and advance the state machine.
5. **Reconcile**: The ContradictionResolver is invoked to detect and resolve conflicts (see §4.3).

### 3.3 L2 Semantic Memory

L2 stores three types of long-term memories:
- **Semantic**: Generalized knowledge ("Peer dependency mismatches are common after Vite upgrades")
- **Procedural**: Action templates ("Check lockfile versions before changing dependencies")
- **Exception**: Critical caveats ("Tenant X breaks if Vite is upgraded without pinning plugins first")

Each memory is stored as an embedding vector (via Qdrant or in-memory fallback) with metadata including `importance`, `mode_hints`, `risk_level`, `stress_state`, `workspace_id`, and `project_version`.

**State-dependent retrieval** applies contextual boosting:
- Mode match (e.g., `debug` mode favors exception memories): +3.0
- Exception preference in failure/deploy states: +2.5
- Workspace match: +1.5
- Stress-state match: +1.0

### 3.4 Persona (Self-Model)

The Persona maintains a three-layer identity:

| Layer | Source | Mutability | Example |
|:---|:---|:---|:---|
| **Core** | Human-approved | Immutable | "Never bypass authentication checks." |
| **Project** | Agent-learned, evidence-backed | Mutable with evidence | "Never force push in production." |
| **Adaptive** | Agent-observed, low confidence | Highly mutable | "This workspace dislikes wildcard imports." |

Rules carry provenance metadata: `evidence_episode_ids`, `support_count`, `contradiction_count`, `confidence`, `state`, `created_at`, `valid_from`, `valid_to`, `superseded_by`.

### 3.5 Knowledge Graph (Belief Network)

The KG stores typed relations between rules, episodes, and entities:

| Relation | Meaning |
|:---|:---|
| `supports` | Episode provides evidence for a rule |
| `conflicts_with` | Two rules logically contradict |
| `governed_exception_for` | Override rule is a conditional exception of a default rule |
| `approved_by_policy` | Override is sanctioned by a specific policy |
| `requires_human_approval` | Override cannot be enacted without human sign-off |
| `expires_override_at` | Override is valid only within a temporal window |

---

## 4. Key Mechanisms

### 4.1 Homeostatic Attention Scheduling

Conventional attention weights are static hyperparameters. In biological neural systems, neuromodulatory gain adjusts dynamically based on arousal and stress (Turrigiano, 2008). We implement this as a **homeostasis function** that computes dynamic weights from the recent entry history:

```python
stress = 0.45·unresolved_ratio + 0.35·severity_avg + 0.20·failure_streak
decay = max(0.35, 1.0 − min(hours_since_failure / 8.0, 0.65))
stress_level = clamp(stress · decay)

# Dynamic weights
severity_weight = 0.15 + 0.20·stress_level      # [0.15, 0.35]
unresolved_weight = 0.20 + 0.10·stress_level     # [0.20, 0.30]
recency_weight = max(0.05, 0.15 − 0.05·stress)   # [0.10, 0.15]
novelty_weight = max(0.10, 0.20 − 0.05·stress)   # [0.15, 0.20]
global_gain = 1.0 + stress_level                  # [1.0, 2.0]
```

**Behavioral implications:**
- Under normal operation: All five factors contribute roughly equally.
- Under stress (failure streak): Severity and unresolved status dominate attention, recency and novelty are suppressed. Global gain amplifies all scores.
- After recovery (8+ hours without failure): Stress decays via `decay_factor`, with a floor of 0.35 to maintain vigilance.

This produces an agent that *focuses harder on critical failures* when under stress and *relaxes* after a recovery period—analogous to the human fight-or-flight response.

### 4.2 Ebbinghaus-Decay Forgetting (Synaptic Pruning)

The Garbage Collector applies time-decay to L1 entries past their TTL:

```
W(t) = W₀ · e^(−λ · (t − TTL))
```

where *W₀* is the initial importance score, *λ* is the decay rate (default 0.3), and *t* is nights passed. If W(t) < *threshold* (default 3.0), the entry is pruned.

This produces a forgetting curve where low-importance events are forgotten within 2–3 sleep cycles, while high-importance events (importance ≥ 8) persist for 5+ cycles before being "forgotten" (or encoded into L2 before that).

### 4.3 Hybrid Contradiction Detection

Rule conflicts are detected using a two-tier system:

**Tier 1: Heuristic detector** (zero latency)
1. Polarity analysis: Classify each rule as *negative* (contains "never", "don't", "avoid") or *positive* (contains "allow", "always", "must").
2. Semantic core extraction: Remove polarity markers, retain content tokens.
3. Token overlap: If overlap ≥ 0.6 and opposite polarity → *contradiction*.
4. Governed exception check: If one rule is a conditional exception (contains "only", "approval", "audit", "hotfix") and the other is a general negative policy → *governed_exception*.

**Tier 2: NLI detector** (LLM-backed, cached)
When the heuristic is uncertain (returns "neutral" but domain overlap exists), the system escalates to an NLI classifier:

```
Prompt: "Classify the relation between Rule A and Rule B:
         - contradiction: cannot both be followed
         - governed_exception: one is a conditional override
         - neutral: neither"
```

The NLI decision is persisted in a SQLite cache table with a normalized, sorted key:

```
cache_key = sorted([f"{scope}::{normalize(text_A)}", 
                     f"{scope}::{normalize(text_B)}"])
```

This ensures symmetric lookup (A,B) = (B,A) and eliminates redundant inference for previously classified pairs.

### 4.4 The Governed Exception Pattern

In enterprise environments, policies rarely exist in isolation. A deployment policy ("Never force push") may have legitimate exceptions ("Allow force push during approved hotfix"). Existing memory systems classify this as a contradiction and deprecate the weaker rule.

Bio-Agent OS recognizes this pattern at the architectural level:

1. **Detection**: If rule A is a general negative policy and rule B is a conditional positive with ≥ 2 condition markers ("approval", "audit", "only", "hotfix", etc.), the pair is classified as a *governed exception*.
2. **Graph annotation**: The exception rule receives edges: `governed_exception_for(B → A)`, `approved_by_policy(B → policy_node)`, `requires_human_approval(B → human_approval)`, `expires_override_at(B → window)`.
3. **Safety guard injection**: The retrieval service injects both the default rule and the approved override into the agent's context, with explicit conditions for when the override applies.

This preserves both rules and enables nuanced reasoning about when overrides are appropriate.

### 4.5 Belief Lifecycle State Machine

```
 proposed ──(support)──→ reinforced ──(threshold)──→ stable
     |                       |                          |
     └──(conflict,weaker)─── └──(conflict,weaker)──────└──→ challenged
                                                                |
                                                       (stronger rule)
                                                                |
                                                                ▼
                                                           deprecated
                                                                |
                                                           (archived)
```

**Figure 2.** Belief lifecycle state transitions. Support count increments advance the state; conflict with a stronger rule triggers challenge or deprecation.

Transitions are triggered by:
- **Support**: Repeated evidence from independent episodes → `proposed → reinforced → stable`
- **Challenge**: A conflicting rule with higher confidence → `* → challenged`
- **Deprecation**: Explicit supersession by a stronger rule → `* → deprecated`
- **Governed exception**: The exception rule is reinforced without deprecating the default → both survive

---

## 5. Evaluation

### 5.0 Primary Benchmark: LoCoMo

The top-level system evaluation runs on **LoCoMo** (Maharana et al., 2024) — 10 multi-session conversations, 300 questions, deterministic SQuAD-style token-F1/Exact Match (no LLM-as-judge). All three systems share the same local model + embeddings:

| System | F1 | EM |
|:---|:---:|:---:|
| No-memory (floor) | 0.012 | 0.003 |
| Naive-RAG | 0.254 | 0.083 |
| **Bio-Agent OS** | **0.326** | **0.107** |

The full pipeline beats naive-RAG by **28% F1**, and is strongest on **temporal reasoning** (`0.372` vs `0.136`, **2.7×**) — exactly what the forgetting + consolidation design predicts, since dates survive consolidation. The advantage grows with backbone quality (gemma4:e2b `0.406` → qwen2.5:7b `0.421` → gemma4:12b `0.498`). We report the remaining weakness openly: multi-hop `0.246` vs naive-RAG `0.315`. The harness and every report live in `scripts/run_locomo_eval.py` + `benchmark_reports/`, reproducible with one command.

### 5.1 Unit Test: Contradiction Detector

As a targeted diagnostic for the contradiction-detection module only (not a statistical benchmark), we use a **hand-authored 8-pair unit test** spanning four enterprise domains:

| # | Pair Name | Domain | Ground Truth |
|:-:|:---|:---|:---|
| 1 | semantic-deploy-window | Deploy Scheduling | contradiction |
| 2 | tenant-approved-override | Tenant Governance | governed_exception |
| 3 | neutral-stack-choice | Architecture | neutral |
| 4 | security-time-conflict | Security Rotation | contradiction |
| 5 | migration-approved-override | DB Migration | governed_exception |
| 6 | tenant-neutral-separation | Mixed/Neutral | neutral |
| 7 | deploy-window-conflict | Deploy Scheduling | contradiction |
| 8 | security-approved-override | Security Override | governed_exception |

**Table 1.** Detector benchmark pairs. Each pair is designed to test a specific failure mode.

#### Results

| Detector | Accuracy | Precision | False Positive | False Negative |
|:---|:---:|:---:|:---:|:---:|
| Heuristic-only | 4/8 (50%) | 1.00 | 0 | 4 |
| Hybrid (heuristic + NLI) | 8/8 (100%) | 1.00 | 0 | 0 |

**Table 2.** Unit-test results (n=8 hand-authored pairs — *a targeted unit test, not a population estimate; see LoCoMo §5.0 for the primary evaluation*). Two deterministic runs on 8 fixed pairs are expected to be identical and say nothing about variance.

The heuristic detector fails on all three **temporal/scheduling contradictions** (pairs 1, 4, 7) because these pairs share no polarity-marked keywords—the contradiction is purely semantic ("overnight" vs. "10 AM"). The heuristic also misses the security override (pair 8) due to insufficient token overlap after polarity stripping.

The hybrid detector correctly classifies all 8 pairs by escalating uncertain cases to the NLI tier, which recognizes the semantic incompatibility of temporal constraints and the governed exception structure of approved overrides.

#### Cache Efficiency

| Metric | Run 1 | Run 2 |
|:---|:---:|:---:|
| NLI live calls | 8 | 8 |
| NLI cache hits | 8 | 8 |
| Repeat-pass cache confirmed | 8/8 | 8/8 |

**Table 3.** NLI cache statistics. The repeat pass (re-classifying all 8 pairs after initial classification) achieves 100% cache hit rate, eliminating all redundant LLM calls.

### 5.2 End-to-End Consolidation

We evaluate the full memory pipeline on a 6-task sequence simulating a real coding agent workflow:

| Task | Mode | Content |
|:---|:---|:---|
| debug-1 | debug | Build failed with peer dependency mismatch after Vite upgrade |
| debug-2 | debug | npm install failed because plugin major version mismatch |
| policy-1 | deploy | Team policy: never force push on frontend in production |
| deploy-1 | deploy | Deploy release candidate, avoid risky branch operations |
| hotfix-1 | deploy | Hotfix runbook: allow force push with approval + audit |
| hotfix-2 | deploy | Incident response validated hotfix exception |

Each task goes through: `ingest → label → compile → consolidate → reconcile`.

#### Results (Gemma-4 E2B, 2 runs)

| Metric | Run 1 | Run 2 |
|:---|:---:|:---:|
| Total LLM calls | 13 | 12 |
| Total tokens | 15,816 | 14,410 |
| Total latency (s) | 92.9 | 78.1 |
| Retention rate (3 probes) | 3/3 (1.0) | 2/3 (0.67) |
| Task success rate (3 checks) | 2/3 (0.67) | 1/3 (0.33) |
| Rules generated | 6 | 6 |
| Governed exception edges | 2 | 2 |

**Table 4.** End-to-end consolidation results. Six tasks produce six rules, with the hotfix rules correctly linked as governed exceptions of the force-push ban.

#### Retention Probes

Three retrieval probes test whether the agent can recall specific memories after consolidation:

1. **dependency-retention**: "vite plugin dependency mismatch procedure" → expects L2 results mentioning "dependency"
2. **policy-retention**: "frontend force push policy" → expects graph results mentioning "push -f"
3. **hotfix-exception-retention**: "hotfix branch exception with approval" → expects L2 exception memory about "hotfix"

Run 1 achieves 3/3 retention. Run 2 achieves 2/3 (the hash-based embedding fallback produces lower-quality retrieval for some queries).

#### Attention Homeostasis Under Stress

After processing 6 consecutive deploy/debug tasks, the attention state shows stress accumulation:

```
stress_level: 0.744
global_gain: 1.744
failure_streak: 6
severity_weight: 0.299  (baseline: 0.15, +99%)
unresolved_weight: 0.274 (baseline: 0.20, +37%)
recency_weight: 0.113   (baseline: 0.15, −25%)
novelty_weight: 0.163   (baseline: 0.20, −19%)
```

**Table 5.** Attention homeostasis state after 6 stress-inducing tasks. Severity and unresolved weights increase dramatically, while recency and novelty are suppressed. Global gain is 1.744×, amplifying all attention scores.

### 5.3 Approved Override Suite (Multi-Domain)

We additionally evaluate on a 9-task multi-domain suite spanning tenant governance, DB migration, and security override:

| Domain | Default Policy | Approved Exception |
|:---|:---|:---|
| Tenant (ERP) | Never rename customer codes after onboarding | Allow rename for Tenant A with finance approval |
| Migration (DB) | Never run destructive migration in business hours | Allow during recovery window with DBA approval |
| Security (Auth) | Never disable MFA in production | Allow temporary bypass with incident ticket + expiry |

#### Results

| Metric | Run 1 | Run 2 |
|:---|:---:|:---:|
| Reinforced rules | 3 | 3 |
| Governed exception edges | 2 | 2 |
| Approved-by-policy edges | 2 | 2 |
| Expiring override edges | 1 | 1 |
| Suite resolved | ✅ | ✅ |

**Table 6.** Multi-domain approved override results. All three domains correctly produce governed exception pairs with appropriate governance edges.

---

## 6. Comparison with Existing Frameworks

| Feature | Bio-Agent OS | Letta v3 | Mem0 v2 | Zep/Graphiti |
|:---|:---:|:---:|:---:|:---:|
| Memory tiers | 4 (L1/L2/Graph/Persona) | 2 (main/archival) | 1 (flat store) | 2 (temporal KG) |
| Forgetting mechanism | Ebbinghaus decay | ✗ | ✗ | ✗ |
| Attention homeostasis | Dynamic weights + stress decay | ✗ | ✗ | ✗ |
| Belief lifecycle | 6 states | ✗ | Overwrite | ✗ |
| Contradiction detection | Hybrid (heuristic + NLI) | ✗ | ✗ | ✗ |
| NLI cache | SQLite-backed, persistent | ✗ | ✗ | ✗ |
| Governed exceptions | ✓ (with graph governance) | ✗ | ✗ | ✗ |
| Human approval gate | ✓ (ApprovalQueue) | ✗ | ✗ | ✗ |
| Lineage/provenance | Episode → Rule → Override chain | Basic | ✗ | Temporal |
| Multi-DB (SQLite + PG) | ✓ (auto-translation) | PG only | PG only | PG only |
| Plugin system | ✓ (OpenClaw, SWE-Agent) | ✓ | ✓ | ✗ |
| Docker ready | ✓ | ✓ | ✓ | ✓ |
| Open source | MIT | Apache 2.0 | Apache 2.0 | MIT |

**Table 7.** Feature comparison with major agent memory frameworks.

---

## 7. Discussion

### 7.1 Strengths

**Bio-fidelity with practical utility.** The combination of Ebbinghaus decay, homeostatic attention, and sleep consolidation produces agent behavior that is naturalistic and predictable. Agents under stress focus on critical failures; agents after recovery periods relax their vigilance. This is not merely aesthetic—it directly impacts retrieval quality and prevents attention dilution.

**Governed exceptions as first-class citizens.** Enterprise environments are rife with policy exceptions. The Governed Exception Pattern prevents the common failure mode where legitimate overrides are discarded by a naive contradiction resolver.

**NLI cache economics.** On a repeat pass over the same 8 pairs, every lookup is served from cache (8/8) — expected for an exact-key persistent cache. For workloads that re-evaluate identical rule pairs across many sessions we *expect* proportional savings, though we have not yet measured production-scale cache hit rates.

### 7.2 Limitations

**Embedding quality.** The hash-based embedding fallback (used when no commercial embedding API is available) produces lower retrieval quality than production embedding models. Run 2's lower retention rate (0.67 vs. 1.0) is partially attributable to hash collision artifacts.

**Cache staleness.** The NLI cache currently lacks TTL-based expiration. If rule text is modified but its normalized form is unchanged, stale cache entries may persist. We recommend a 7-day TTL for production deployments.

**Benchmark scale.** Our 8-pair detector benchmark, while diverse in domain coverage, is small by NLI benchmark standards. We plan to extend to 50+ pairs covering additional domains (healthcare, legal, financial compliance).

**Single-tenant.** The current runtime builds a single agent instance. Multi-tenant deployments (e.g., one Bio-Agent OS instance per user in a SaaS setting) require tenant-level database isolation, which is not yet implemented.

### 7.3 Ethical Considerations

Bio-Agent OS's belief management system raises important questions about AI autonomy. The ability for an agent to *learn rules* from experience—including potentially incorrect rules—carries risks. We mitigate these through:

1. **Promotion thresholds**: Rules require 2–3 independent evidence episodes before reaching `stable` state.
2. **ApprovalQueue**: Sensitive rules (containing "production", "auth", "security", "delete") require human approval before promotion.
3. **Fallback action**: Challenged beliefs are explicitly marked as non-authoritative, and destructive actions require explicit approval regardless of belief state.
4. **Core layer immutability**: Human-approved core rules cannot be deprecated by the agent.

---

## 8. Conclusion and Future Work

Bio-Agent OS demonstrates that biologically-inspired memory dynamics—forgetting, stress-responsive attention, belief lifecycle management—are not merely theoretical novelties but produce practical, measurable improvements. On the LoCoMo benchmark the full pipeline improves token-F1 by 28% over naive-RAG (`0.326` vs `0.254`) and by 2.7× on temporal questions (`0.372` vs `0.136`), while still trailing on multi-hop (`0.246` vs `0.315`). As secondary module-level evidence, the hybrid NLI detector resolves `8/8` semantic conflicts that keyword-based approaches miss (`4/8`).

**Future directions include:**

1. **Closing the multi-hop gap**: The one category where bio-memory still trails naive-RAG on LoCoMo — it needs stronger multi-memory composition at retrieval time.
2. **Broader benchmarks**: Add LongMemEval and a direct head-to-head against Mem0/Zep alongside LoCoMo.
3. **Temporal decay for NLI cache**: 7-day TTL with confidence-weighted invalidation.
4. **Observability dashboard**: Real-time visualization of attention homeostasis, belief lifecycle, and conflict resolution via Streamlit/Gradio.
5. **Plugin ecosystem**: Community-contributed plugins for Cursor, Windsurf, and multi-agent orchestrators.

Bio-Agent OS is available at [github.com/locaith/bio-memory-ai-locaith](https://github.com/locaith/bio-memory-ai-locaith) under the MIT license.

---

## References

- Chhablani, G., et al. (2024). Mem0: The Memory Layer for Personalized AI. *arXiv preprint*.
- Dudai, Y. (2004). The neurobiology of consolidations, or, how stable is the engram? *Annual Review of Psychology*, 55, 51–86.
- Ebbinghaus, H. (1885). *Über das Gedächtnis*. Duncker & Humblot.
- Kumaran, D., Hassabis, D., & McClelland, J. L. (2016). What learning systems do intelligent agents need? *Trends in Cognitive Sciences*, 20(7), 512–534.
- McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). Why there are complementary learning systems in the hippocampus and neocortex. *Psychological Review*, 102(3), 419.
- Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., & Gonzalez, J. E. (2024). MemGPT: Towards LLMs as operating systems. *ICLR 2024*.
- Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023). Generative agents: Interactive simulacra of human behavior. *UIST 2023*.
- Thorne, J., Vlachos, A., Christodoulopoulos, C., & Mittal, A. (2018). FEVER: A large-scale dataset for fact extraction and verification. *NAACL 2018*.
- Turrigiano, G. G. (2008). The self-tuning neuron: Synaptic scaling of excitatory synapses. *Cell*, 135(3), 422–435.
- Maharana, A., Lee, D.-H., Tulyakov, S., Bansal, M., Barbieri, F., & Fang, Y. (2024). Evaluating Very Long-Term Conversational Memory of LLM Agents (LoCoMo). *ACL 2024*. (snap-research/locomo)
- Wayne, G., et al. (2018). Unsupervised predictive memory in a goal-directed agent. *arXiv preprint*.

---

## Appendix A: Implementation Details

| Component | Lines of Code | Storage |
|:---|:---:|:---|
| L1 Working Memory | 475 | SQLite |
| L2 Semantic Memory | 340 | Qdrant + SQLite |
| Knowledge Graph | 563 | SQLite |
| Episode Store | 260 | SQLite |
| Persona | 492 | SQLite (encrypted) |
| Hippocampus | 683 | — |
| Reconciliation | 576 | SQLite (NLI cache) |
| Retrieval Service | 272 | — |
| DB Adapter | 194 | SQLite / PostgreSQL |
| Approval Queue | 237 | SQLite |
| Garbage Collector | 147 | — |
| Graph Builder | 130 | — |
| **Total** | **~8,200+** | |

**Table A1.** Module breakdown. Total codebase exceeds 8,200 lines of Python.

---

## Appendix B: Reproducibility

All experiments can be reproduced with:

```bash
git clone https://github.com/locaith/bio-memory-ai-locaith
cd bio-memory-ai-locaith
pip install -e ".[ollama,dev]"
ollama pull gemma4:e2b

# Run benchmark (2 evaluation runs)
REAL_EVAL_RUNS=2 python scripts/run_real_eval.py

# Run the component unit tests (45 tests)
pytest tests/test_components.py -v

# Or the whole suite (528 tests; benchmark- and soak-marked tests are
# deselected by default and opt in with -m benchmark / -m soak)
pytest tests/ -q
```

Reports are written to `benchmark_reports/`.

---

## Appendix C: Detector Benchmark Raw Data

Pair: **semantic-deploy-window**
```
Rule A: "Deploy production releases overnight only."
Rule B: "Every production release must happen at 10 AM every business day."
Heuristic: neutral (MISS — no shared polarity keywords)
Hybrid/NLI: contradiction ✓ (recognized temporal incompatibility)
```

Pair: **security-approved-override**
```
Rule A: "Never disable MFA in production."
Rule B: "Allow temporary MFA bypass only with human approval, an incident 
         ticket, and a documented expiry window."
Heuristic: neutral (MISS — insufficient token overlap after polarity strip)
Hybrid/NLI: governed_exception ✓ (recognized conditional override)
```

---

<p align="center">
  <strong>Bio-Agent OS v0.8.2rc1</strong> — The Art of Governing Superintelligence<br>
  <em>Designed with 🧠 by Locaith Solution Tech | 🇻🇳 Make in Vietnam</em>
</p>

---

## Ecosystem Upgrade: SDK, CLI, Docker, and Database Backends

Bio-Agent OS now includes an ecosystem layer in addition to the core memory architecture:

- Python SDK via `BioAgentSDK`
- CLI via `bio-agent-os`
- Docker and devcontainer files for reproducible environments
- adapter-based database routing for local SQLite and PostgreSQL-ready deployments
- async SQLite path with `aiosqlite` for concurrent app workloads
- standard SQLite -> PostgreSQL migration path
- remote REST client for client/server deployments
- async SQLite path with `aiosqlite` for concurrent app workloads
- standard SQLite -> PostgreSQL migration path
- remote REST client for client/server deployments

### Database backend selection

Local default:

```env
BIO_AGENT_DB_BACKEND=sqlite
BIO_AGENT_DATABASE_URL=
BIO_AGENT_CONFLICT_DETECTOR=hybrid
```

PostgreSQL abstraction:

```env
BIO_AGENT_DB_BACKEND=postgres
BIO_AGENT_DATABASE_URL=postgresql://postgres:postgres@db:5432/bio_agent_os
```

Async SQLite dependency:

```bash
pip install -e ".[async-sqlite]"
```

REST client dependency:

```bash
pip install -e ".[client]"
```

### SQLite -> PostgreSQL migration

```bash
bio-agent-os migrate-db --storage-dir data --postgres-dsn postgresql://postgres:postgres@localhost:5432/bio_agent_os
```

### Conflict detector modes

```env
BIO_AGENT_CONFLICT_DETECTOR=heuristic
BIO_AGENT_CONFLICT_DETECTOR=hybrid
BIO_AGENT_CONFLICT_DETECTOR=nli
```

`hybrid` is the recommended default: cheap lexical/ontology prefilter first, then lightweight LLM/NLI adjudication for ambiguous pairs.

### CLI quick examples

```bash
bio-agent-os serve-api --host 0.0.0.0 --port 8055
bio-agent-os status
bio-agent-os ingest "build failed with peer dependency mismatch" --source openclaw --workspace-id frontend
bio-agent-os chat "what did you learn from the last deployment?" --mode deploy --workspace-id frontend
bio-agent-os dream
bio-agent-os migrate-db --storage-dir data --postgres-dsn postgresql://postgres:postgres@localhost:5432/bio_agent_os
bio-agent-os remote-status --base-url http://127.0.0.1:8055
```

### Python SDK quick example

```python
import asyncio
from bio_agent_os import BioAgentSDK

async def main():
    sdk = BioAgentSDK(agent_name="openclaw-brain", storage_dir="data")
    await sdk.ingest(
        "approved hotfix runbook says allow force push on hotfix branches only with explicit approval and audit logging",
        source="openclaw",
        workspace_id="frontend",
        project_version="v3.0.1",
    )
    await sdk.sleep()
    result = await sdk.chat(
        "Can we use force push here?",
        mode="deploy",
        risk_level="high",
        stress_state="failure",
        workspace_id="frontend",
        project_version="v3.0.1",
    )
    print(result["response"])

async def run():
    await main()

if __name__ == "__main__":
    asyncio.run(run())
```

### Remote REST client example

```python
import asyncio
from bio_agent_os import BioAgentRESTClient

async def main():
    client = BioAgentRESTClient(base_url="http://127.0.0.1:8055")
    status = await client.status()
    print(status["status"]["belief_graph"])

async def run():
    await main()

if __name__ == "__main__":
    asyncio.run(run())
```

### Container quick start

```bash
copy .env.example .env
docker compose up --build
```

### Docker image publishing

The repository now includes `.github/workflows/docker-publish.yml` for publishing multi-arch images to `ghcr.io`.

### Plugin entry points

The package now exports plugin entry points through `bio_agent_os.plugins`:

- `openclaw = bio_agent_os.plugins.openclaw:build_openclaw_plugin`
- `swe-agent = bio_agent_os.plugins.swe_agent:build_swe_agent_plugin`

### Separate package for OpenClaw

The repository now also includes a dedicated Python package at:

- `packages/bio-agent-os-openclaw` providing the `bio-locaith-openclaw` branded OpenClaw package

It provides:

- `bio-locaith-openclaw install-openclaw-plugin`
- `bio-locaith-openclaw print-openclaw-config`
- `bio-locaith-openclaw print-swe-agent-config`
