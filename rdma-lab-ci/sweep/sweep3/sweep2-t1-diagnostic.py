import pathlib,subprocess,os,fcntl,json,datetime
R=pathlib.Path('/data/nvme/testdev/codex02-integration-ac7f4e0f0-20260913')
env=dict(os.environ,PATH='/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin',WEED_BINARY=str(R/'bin/weed'),RUST_VOLUME_BINARY='/opt/work/bin/weed-volume-ac7f4e0f08716ecc0ed1b418b5f26fdfb752c34f-rdma')
with open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 out=R/'t1-exact-command-diagnostic.log';assert not out.exists()
 args=['go','test','-count=1','-v','./weed/shell','-run','^TestCacheVolumeLifecycleProcesses$']
 with out.open('w') as f:r=subprocess.run(args,cwd='/opt/work/codex02-sweep2-product/enterprise',env=env,stdout=f,stderr=subprocess.STDOUT,timeout=1200)
 (R/'t1-diagnostic.json').write_text(json.dumps({'command':args,'exit':r.returncode,'reason':'one exact-command diagnostic to capture stdout omitted by failed exec action; original gate preserved','finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2))
 print('T1 diagnostic exit='+str(r.returncode),flush=True)
