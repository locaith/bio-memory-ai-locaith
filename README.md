<p align="center">
  <img src="docs/images/openclaw_integration.png" alt="OpenClaw + Bio-Agent OS Integration" width="100%"/>
</p>

<p align="center">
  <h1 align="center">🧠 Bio-Agent OS v0.6.0</h1>
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

## 📊 So Sánh: Compact (Big Tech) vs Bio-Memory (Coding Sessions)

Dưới đây là biểu đồ mô phỏng hiệu suất và lượng Token sụp đổ rùng rợn của phương pháp "Compact" (nén rác thành rác) so với sự ổn định tuyệt đối của Bio-Memory khi code liên tục 100 tác vụ.

<p align="center">
  <img src="docs/images/coding_performance.png" alt="Coding Performance Over Time" width="100%"/>
</p>

* **Compact (Đường màu Đỏ)**: Token phình to nhanh chóng → Mất Context (Hallucination) → Crash hoàn toàn tại Task thứ 50 do không thể xử lý nổi lượng rác tích tụ.
* **Bio-Memory (Đường màu Xanh/Cyan)**: Trễ nhịp tí xíu chạy Background Sleep Cycle, nhưng duy trì VRAM tối ưu và độ chính xác hoàn hảo 100% kể cả ở Task thứ 1000.

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

asyncio.run(main())
```

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

6. **AI Local / LM Studio / vLLM / OpenWebUI / mọi server OpenAI-compatible**
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

### Phase 5 memory upgrades

Bio-Agent OS V2.1 phase 5 hiện bổ sung 4 nâng cấp thực dụng cho OpenClaw session dài:

1. `compaction`: quan sát quá dài sẽ được nén gọn trước khi vào L1, nhưng episode gốc vẫn được giữ để audit và replay.
2. `adaptive effort`: hippocampus có thể tự tăng mức effort cho ký ức quan trọng hoặc lúc bộ nhớ đang quá tải, thay vì đốt effort cao cho mọi event.
3. `memory replay / audit view`: dùng `GET /api/audit` và `GET /api/replay` để soi lại toàn bộ vòng đời ingest, consolidate, reflect, dream.
4. `deeper benchmark`: CI giờ kiểm cả mini benchmark lẫn long-session benchmark cho OpenClaw để đảm bảo rule được reinforce qua nhiều micro-sleep cycle.

---

## 🧪 Hướng nâng cấp V2.1 đang triển khai

Mục tiêu V2.1 là đưa Bio-Agent OS tiến thêm một bước gần hơn với trí nhớ người:

1. **Contradiction Resolver**: Rule mới không overwrite rule cũ ngay, mà challenge, reinforce hoặc deprecate theo evidence.
2. **Belief Lifecycle**: Memory/rule đi qua các trạng thái `proposed`, `reinforced`, `stable`, `challenged`, `deprecated`, `archived`.
3. **Reconsolidation**: Khi có episode mới, memory cũ được đọc lại và có thể bị chỉnh sửa thay vì chỉ append thêm.
4. **OpenClaw-first usability**: README, config và flow cài đặt sẽ nhấn mạnh để cộng đồng OpenClaw clone về dùng được ngay.
5. **Global portability**: Một memory controller dùng được trên local AI lẫn cloud AI, áp dụng cho nhiều agent framework chứ không chỉ một nền tảng.

---

## 🌏 Tầm Nhìn & Open-source Commitment

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

## 📊 Comparison: Compact (Big Tech) vs Bio-Memory

Below is a simulated graph representing the horrific token bloat and performance collapse of the "Compact" method (compressing garbage into smaller garbage) compared to the absolute stability of Bio-Memory when performing 100 continuous coding tasks.

<p align="center">
  <img src="docs/images/coding_performance.png" alt="Coding Performance Over Time" width="100%"/>
</p>

* **Compact (Red Line)**: Rapid token bloat → Context Loss (Hallucination) → Total crash at Task #50 due to overwhelming garbage accumulation.
* **Bio-Memory (Cyan Line)**: Microsecond delays running Background Sleep Cycles, but maintains strictly optimized VRAM and 100% precision accuracy even at Task #1000.

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

asyncio.run(main())
```

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

### Phase 5 memory upgrades

Bio-Agent OS V2.1 phase 5 now adds four practical upgrades for long OpenClaw sessions:

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
  <strong>Bio-Agent OS v0.6.0</strong> — The Art of Governing Superintelligence<br>
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

asyncio.run(main())
```

### Remote REST client example

```python
import asyncio
from bio_agent_os import BioAgentRESTClient

async def main():
    client = BioAgentRESTClient(base_url="http://127.0.0.1:8055")
    status = await client.status()
    print(status["status"]["belief_graph"])

asyncio.run(main())
```

### Container quick start

```bash
copy .env.example .env
docker compose up --build
```

### Docker image publishing

The repository now includes `.github/workflows/docker-publish.yml` for publishing multi-arch images to `ghcr.io`.
