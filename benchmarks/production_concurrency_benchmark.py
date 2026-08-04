from __future__ import annotations
import json, os, platform, random, sqlite3, tempfile, time
from multiprocessing import Process, Queue
from multiprocessing.connection import Listener, Client
from pathlib import Path
from bio_agent_os import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType, TrustTier, VerificationStatus
from benchmarks.concurrency_probe import seed, run_read, run_write, pct

AUTH=b'bio-bench'

def mixed_worker(db, wid, ops, q):
    reads=[]; writes=[]; errors=[]; correct=0; stored=0; leaks=0
    tenant=f'tenant-mix-{wid%4}'
    try:
      with MemoryOS(db) as m:
        ctx=AccessContext(tenant_id='tenant-main',workspace_id='prod',agent_id=f'a{wid}',roles=('operator',),purpose='benchmark')
        for i in range(ops):
          if i%5:
            target=(wid*ops+i)%10000; t=time.perf_counter()
            try:
              r=m.recall(f'calibrate device-{target} CAL-{target}',context=ctx,state={'mode':'repair'},limit=3)
              correct += int(bool(r and f'device-{target}' in r[0].memory.content))
            except Exception as e: errors.append(type(e).__name__+':'+str(e))
            reads.append((time.perf_counter()-t)*1000)
          else:
            uid=f'mix-{wid}-{i}'; t=time.perf_counter()
            try:
              a,b=m.bulk_ingest(tenant_id=tenant,actor=f'w{wid}',source='mixed',workspace_id='prod',trust_tier=TrustTier.TRUSTED_SYSTEM,items=[{'content':f'private {uid} secret procedure','memory_type':MemoryType.PROCEDURAL,'verification_status':VerificationStatus.MACHINE_CHECKED}])
              stored+=len(a)
            except Exception as e: errors.append(type(e).__name__+':'+str(e))
            writes.append((time.perf_counter()-t)*1000)
        # cross-tenant leak attempt
        foreign=AccessContext(tenant_id=f'tenant-mix-{(wid+1)%4}',workspace_id='prod',agent_id='x',roles=('operator',),purpose='benchmark')
        r=m.recall(f'private mix-{wid}-0 secret procedure',context=foreign,limit=5)
        leaks += sum(1 for x in r if f'mix-{wid}-0' in x.memory.content)
    except Exception as e: errors.append('worker:'+type(e).__name__+':'+str(e))
    q.put({'reads':reads,'writes':writes,'errors':errors,'correct':correct,'stored':stored,'leaks':leaks})

def run_mixed(db, workers, ops):
    q=Queue(); ps=[]; t=time.perf_counter()
    for w in range(workers):
      p=Process(target=mixed_worker,args=(db,w,ops,q));p.start();ps.append(p)
    rs=[q.get() for _ in ps]
    for p in ps:p.join()
    q.close(); q.join_thread()
    elapsed=time.perf_counter()-t; rl=[x for r in rs for x in r['reads']]; wl=[x for r in rs for x in r['writes']]; errs=[x for r in rs for x in r['errors']]
    return {'workers':workers,'operations':workers*ops,'read_ops':len(rl),'write_ops':len(wl),'read_correct':sum(r['correct'] for r in rs),'stored':sum(r['stored'] for r in rs),'tenant_leaks':sum(r['leaks'] for r in rs),'errors':len(errs),'error_samples':errs[:5],'elapsed_s':elapsed,'ops_s':workers*ops/elapsed,'read_p50_ms':pct(rl,.5),'read_p95_ms':pct(rl,.95),'read_p99_ms':pct(rl,.99),'write_p50_ms':pct(wl,.5),'write_p95_ms':pct(wl,.95),'write_p99_ms':pct(wl,.99)}

def node_server(db, address, ready):
  with MemoryOS(db) as m:
    listener=Listener(address,authkey=AUTH); ready.put(address)
    running=True
    while running:
      conn=listener.accept()
      try:
        while True:
          msg=conn.recv(); cmd=msg['cmd']
          if cmd=='shutdown': running=False; conn.send({'ok':True}); break
          if cmd=='recall':
            ctx=AccessContext(tenant_id='tenant-main',workspace_id='prod',agent_id='rpc',roles=('operator',),purpose='benchmark')
            r=m.recall(msg['query'],context=ctx,state={'mode':'repair'},limit=1)
            conn.send({'ok':True,'content':r[0].memory.content if r else ''})
          elif cmd=='write':
            a,b=m.bulk_ingest(tenant_id=msg['tenant'],actor='rpc',source='cluster',workspace_id='prod',trust_tier=TrustTier.TRUSTED_SYSTEM,items=[{'content':msg['content'],'memory_type':MemoryType.PROCEDURAL,'verification_status':VerificationStatus.MACHINE_CHECKED}])
            conn.send({'ok':True,'stored':len(a)})
      except EOFError: pass
      finally: conn.close()
    listener.close()

