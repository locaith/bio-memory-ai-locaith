from __future__ import annotations
import argparse, json, os, random, statistics, tempfile, time, traceback
from multiprocessing import Process, Queue
from pathlib import Path

from bio_agent_os import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType, TrustTier, VerificationStatus


def pct(values, p):
    if not values: return None
    s=sorted(values)
    idx=min(len(s)-1, max(0, int((len(s)-1)*p)))
    return s[idx]


def seed(db, n=10000):
    with MemoryOS(db) as m:
        batch=[]
        for i in range(n):
            batch.append({
                'content': f'device-{i} canonical calibration procedure code CAL-{i} use torque {i%97+10}',
                'memory_type': MemoryType.PROCEDURAL,
                'confidence': .92, 'importance': .7, 'utility': .8,
                'verification_status': VerificationStatus.MACHINE_CHECKED,
                'structured_content': {'id': i},
            })
            if len(batch)>=500:
                m.bulk_ingest(tenant_id='tenant-main', actor='seed', source='benchmark', items=batch, workspace_id='prod', trust_tier=TrustTier.TRUSTED_SYSTEM)
                batch=[]
        if batch:
            m.bulk_ingest(tenant_id='tenant-main', actor='seed', source='benchmark', items=batch, workspace_id='prod', trust_tier=TrustTier.TRUSTED_SYSTEM)


def read_worker(db, ids, q):
    lat=[]; ok=0; errors=[]
    try:
        with MemoryOS(db) as m:
            ctx=AccessContext(tenant_id='tenant-main', workspace_id='prod', agent_id='bench', roles=('operator',), purpose='benchmark')
            for i in ids:
                t=time.perf_counter()
                try:
                    r=m.recall(f'how to calibrate device-{i} CAL-{i}', context=ctx, state={'mode':'repair'}, limit=1)
                    if r and f'device-{i}' in r[0].memory.content: ok+=1
                    else: errors.append(f'wrong:{i}')
                except Exception as e:
                    errors.append(type(e).__name__+':'+str(e))
                lat.append((time.perf_counter()-t)*1000)
    except Exception as e:
        errors.append('worker:'+type(e).__name__+':'+str(e))
    q.put({'lat':lat,'ok':ok,'errors':errors})


def write_worker(db, worker_id, count, q):
    lat=[]; ok=0; errors=[]
    try:
        with MemoryOS(db) as m:
            for start in range(0,count,25):
                items=[]
                for j in range(start,min(count,start+25)):
                    uid=f'w{worker_id}-{j}'
                    items.append({'content':f'worker memory {uid} production procedure unique-{uid}', 'memory_type':MemoryType.PROCEDURAL, 'confidence':.9, 'verification_status':VerificationStatus.MACHINE_CHECKED})
                t=time.perf_counter()
                try:
                    stored,rejected=m.bulk_ingest(tenant_id=f'tenant-{worker_id%2}', actor=f'worker-{worker_id}', source='concurrency', items=items, workspace_id='prod', trust_tier=TrustTier.TRUSTED_SYSTEM)
                    ok+=len(stored)
                    if rejected: errors.append(f'rejected:{len(rejected)}')
                except Exception as e:
                    errors.append(type(e).__name__+':'+str(e))
                lat.append((time.perf_counter()-t)*1000)
    except Exception as e:
        errors.append('worker:'+type(e).__name__+':'+str(e))
    q.put({'lat':lat,'ok':ok,'errors':errors})


def run_read(db, workers, total_queries):
    q=Queue(); procs=[]
    ids=list(range(total_queries))
    chunks=[ids[i::workers] for i in range(workers)]
    t=time.perf_counter()
    for chunk in chunks:
        p=Process(target=read_worker,args=(db,chunk,q)); p.start(); procs.append(p)
    results=[q.get() for _ in procs]
    for p in procs: p.join()
    q.close(); q.join_thread()
    elapsed=time.perf_counter()-t
    lat=[x for r in results for x in r['lat']]; errs=[x for r in results for x in r['errors']]
    return {'workers':workers,'queries':len(lat),'correct':sum(r['ok'] for r in results),'errors':len(errs),'error_samples':errs[:5],'elapsed_s':elapsed,'qps':len(lat)/elapsed,'p50_ms':pct(lat,.5),'p95_ms':pct(lat,.95),'p99_ms':pct(lat,.99),'max_ms':max(lat) if lat else None}


def run_write(db, workers, writes_per_worker):
    q=Queue(); procs=[]
    t=time.perf_counter()
    for w in range(workers):
        p=Process(target=write_worker,args=(db,w,writes_per_worker,q)); p.start(); procs.append(p)
    results=[q.get() for _ in procs]
    for p in procs:p.join()
    elapsed=time.perf_counter()-t
    lat=[x for r in results for x in r['lat']]; errs=[x for r in results for x in r['errors']]
    total=workers*writes_per_worker
    return {'workers':workers,'attempted':total,'stored':sum(r['ok'] for r in results),'errors':len(errs),'error_samples':errs[:8],'elapsed_s':elapsed,'writes_s':sum(r['ok'] for r in results)/elapsed,'batch_p50_ms':pct(lat,.5),'batch_p95_ms':pct(lat,.95),'batch_p99_ms':pct(lat,.99),'batch_max_ms':max(lat) if lat else None}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--seed',type=int,default=10000); ap.add_argument('--queries',type=int,default=400); ap.add_argument('--writes',type=int,default=200); ap.add_argument('--output',default='reports/concurrency_probe.json')
    a=ap.parse_args()
    d=tempfile.mkdtemp(prefix='bio-conc-'); db=str(Path(d)/'bench.db')
    t=time.perf_counter(); seed(db,a.seed); seed_s=time.perf_counter()-t
    report={'environment':{'cpu':os.cpu_count(),'python':os.sys.version,'seed_memories':a.seed,'seed_s':seed_s},'reads':[],'writes':[]}
    for w in (1,2,4,8):
        report['reads'].append(run_read(db,w,a.queries))
    for w in (1,2,4,8):
        wdb=str(Path(d)/f'write-{w}.db')
        # initialize schemas before racing workers
        with MemoryOS(wdb): pass
        report['writes'].append(run_write(wdb,w,a.writes))
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
