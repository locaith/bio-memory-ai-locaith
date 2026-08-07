"""Reader giu snapshot kieu nao thi WAL phinh, kieu nao thi khong?"""
import os, sqlite3, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, r"C:\locaith\bio-memory-ai-locaith")
os.environ.setdefault("BIO_AGENT_TENANT_ID","bench"); os.environ.setdefault("BIO_AGENT_WORKSPACE_ID","bd")
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
BODY="x"*380

def run(label, style, n_readers=2, batches=14, per=700):
    d = Path(tempfile.mkdtemp())/"b.db"
    os_ = MemoryOS(d, projection_mode="shadow"); c = os_.events.conn
    stop = threading.Event()
    def reader():
        r = sqlite3.connect(str(d), timeout=1.0)
        while not stop.is_set():
            if style == "held":                 # BEGIN ... giu 0,35s ... rollback
                r.execute("BEGIN"); r.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
                time.sleep(0.35); r.rollback()
            elif style == "short":              # BEGIN ... nha ngay
                r.execute("BEGIN"); r.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
                r.rollback(); time.sleep(0.35)
            else:                               # autocommit, khong BEGIN
                r.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
                time.sleep(0.35)
        r.close()
    for _ in range(n_readers):
        threading.Thread(target=reader, daemon=True).start()
    wal=lambda: Path(str(d)+"-wal").stat().st_size if Path(str(d)+"-wal").exists() else 0
    peaks=[]; k=0
    for _ in range(batches):
        for i in range(k,k+per):
            e=os_.observe(tenant_id="bench",actor="a",source="u",content=f"r{i} {BODY}",workspace_id="bd")
            os_.remember(event=e,memory_type=MemoryType.EPISODIC,content=f"r{i} {BODY}")
        k+=per
        c.execute("PRAGMA wal_checkpoint(PASSIVE)")
        peaks.append(wal())
    stop.set(); time.sleep(0.5)
    print(f"  {label:<50} dinh {max(peaks)/1048576:>7.1f} MB", flush=True)
    os_.close()

print("Cung khoi luong ghi (9.800 luot), chi doi CACH READER giu snapshot.\n", flush=True)
run("khong co reader nao",                          "none", n_readers=0)
run("reader BEGIN roi giu 0,35 giay  (nhu hien nay)","held")
run("reader BEGIN roi nha ngay",                     "short")
run("reader autocommit, khong BEGIN",                "auto")
