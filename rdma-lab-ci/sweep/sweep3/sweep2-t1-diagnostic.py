import pathlib,subprocess,os,fcntl,json,datetime
R=pathlib.Path(os.environ['SWEEP_ROOT'])
env=dict(os.environ,PATH='/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin',WEED_BINARY=str(R/'bin/weed'),RUST_VOLUME_BINARY=os.environ.get('SWEEP_VOLUME_BIN', f"/opt/work/bin/weed-volume-{os.environ['SWEEP_PRODUCT']}-sweep-rdma"))
if os.environ.get('SWEEP_DRY_RUN')=='1':print('DRY sweep2-t1-diagnostic');raise SystemExit
with open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 out=R/'t1-exact-command-diagnostic.log';assert not out.exists()
 args=['go','test','-count=1','-v','./weed/shell','-run','^TestCacheVolumeLifecycleProcesses$']
 with out.open('w') as f:r=subprocess.run(args,cwd=os.environ['SWEEP_PRODUCT_TREE']+'/enterprise',env=env,stdout=f,stderr=subprocess.STDOUT,timeout=1200)
 (R/'t1-diagnostic.json').write_text(json.dumps({'command':args,'exit':r.returncode,'reason':'one exact-command diagnostic to capture stdout omitted by failed exec action; original gate preserved','finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2))
 print('T1 diagnostic exit='+str(r.returncode),flush=True)
