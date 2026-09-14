import os,pathlib,json,subprocess,re,fcntl,datetime
R=pathlib.Path(os.environ['SWEEP_ROOT']);T=pathlib.Path(os.environ.get('SWEEP_T3_ROOT', str(R/'t3')))
B=pathlib.Path(os.environ.get('SWEEP_PAIRED_ROOT', str(R/'paired')))
if os.environ.get('SWEEP_DRY_RUN')=='1':print('DRY sweep2-cleanup-check');raise SystemExit
markers=[str(R),str(T),str(B)]
alive=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid():continue
 try:cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
 except (OSError,ProcessLookupError):continue
 if any(m in cmd for m in markers):alive.append({'pid':int(p.name),'command':cmd})
portset={19755,29755,19601,29601,18980,18981,18982,18983}
for root in [R,T]:
 for f in root.rglob('ports.env'):
  for line in f.read_text(errors='replace').splitlines():
   if re.match(r'[A-Z0-9_]*PORT=',line):
    value=line.split('=',1)[1].strip().strip('"');
    if value.isdigit():portset.add(int(value))
listeners=subprocess.check_output(['ss','-H','-ltn'],text=True)
busy=[]
for line in listeners.splitlines():
 cols=line.split()
 if len(cols)>3:
  try:port=int(cols[3].rsplit(':',1)[1])
  except ValueError:continue
  if port in portset:busy.append(line)
mounts=subprocess.check_output(['findmnt','-rn','-o','TARGET,SOURCE'],text=True)
loops=subprocess.check_output(['sudo','-n','losetup','--json','--list'],text=True)
owned_mounts=[x for x in mounts.splitlines() if any(m in x for m in markers)]
owned_loops=[x for x in json.loads(loops).get('loopdevices',[]) if any(m in json.dumps(x) for m in markers)]
runids=set()
for f in R.rglob('manifest.json'):
 try:runids.add(json.loads(f.read_text()).get('run_id',''))
 except (ValueError,OSError):pass
runids.discard('')
rules=subprocess.check_output(['sudo','-n','iptables-save'],text=True)
owned_rules=[x for x in rules.splitlines() if any(runid in x for runid in runids)]
clean={}
for repo in ['product','harness']:
 path=os.environ['SWEEP_'+repo.upper().replace('-','_')+'_TREE']
 clean[repo]=subprocess.check_output(['git','-C',path,'status','--porcelain'],text=True)
lock=open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);free=True;fcntl.flock(lock,fcntl.LOCK_UN)
except BlockingIOError:free=False
remote_processes=subprocess.check_output(['ssh','-o','BatchMode=yes','testdev@192.168.1.181','ps -eo pid=,comm=,args='],text=True)
client_alive=[line for line in remote_processes.splitlines() if len(line.split(None,2))==3 and line.split(None,2)[1].startswith('step05-read-ben') and os.environ.get('SWEEP_PAIRED_REMOTE','/opt/work/codex02-sweep3-paired-'+os.environ['SWEEP_PRODUCT'][:8])+'/' in line]
result={'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'owned_processes_alive':alive,'M01_owned_clients_alive':client_alive,'recorded_ports_checked':sorted(portset),'recorded_ports_busy':busy,'owned_mounts':owned_mounts,'owned_loops':owned_loops,'owned_iptables_rules':owned_rules,'source_status':clean,'lab_flock_reacquired':free,'scope':'read-only audit of this sweep; no arbitrary cleanup','reservation':'physical cleanup audit; queue handoff is stated separately in the RESULT'}
(R/'cleanup-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
