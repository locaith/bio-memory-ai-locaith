"""journal_size_limit co giu duoc file WAL trong gioi han khong, KHI CO READER chan?

Day la tinh huong that cua canary: 3 worker + supervisor doc lien tuc, nen
TRUNCATE thuong bi busy. Cau hoi: journal_size_limit co cat file ma khong can
TRUNCATE thanh cong khong.
"""
import os, sqlite3, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, r"C:\locaith\bio-memory-ai-locaith")
os.environ.setdefault("BIO_AGENT_TENANT_ID","bench"); os.environ.setdefault("BIO_AGENT_WORKSPACE_ID","bd")
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
BODY="x"*380

def run(label, jsl_mb, with_reader, batches=14, per=700):
    d = Path(tempfile.mkdtemp())/"b.db"
    os_ = MemoryOS(d, projection_mode="shadow"); c = os_.events.conn
    if jsl_mb is not None:
        c.execute(f"PRAGMA journal_size_limit={int(jsl_mb*1048576)}")
    stop = threading.Event(); rd=[]
    def reader():
        r = sqlite3.connect(str(d), timeout=1.0)
        while not stop.is_set():
            r.execute("BEGIN"); r.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
            time.sleep(0.35); r.rollback()
        r.close()
    if with_reader:
        for _ in range(2):
            t=threading.Thread(target=reader,daemon=True); t.start(); rd.append(t)
    wal=lambda: Path(str(d)+"-wal").stat().st_size if Path(str(d)+"-wal").exists() else 0
    peaks=[]; n=0
    for _ in range(batches):
        for i in range(n,n+per):
            e=os_.observe(tenant_id="bench",actor="a",source="u",content=f"r{i} {BODY}",workspace_id="bd")
            os_.remember(event=e,memory_type=MemoryType.EPISODIC,content=f"r{i} {BODY}")
        n+=per
        c.execute("PRAGMA wal_checkpoint(PASSIVE)")     # y het duong NORMAL cua manager
        peaks.append(wal())
    stop.set(); time.sleep(0.5)
    print(f"  {label:<46} dinh {max(peaks)/1048576:>6.1f} MB   cuoi {peaks[-1]/1048576:>6.1f} MB", flush=True)
    os_.close(); return max(peaks)

print("Chi dung PASSIVE checkpoint (khong TRUNCATE), giong duong NORMAL.\n", flush=True)
print("  --- khong co reader chan ---", flush=True)
run("journal_size_limit = -1 (nhu hien nay)", None, False)
run("journal_size_limit = 32 MB",             32,  False)
print("\n  --- CO 2 reader doc lien tuc, giong canary ---", flush=True)
run("journal_size_limit = -1 (nhu hien nay)", None, True)
run("journal_size_limit = 32 MB",             32,  True)
run("journal_size_limit = 64 MB",             64,  True)
