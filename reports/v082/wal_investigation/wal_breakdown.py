"""4,2 KB WAL moi luot ghi di dau? Tach tung thanh phan."""
import os, sqlite3, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, r"C:\locaith\bio-memory-ai-locaith")
os.environ.setdefault("BIO_AGENT_TENANT_ID","bench"); os.environ.setdefault("BIO_AGENT_WORKSPACE_ID","bd")
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord, MemoryType
from bio_agent_os.cognitive.projection_registry import ProjectionType
MEM = ProjectionType.COGNITIVE_MEMORY.value
BODY = "x"*380; N = 1500; WARM = 3000

def measure(label, mode, fn):
    d = Path(tempfile.mkdtemp())/"b.db"
    os_ = MemoryOS(d, projection_mode=mode)
    fn(os_, WARM, 0)                      # lam nong
    c = os_.events.conn
    for _ in range(6):
        r = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if r and int(r[0])==0: break
        time.sleep(0.2)
    before = Path(str(d)+"-wal").stat().st_size if Path(str(d)+"-wal").exists() else 0
    t0=time.perf_counter(); fn(os_, N, WARM); el=time.perf_counter()-t0
    after = Path(str(d)+"-wal").stat().st_size if Path(str(d)+"-wal").exists() else 0
    per=(after-before)/N
    print(f"  {label:<44} {per:>8,.0f} B/luot  {per/4096:>5.2f} trang  {N/el:>7,.0f} luot/s", flush=True)
    os_.close(); return per

def only_event(os_, n, s):
    for i in range(s,s+n):
        os_.events.append(EventRecord(tenant_id="bench",actor="a",source="u",
            payload={"content":f"r{i} {BODY}"},event_id=f"e{i}"), projection_types=())

def event_with_debt(os_, n, s):
    for i in range(s,s+n):
        os_.events.append(EventRecord(tenant_id="bench",actor="a",source="u",
            payload={"content":f"r{i} {BODY}"},event_id=f"e{i}"), projection_types=(MEM,))

def observe_only(os_, n, s):
    for i in range(s,s+n):
        os_.observe(tenant_id="bench",actor="a",source="u",content=f"r{i} {BODY}",workspace_id="bd")

def observe_remember(os_, n, s):
    for i in range(s,s+n):
        ev=os_.observe(tenant_id="bench",actor="a",source="u",content=f"r{i} {BODY}",workspace_id="bd")
        os_.remember(event=ev,memory_type=MemoryType.EPISODIC,content=f"r{i} {BODY}")

print("Moi dong = mot lop duoc them vao. Chenh lech giua hai dong = gia cua lop do.\n", flush=True)
a=measure("1. chi ghi su kien, khong no chieu",       "legacy", only_event)
b=measure("2. su kien + tao no chieu (outbox)",       "legacy", event_with_debt)
c=measure("3. observe() day du",                      "legacy", observe_only)
d=measure("4. observe + remember, che do legacy",     "legacy", observe_remember)
e=measure("5. observe + remember, che do SHADOW",     "shadow", observe_remember)
print(f"\n  gia cua outbox        {b-a:>8,.0f} B")
print(f"  gia cua phan con lai observe() {c-b:>8,.0f} B")
print(f"  gia cua remember()    {d-c:>8,.0f} B")
print(f"  gia cua shadow        {e-d:>8,.0f} B")