def rpc_client(address, cid, ops, q):
  lat=[]; errors=[]; correct=0; writes=0
  try:
    c=Client(address,authkey=AUTH)
    for i in range(ops):
      t=time.perf_counter()
      try:
        if i%10==0:
          content=f'rpc private client-{cid}-{i}';c.send({'cmd':'write','tenant':f'rpc-{cid%2}','content':content});res=c.recv();writes+=res.get('stored',0)
        else:
          target=(cid*ops+i)%10000;c.send({'cmd':'recall','query':f'calibrate device-{target} CAL-{target}'});res=c.recv();correct+=int(f'device-{target}' in res.get('content',''))
      except Exception as e: errors.append(type(e).__name__+':'+str(e))
      lat.append((time.perf_counter()-t)*1000)
    c.close()
  except Exception as e: errors.append('client:'+type(e).__name__+':'+str(e))
  q.put({'lat':lat,'errors':errors,'correct':correct,'writes':writes})

def run_cluster(db,nodes=3,clients=6,ops=60):
  ready=Queue(); servers=[]; base=24000+random.randint(0,1000); addresses=[]
  for n in range(nodes):
    addr=('127.0.0.1',base+n);p=Process(target=node_server,args=(db,addr,ready));p.start();servers.append(p)
  addresses=[ready.get() for _ in servers]
  ready.close(); ready.join_thread()
  q=Queue(); cps=[];t=time.perf_counter()
  for c in range(clients):
    p=Process(target=rpc_client,args=(addresses[c%nodes],c,ops,q));p.start();cps.append(p)
  rs=[q.get() for _ in cps]
  for p in cps:p.join()
  q.close(); q.join_thread()
  elapsed=time.perf_counter()-t
  for addr in addresses:
    try:
      c=Client(addr,authkey=AUTH);c.send({'cmd':'shutdown'});c.recv();c.close()
    except Exception: pass
  for p in servers:p.join(timeout=10)
  lat=[x for r in rs for x in r['lat']];errs=[x for r in rs for x in r['errors']]
  return {'nodes':nodes,'clients':clients,'operations':len(lat),'correct_reads':sum(r['correct'] for r in rs),'writes':sum(r['writes'] for r in rs),'errors':len(errs),'error_samples':errs[:5],'elapsed_s':elapsed,'ops_s':len(lat)/elapsed,'rpc_p50_ms':pct(lat,.5),'rpc_p95_ms':pct(lat,.95),'rpc_p99_ms':pct(lat,.99),'rpc_max_ms':max(lat)}

def crash_worker(db,q):
  with MemoryOS(db) as m:
    for chunk in range(100):
      items=[{'content':f'crash-{chunk}-{i} durable memory','memory_type':MemoryType.SEMANTIC} for i in range(25)]
      m.bulk_ingest(tenant_id='crash',actor='crasher',source='crash',items=items,workspace_id='prod')
      q.put(chunk)
      time.sleep(.02)

def crash_recovery(db):
  with MemoryOS(db):pass
  q=Queue();p=Process(target=crash_worker,args=(db,q));p.start()
  committed=-1
  while committed<4: committed=q.get(timeout=5)
  p.terminate();p.join()
  conn=sqlite3.connect(db); integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
  events=conn.execute("SELECT count(*) FROM cognitive_events WHERE tenant_id='crash'").fetchone()[0]
  memories=conn.execute("SELECT count(*) FROM cognitive_memories WHERE tenant_id='crash'").fetchone()[0]
  conn.close()
  with MemoryOS(db) as m: chain=m.events.verify_chain('crash')
  return {'terminated_after_chunk_at_least':committed,'integrity_check':integrity,'event_rows':events,'memory_rows':memories,'projection_parity':events==memories,'checksum_chain_valid':chain}

def main():
  """Stable smoke reproduction. The published high-load report was run phase-by-phase."""
  root=Path(tempfile.mkdtemp(prefix='bio-prod-smoke-')); db=str(root/'shared.db')
  t=time.perf_counter(); seed(db,10000); seed_s=time.perf_counter()-t
  report={'environment':{'cpu_count':os.cpu_count(),'python':platform.python_version(),'backend':'SQLite WAL','seed_memories':10000,'seed_s':seed_s},'read_concurrency':[],'write_concurrency':[],'mixed':[]}
  for w in (1,2,4,8):
    result=run_read(db,w,200); report['read_concurrency'].append(result); print('read',w,result,flush=True)
  for w in (1,2,4,8):
    wdb=str(root/f'write-{w}.db')
    with MemoryOS(wdb): pass
    result=run_write(wdb,w,300); report['write_concurrency'].append(result); print('write',w,result,flush=True)
  for w in (2,4,8):
    result=run_mixed(db,w,40); report['mixed'].append(result); print('mixed',w,result,flush=True)
  report['distributed_process_cluster']=run_cluster(db,3,3,30)
  report['crash_recovery']=crash_recovery(str(root/'crash.db'))
  out=Path('reports/production_concurrency_smoke_v081.json'); out.parent.mkdir(exist_ok=True); out.write_text(json.dumps(report,indent=2),encoding='utf-8')
  print(json.dumps(report,indent=2))

if __name__=='__main__':main()
