"""Do sach: tat autocheckpoint de moi frame ghi ra deu nam lai trong file."""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, r"C:\locaith\bio-memory-ai-locaith")
os.environ.setdefault("BIO_AGENT_TENANT_ID","bench"); os.environ.setdefault("BIO_AGENT_WORKSPACE_ID","bd")
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord, MemoryType
from bio_agent_os.cognitive.projection_registry import ProjectionType
MEM = ProjectionType.COGNITIVE_MEMORY.value
BODY="x"*380; N=1000

def run(label, mode, fn, warm, autockpt):
    d = Path(tempfile.mkdtemp())/"b.db"
    os_ = MemoryOS(d, projection_mode=mode); c = os_.events.conn
    fn(os_, warm, 0)
    c.execute(f"PRAGMA wal_autocheckpoint={autockpt}")
    for _ in range(6):
        r=c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if r and int(r[0])==0: break
        time.sleep(0.2)
    wal=lambda: Path(str(d)+"-wal").stat().st_size if Path(str(d)+"-wal").exists() else 0
    b=wal(); t0=time.perf_counter(); fn(os_,N,warm); el=time.perf_counter()-t0; a=wal()
    per=(a-b)/N; frames=(a-b)/4120
    print(f"  {label:<40} {per:>8,.0f} B  {frames/N:>6.2f} frame/luot  {N/el:>7,.0f} luot/s", flush=True)
    os_.close(); return per

def ev(os_,n,s):
    for i in range(s,s+n):
        os_.events.append(EventRecord(tenant_id="bench",actor="a",source="u",
            payload={"content":f"r{i} {BODY}"},event_id=f"e{i}"), projection_types=())
def obs_rem(os_,n,s):
    for i in range(s,s+n):
        e=os_.observe(tenant_id="bench",actor="a",source="u",content=f"r{i} {BODY}",workspace_id="bd")
        os_.remember(event=e,memory_type=MemoryType.EPISODIC,content=f"r{i} {BODY}")

print("A. autocheckpoint TAT (0) — moi frame nam lai, day la so that\n", flush=True)
run("chi ghi su kien", "legacy", ev, 3000, 0)
run("observe+remember shadow, db nho", "shadow", obs_rem, 3000, 0)
run("observe+remember shadow, db lon hon", "shadow", obs_rem, 30000, 0)
print("\nB. autocheckpoint BAT (1000) — nhu cau hinh dang chay\n", flush=True)
run("chi ghi su kien", "legacy", ev, 3000, 1000)
run("observe+remember shadow, db nho", "shadow", obs_rem, 3000, 1000)
run("observe+remember shadow, db lon hon", "shadow", obs_rem, 30000, 1000)
