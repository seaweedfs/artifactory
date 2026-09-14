import datetime,fcntl,hashlib,json,os,pathlib,re,shutil,signal,subprocess,time
R=pathlib.Path(os.environ['SWEEP_ROOT'])
OUT=R/'privileged-551-configured';CASE=OUT/'data'
PRODUCT=os.environ['SWEEP_PRODUCT']
HARNESS=os.environ['SWEEP_HARNESS']
W=os.environ.get('SWEEP_WIKI','sweep3-unpublished')
if os.environ.get('SWEEP_DRY_RUN')=='1':print('DRY sweep2-551-configured');raise SystemExit
ports={19865,29865,19711,29711,19712,29712,19888,29888,19933,19934}
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def command(args,**kw):return subprocess.run(args,check=True,**kw)
with open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert not OUT.exists() and CASE.resolve().is_relative_to(R.resolve())
 assert subprocess.run(['sudo','-n','iptables','-C','INPUT','-p','tcp','--dport','29711','-j','DROP'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==1
 before_rules=subprocess.check_output(['sudo','-n','iptables-save'],text=True)
 for line in subprocess.check_output(['ss','-ltnH'],text=True).splitlines():
  assert int(line.split()[3].rsplit(':',1)[1]) not in ports,'port occupied'
 assert subprocess.run(['pgrep','-x','weed'],stdout=subprocess.DEVNULL).returncode==1,'other lab process'
 key=pathlib.Path('/home/testdev/.ssh/id_ed25519');assert key.is_file()
 for exe in ['aws','grpcurl','curl']:assert shutil.which(exe),exe
 weed=R/'bin/weed';runner=R/'bin/sw-test-runner'
 assert weed.is_file()
 assert runner.is_file()
 OUT.mkdir();(OUT/'rules-before.txt').write_text(before_rules)
 (OUT/'binary-hashes.json').write_text(json.dumps({str(p):sha(p) for p in (weed,runner)},indent=2))
 source=subprocess.check_output(['git','-C',os.environ.get('SWEEP_HARNESS_TREE','/opt/work/codex02-sweep2-harness'),'show',HARNESS+':enterprise/testops/scenarios/gate-551-delete-bucket-name-reuse.yaml'])
 scenario=OUT/'scenario.yaml';scenario.write_bytes(source)
 env={'ssh_key':str(key),'weed_bin':str(weed),'run':str(CASE),'ip':'10.0.0.3','proto_dir':os.environ.get('SWEEP_PRODUCT_TREE','/opt/work/codex02-sweep2-product')+'/enterprise/weed/pb',
 '__testops_executor_host':'192.168.1.184','__testops_executor_user':'testdev','__testops_ssh_key':str(key),
 '__testops_binary':str(weed),'__testops_run_dir':str(CASE),'__testops_proto_dir':os.environ.get('SWEEP_PRODUCT_TREE','/opt/work/codex02-sweep2-product')+'/enterprise/weed/pb',
 '__testops_activity_log':os.environ['TESTOPS_ACTIVITY_LOG']}
 args=[str(runner),'run','-allow-mutating','-results-dir',str(OUT/'results'),'-output',str(OUT/'result.json')]
 for k,v in env.items():args.extend(['-env',k+'='+v])
 for k,v in {'product_commit':PRODUCT,'harness_commit':HARNESS,'run_by':'codex02','scope':'privileged/synthetic-fault fixture','expected_reference':'RED','wiki':W}.items():args.extend(['-meta',k+'='+v])
 args.append(str(scenario))
 (OUT/'command.json').write_text(json.dumps(args,indent=2))
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with open(os.environ['TESTOPS_ACTIVITY_LOG'],'a') as activity:activity.write('START codex02 sweep privileged gate-551 '+started+' root='+str(OUT)+' evidence='+W+'\n')
 try:
  with (OUT/'console.log').open('w') as log:result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=240)
  print(json.dumps({'runner_exit':result.returncode,'evidence':str(OUT)}),flush=True)
 finally:
  # Only processes whose command or stdout is bound to this newly-created case.
  killed=[]
  for p in pathlib.Path('/proc').iterdir():
   if not p.name.isdigit() or int(p.name)==os.getpid():continue
   try:
    cmd=(p/'cmdline').read_bytes();stdout=os.readlink(p/'fd/1')
    owned=str(CASE).encode() in cmd or stdout.startswith(str(CASE)+'/')
    if not owned:continue
    pidfd=os.pidfd_open(int(p.name));signal.pidfd_send_signal(pidfd,signal.SIGTERM);os.close(pidfd);killed.append(int(p.name))
   except (FileNotFoundError,ProcessLookupError,PermissionError):continue
  if killed:time.sleep(2)
  # The exact rule was absent before the run and created only by this fixture.
  while subprocess.run(['sudo','-n','iptables','-C','INPUT','-p','tcp','--dport','29711','-j','DROP'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
   command(['sudo','-n','iptables','-D','INPUT','-p','tcp','--dport','29711','-j','DROP'])
  after_rules=subprocess.check_output(['sudo','-n','iptables-save'],text=True);(OUT/'rules-after.txt').write_text(after_rules)
  canonical=lambda s:[re.sub(r'\[\d+:\d+\]','[counter]',x) for x in s.splitlines() if not x.startswith('#')]
  assert canonical(before_rules)==canonical(after_rules),'unrelated rules differ'
  busy=[l for l in subprocess.check_output(['ss','-ltnH'],text=True).splitlines() if int(l.split()[3].rsplit(':',1)[1]) in ports]
  assert not busy,busy
  (OUT/'cleanup.json').write_text(json.dumps({'ports_clear':sorted(ports),'rules_restored':True,'owned_extra_processes_terminated':killed,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2))
  with open(os.environ['TESTOPS_ACTIVITY_LOG'],'a') as activity:activity.write('END codex02 sweep privileged gate-551 '+datetime.datetime.now(datetime.timezone.utc).isoformat()+' evidence='+W+'\n')
