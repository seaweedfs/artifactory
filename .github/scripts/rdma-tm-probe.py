"""No-service runner facts and exact reviewed BUILD/PROBE association."""
import ipaddress,json,os,pathlib,re,signal,time,socket,http.client

BUILD_SHA='a71564f7a85a6a5304cde977239f19d21f5bd1b8'
MONO_SHA='8c22388d091484dd02dd78fc86f9b564baffbb36'
BUILD_REF=dict(run_id=37359833660,attempt=1,workflow_id=302349806,head_sha=BUILD_SHA,actor_id=6647175,artifact_id=11365694398,name='tm-connected-build-'+MONO_SHA+'-37359833660-1')
PRODUCTS=dict(loader='28bf1741053ada3f64b8fef74cd33bb1cf5501ff1cd1a2a11e25b14f32057523',server='e9dacec10841244ba2180a5a3a167dc1a785f485031faaa798f64f331daeb7eb',master='1a9630a7e5aa436b34df723ad601a04f1c06e815baf9d700eef5690d574ce15b')
BUILD_JSON='9bded3c5422e6e03e1a6d128879bf105752c6ad5b769a8c7f81af3016bb311af'
LIST_SHA='656ff488ae363a21a4473ed87509381a6a7a7c565a14b8698121a32adf4db226'

def exact_build(a,bind,env):
    if bind.get('profile')=='r2-recovery-v1':
        a.require(bind['source_sha']==env['TM_SOURCE_SHA'] and bind['ci_sha']==env['GITHUB_SHA'] and bind['build_ci_sha']==bind['build_reference']['head_sha']=='3f4543ab47959d86bea2deefb18193474af5ef58' and bind['build_reference']['run_id']==37449089123,'R2_BUILD_BIND_DRIFT')
        a.require(set(bind['product_hashes'])=={'loader','server','master'} and all(a.HEX64.fullmatch(v) for v in bind['product_hashes'].values()),'R2_BUILD_PRODUCTS');return
    a.require(bind['source_sha']==env['TM_SOURCE_SHA']==MONO_SHA and bind['ci_sha']==env['GITHUB_SHA'],'SOURCE_OR_ADAPTER_BIND_DRIFT')
    a.require(bind['build_ci_sha']==BUILD_SHA and bind['build_reference']==BUILD_REF,'UNAPPROVED_PRIOR_BUILD')
    a.require(bind['product_hashes']==PRODUCTS and bind['build_json_sha256']==BUILD_JSON and bind['list_sha256']==LIST_SHA,'BUILD_PROVENANCE_DRIFT')

def prepare(a,out,env):
    deadline=time.monotonic()+180;api=a.Actions(env['GITHUB_REPOSITORY'],env['GH_TOKEN'],out)
    event=json.loads(pathlib.Path(env['GITHUB_EVENT_PATH']).read_text());request=json.loads(event['inputs']['diagnostic_objects'])
    a.require(env.get('TM_MANAGER_ACTOR_ID') and str(event['sender']['id'])==env['TM_MANAGER_ACTOR_ID']==env.get('GITHUB_ACTOR_ID'),'FOREIGN_MANAGER_DISPATCH')
    if env.get('TM_PROFILE')=='r2-recovery-v1':
        raw=a.base64.b64decode(request['bind_b64'],validate=True);a.require(len(raw)<=65536,'R2_INLINE_BIND_SIZE');root=out/'input';root.mkdir();root.joinpath('bind.json').write_bytes(raw)
        for name in ('rdma-tm-diagnostic.py','rdma-tm-decode.py','rdma-tm-probe.py'):root.joinpath(name).write_bytes(a.HERE.joinpath(name).read_bytes())
        a.seal(root);a.manifest(root,request['input_manifest'])
    else:api.artifact(request['input'],out/'input',deadline);a.manifest(out/'input',request['input_manifest'])
    bind=json.loads((out/'input/bind.json').read_text());a.require('local_inert_fixture' not in bind,'PRIVATE_FIXTURE_FORBIDDEN');exact_build(a,bind,env)
    r2=bind.get('profile')=='r2-recovery-v1';mono=env['TM_SOURCE_SHA'] if r2 else MONO_SHA;products=bind['product_hashes'] if r2 else PRODUCTS;build_json=bind['build_json_sha256'] if r2 else BUILD_JSON;listing=bind['list_sha256'] if r2 else LIST_SHA
    if r2:bind['root']=str(pathlib.Path(env['GITHUB_WORKSPACE']).resolve()/('codex03-tm-r2-'+bind['run_id']))
    a.require(bind['phase']==env['TM_PHASE'] and bind['phase'] in ('probe','run'),'PHASE_BIND_DRIFT')
    for name in ('rdma-tm-diagnostic.py','rdma-tm-decode.py','rdma-tm-probe.py'):a.require(a.sha(out/'input'/name)==a.sha(a.HERE/name),'ADAPTER_DRIFT')
    a.authorize(bind,request,env,event['sender']['id']);a.save(out/'authority-association.json',dict(launch_id=request['launch_id'],input_digest=request['input_manifest'],actor_id=event['sender']['id'],ci_sha=env['GITHUB_SHA'],source_sha=env['TM_SOURCE_SHA'],run_id=bind['run_id']))
    run,_=api.artifact(bind['build_reference'],out/'build',deadline);a.manifest(out/'build')
    info=json.loads((out/'build/build.json').read_text())
    a.require(info['ci_sha']==run['head_sha']==bind['build_ci_sha'] and info['source_sha']==mono and a.sha(out/'build/build.json')==build_json,'BUILD_BIND_DRIFT')
    if r2:a.require(info.get('profile')=='r2-recovery-v1' and info.get('client_package')=='seaweedfs-sw-rdma-vfs' and info.get('product_tree')==a.R2_TREE,'R2_WRONG_PRODUCER')
    a.require(info['run_id']==run['id'] and info['attempt']==run['run_attempt'] and info['job']=='tm-connected-diagnostic','BUILD_JOB_ASSOCIATION')
    jobs=api.get('/repos/'+api.repository+'/actions/runs/'+str(run['id'])+'/attempts/'+str(run['run_attempt'])+'/jobs?per_page=100',deadline)
    a.require(jobs['total_count']<100 and len([j for j in jobs['jobs'] if j['name']==info['job'] and j['runner_name']==info['runner'] and j['conclusion']=='success'])==1,'BUILD_RUNNER_ASSOCIATION')
    for label,digest in products.items():
        row=info['products'][label];a.require(row['file']==label+'.elf' and row['source_sha']==mono and row['sha256']==a.sha(out/'build'/row['file'])==digest,'BUILD_ELF_DRIFT');(out/'build'/row['file']).chmod(0o700)
    a.require(a.sha(out/'build/loader-list.stdout.raw')==info['list_sha256']==listing,'BUILD_LIST_DRIFT');a.exact_row((out/'build/loader-list.stdout.raw').read_bytes(),a.decoder.R2_ROW if r2 else a.decoder.ROW)
    a.require(bind.get('profile','tm-connected-v1')==env.get('TM_PROFILE','tm-connected-v1'),'PROFILE_BIND_DRIFT')
    return bind,api,deadline

def object_identity(a,path):
    p=pathlib.Path(path).resolve(strict=True);before=p.stat();digest=a.sha(p);after=p.stat()
    a.require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'PROBE_OBJECT_DRIFT')
    return dict(path=str(p),dev=f'{os.major(before.st_dev):02x}:{os.minor(before.st_dev):02x}',inode=before.st_ino,sha256=digest)

def network(a,sysfs,links,addresses,out=None):
    matches=re.findall(r'\bsiw0/1\s+[^\n]*?\bnetdev\s+(\S+)',links);a.require(len(set(matches))==1,'PROBE_NETDEV_AMBIGUOUS')
    netdev=matches[0];ips={v['local'] for row in addresses if row['ifname']==netdev for v in row.get('addr_info',[]) if v['family']=='inet'}
    r2=a.recovery(os.environ);devices=[row for row in addresses if row['ifname']==netdev];mac=devices[0].get('address','') if len(devices)==1 else ''
    if r2:a.require(len(devices)==1 and len(ips)==1 and re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac),'PROBE_SIW_NETDEV_IP_OR_MAC')
    candidates=[];raw_rows=[]
    try:
        for f in sorted((sysfs/'siw0/ports/1/gids').iterdir()):
            row=dict(gid_path=str(f),netdev_path=str(sysfs/'siw0/ports/1/gid_attrs/ndevs'/f.name));raw_rows.append(row)
            row['gid']=gid=f.read_text().strip();row['netdev']=ndev=pathlib.Path(row['netdev_path']).read_text().strip()
            address=ipaddress.IPv6Address(gid);mapped=address.ipv4_mapped
            if r2:
                if f.name=='0' and ndev==netdev and address.packed==bytes.fromhex(mac.replace(':',''))+bytes(10):candidates.append(dict(gid=gid,gid_index=0,netdev=netdev,ip=str(ipaddress.IPv4Address(next(iter(ips)))),gid_binding='SIW_MAC_NETDEV_IPV4'))
            elif mapped and str(mapped) in ips and ndev==netdev:candidates.append(dict(gid=gid,gid_index=int(f.name),netdev=netdev,ip=str(mapped)))
        a.require(len(candidates)==1,'PROBE_GID_MAPPING_MISSING_OR_AMBIGUOUS');return dict(candidates[0],gid_rows=raw_rows)
    finally:
        if out is not None:a.save(out/'sysfs-gid-observations.json',dict(rows=raw_rows,links=links,addresses=addresses))

def root_access(a,bind,out,deadline,work='/opt/work'):
    r2=bind.get('profile')=='r2-recovery-v1';work=os.environ['GITHUB_WORKSPACE'] if r2 else work;target=a.owned_root(bind['root'],bind['lock_path'],work,'/opt/work' if r2 else None);base=pathlib.Path(work).resolve(strict=True)
    a.require(base.is_dir() and time.monotonic()<deadline,'PROBE_RUN_BASE_OR_DEADLINE')
    lock=pathlib.Path(bind['lock_path']) if r2 else base/'siw-lab.lock';fd=None;made=False;created=False;primary=None;cleanup_errors=[];record=dict(base=str(base),root=str(target),lock=str(lock),uid=os.getuid(),free_bytes=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize)
    try:
        a.require(not lock.is_symlink(),'PROBE_LOCK_ALIAS');created=not lock.exists()
        fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600);s=os.fstat(fd)
        a.require((s.st_uid==os.getuid() or r2) and (s.st_dev,s.st_ino)==(lock.stat().st_dev,lock.stat().st_ino),'PROBE_LOCK_OWNER_OR_DRIFT')
        record['lock_identity']=dict(dev=s.st_dev,inode=s.st_ino,uid=s.st_uid);target.mkdir(mode=0o700);made=True
        f=target/'admission';probe=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            a.require(os.write(probe,b'probe')==5,'PROBE_WRITE_SHORT');os.fsync(probe);st=os.fstat(probe)
            a.require(st.st_uid==os.getuid() and (st.st_dev,st.st_ino)==(f.stat().st_dev,f.stat().st_ino),'PROBE_ROOT_IDENTITY')
            record['root_identity']=dict(dev=target.stat().st_dev,inode=target.stat().st_ino,uid=target.stat().st_uid)
        finally:os.close(probe)
        f.unlink();target.rmdir();made=False;a.require(time.monotonic()<deadline,'PROBE_DEADLINE');record['state']='PASS'
    except BaseException as error:primary=error;record['primary_refusal']=repr(error);raise
    finally:
        # Only remove our verified fresh scratch entries. A cleanup tail grants
        # no producer time; an expired producer remains a refusal after cleanup.
        signal.setitimer(signal.ITIMER_REAL,5)
        if made:
            try:
                a.require(target.stat().st_uid==os.getuid() and not target.is_symlink(),'PROBE_CLEANUP_OWNER')
                f=target/'admission'
                if f.exists():a.require(f.stat().st_uid==os.getuid() and not f.is_symlink(),'PROBE_CLEANUP_ALIAS');f.unlink()
                target.rmdir();made=False
            except BaseException as error:cleanup_errors.append(repr(error))
        if fd is not None:os.close(fd)
        # A preexisting lock is never removed, changed or certified reserved.
        record['cleanup']=dict(scratch_absent=not target.exists(),created_lock=created,lock_retained=True,reservation='NONE',errors=cleanup_errors)
        a.save(out/'run-root-access.json',record)
        signal.setitimer(signal.ITIMER_REAL,max(.000001,deadline-time.monotonic()))
        if primary is None:a.require(not made and not target.exists(),'PROBE_SCRATCH_CLEANUP_UNPROVEN')
    return record

def plans(a,build,bind,facts):
    help_raw=(build/'server-help.stdout.raw').read_text()+(build/'server-help.stderr.raw').read_text()
    needed=['--port','--port.grpc','--ip','--ip.bind','--master','--dir','--max','--rdma.enabled','--rdma.ip','--rdma.port']
    a.require(all(re.search(re.escape(flag)+r'(?:\s|$)',help_raw) for flag in needed),'PROBE_SERVER_PLAN_UNSUPPORTED')
    a.require('master' in (build/'master-help.stdout.raw').read_text(),'PROBE_MASTER_PLAN_UNSUPPORTED')
    if bind.get('profile')=='r2-recovery-v1':
        a.require(all(flag in help_raw for flag in ('--metricsPort','--metricsIp')),'R2_METRICS_FLAGS_UNSUPPORTED')
        rows={'master':dict(argv=['${BUILD}/master.elf','master','-ip=127.0.0.1','-port=46243','-port.grpc=56243','-volumeSizeLimitMB=64','-mdir=${ROOT}/master/data'],env={}),'server':dict(argv=['${BUILD}/server.elf','--ip',facts['ip'],'--ip.bind','127.0.0.1','--port','46240','--port.grpc','46241','--master','127.0.0.1:46243','--dir','${ROOT}/server/data','--max','4','--rdma.enabled','--rdma.ip',facts['ip'],'--rdma.port','46242','--metricsPort','46244','--metricsIp','127.0.0.1'],env={})}
        wrapper=lambda role,phase:dict(role=role,argv=['python3','-B','${INPUT}/rdma-tm-diagnostic.py','service','${ROOT}/r2-service-bundle.json',role,phase],env={})
        probes=[dict(argv=['curl','--fail','--max-time','1',*(['--retry','30','--retry-connrefused','--retry-max-time','14'] if '/vol/grow' not in url else []),url],env={}) for url in ['http://127.0.0.1:46243/dir/status','http://127.0.0.1:46240/status','http://127.0.0.1:46243/vol/grow?count=1&replication=000']]
        return dict(state='ARTIFACT_HELP_DERIVED_PLANS_NOT_EXECUTED',r2_services=rows,setup=[wrapper(r,'up') for r in ('master','server')],down=[wrapper(r,'down') for r in ('server','master')],probes=probes,test_env=dict(TM_RDMA_ADDR=facts['ip']+':46242',TM_CONTROL_ADDR='127.0.0.1:46241'))
    return dict(state='ARTIFACT_HELP_DERIVED_PLANS_NOT_EXECUTED',producer_hashes={name:a.sha(build/name) for name in ('server-help.stdout.raw','server-help.stderr.raw','master-help.stdout.raw')},setup=[dict(role='master',binary_hash=PRODUCTS['master'],argv=['${BUILD}/master.elf','master','-ip=127.0.0.1','-port=46243','-port.grpc=56243','-mdir=${ROOT}/master'],flag_help='probe-master-flags.stdout.raw + stderr.raw'),dict(role='server',binary_hash=PRODUCTS['server'],argv=['${BUILD}/server.elf','--ip',facts['ip'],'--ip.bind','127.0.0.1','--port','46240','--port.grpc','46241','--master','127.0.0.1:46243','--dir','${ROOT}/server','--max','4','--rdma.enabled','--rdma.ip',facts['ip'],'--rdma.port','46242'])],down=[dict(role=role,argv=['kill','-TERM','--','-${ENROLLED_'+role.upper()+'_SID}'],identity='RUN PID/starttime enrollment mandatory; checked guardian census/reap required') for role in ('server','master')],probes=[dict(role='master',argv=['curl','--fail','--max-time','${REMAINING_SECONDS}','http://127.0.0.1:46243/dir/status']),dict(role='server',argv=['curl','--fail','--max-time','${REMAINING_SECONDS}','http://127.0.0.1:46240/status'])],runtime_renderer='NOT_RUN: direct spawn/owned SID plans require reviewed existing guardian enrollment wrapper before RUN INPUT; no plan is an observed service success')

def produce(a,bind,out,env,api):
    start=time.monotonic();deadline=start+20;previous=signal.getsignal(signal.SIGALRM)
    def expired(signum,frame):raise ValueError('PROBE_PRODUCER_DEADLINE')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,20)
    r2=bind.get('profile')=='r2-recovery-v1';record=dict(state='FAIL',phase='PROBE_ONLY',ci_sha=env['GITHUB_SHA'],source_sha=env['TM_SOURCE_SHA'] if r2 else MONO_SHA,build_ci_sha=bind['build_ci_sha'] if r2 else BUILD_SHA,run_id=int(env['GITHUB_RUN_ID']),attempt=int(env['GITHUB_RUN_ATTEMPT']),job=env['GITHUB_JOB'],runner=env['RUNNER_NAME'],hostname=socket.gethostname(),uid=os.getuid(),scope='INSTALLED_HOST_FACTS_NOT_IN_CHILD',producer_start=start,deadline=deadline)
    def run(label,argv):return a.command(argv,out,'probe-'+label,deadline).decode()
    try:
        record['kernel_release']=run('kernel',['uname','-r']).strip();record['loaded_modules']=pathlib.Path('/proc/modules').read_text()
        a.require(re.search(r'^siw\s',record['loaded_modules'],re.M),'PROBE_SIW_NOT_LOADED')
        module=run('siw-module',['modinfo','siw']);record['siw_module_required_lines']=[line for line in module.splitlines() if line.split(':',1)[0] in ('filename','version','srcversion','vermagic')]
        a.require(any(line.startswith('srcversion:') for line in record['siw_module_required_lines']),'PROBE_MODULE_IDENTITY_MISSING')
        links=run('rdma-link',['rdma','link','show']);addresses=json.loads(run('IP',['ip','-j','addr','show']))
        facts=network(a,pathlib.Path('/sys/class/infiniband'),links,addresses,out);record.update(facts)
        a.require(run('memlock',['bash','-c','ulimit -l']).strip()=='unlimited','PROBE_MEMLOCK')
        devices=run('devices',['ibv_devinfo','-v']);a.require('siw0' in devices and facts['gid'] in devices,'PROBE_DEVICE_GID')
        ld=run('ldconfig',['ldconfig','-p']);objects={}
        for library in ('libibverbs','librdmacm'):
            paths={str(pathlib.Path(v).resolve()) for v in re.findall(r'\b'+library+r'\.so(?:\.\d+)*\s+[^\n]*=>\s+(\S+)',ld)}
            a.require(len(paths)==1,'PROBE_PROVIDER_AMBIGUOUS_'+library);objects[library]=object_identity(a,next(iter(paths)))
        candidates={str(f.resolve()) for directory in {pathlib.Path(objects['libibverbs']['path']).parent/'libibverbs',pathlib.Path('/usr/lib64/libibverbs')} for f in directory.glob('libsiw*.so*')}
        a.require(len(candidates)==1,'PROBE_PROVIDER_AMBIGUOUS_libsiw');objects['libsiw']=object_identity(a,next(iter(candidates)));record['provider_objects']=objects
        prefix='/repos/'+api.repository+'/actions/';recent=api.get(prefix+'runs?per_page=10',deadline);inventory=[]
        for row in recent['workflow_runs']:
            jobs=api.get(prefix+'runs/'+str(row['id'])+'/jobs?per_page=100',deadline);a.require(jobs['total_count']<100,'PROBE_JOB_INVENTORY_TRUNCATED');inventory.extend(jobs['jobs'])
        names=sorted({r['runner_name'] for r in inventory if r.get('runner_name') and 'tp01' in r.get('labels',[])})
        a.save(out/'runner-inventory.json',dict(scope='repository latest10 run job observations; not exhaustive inventory/exclusion',recent=recent,jobs=inventory))
        a.require(len(set(names))==2 and record['runner'] in names,'PROBE_ACTUAL_RUNNER_NOT_INVENTORIED');record['runner_names']=names
        a.ports_free();record['ports_observed_free_not_reserved']=a.PORTS
        if r2:record['qp_access']=json.loads(a.command(['rdma','-j','resource','show','qp'],out,'probe-qp-json',deadline,limit=1048576));record['qp_owner_schema']='RUN_CONNECTED_SNAPSHOT_REQUIRED'
        record['runtime_access']=root_access(a,bind,out,deadline)
        help_raw=run('master-flags',[str(out/'build/master.elf'),'master','-h'])
        help_raw+=(out/'probe-master-flags.stderr.raw').read_text()
        a.require(all(re.search(r'-'+re.escape(name)+r'(?:\s|=)',help_raw) for name in ('ip','port','port.grpc','mdir')),'PROBE_MASTER_FLAGS_UNSUPPORTED')
        record['plans']=plans(a,out/'build',bind,facts)
        a.require(time.monotonic()<deadline,'PROBE_PRODUCER_DEADLINE');record['state']='PASS_FACTS_PLANS_NOT_RUN'
    except BaseException as error:record.update(error=repr(error),error_code=str(error) if isinstance(error,ValueError) else 'PROBE_'+type(error).__name__);raise
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);record['producer_end']=time.monotonic();record['producer_seconds']=record['producer_end']-start;a.save(out/'probe.json',record)
    return 'PROBE_FACTS_CAPTURED_PLANS_NOT_EXECUTED_PUBLICATION_PENDING'

def run_admit(a,bind,out,env,api,deadline):
    run,_=api.artifact(bind['probe_reference'],out/'probe',deadline);a.manifest(out/'probe',output_receipt=True)
    p=json.loads((out/'probe/probe.json').read_text())
    a.require(a.sha(out/'probe/probe.json')==bind['probe_json_sha256'] and p['state']=='PASS_FACTS_PLANS_NOT_RUN','PROBE_RECEIPT_DRIFT')
    r2=bind.get('profile')=='r2-recovery-v1';a.require(p['ci_sha']==run['head_sha']==bind['ci_sha'] and p['source_sha']==(env['TM_SOURCE_SHA'] if r2 else MONO_SHA) and p['build_ci_sha']==(bind['build_ci_sha'] if r2 else BUILD_SHA) and p['run_id']==run['id'] and p['attempt']==run['run_attempt'] and p['job']=='tm-connected-diagnostic','PROBE_JOB_ASSOCIATION')
    if r2:a.require(json.loads((out/'probe/prior-artifacts.json').read_text()).get('build_reference')==bind['build_reference'],'PROBE_BUILD_REFERENCE_DRIFT')
    a.require(p['runner']==env['RUNNER_NAME'] and p['hostname']==socket.gethostname() and p['uid']==os.getuid(),'PROBE_FOREIGN_RUNNER')
    jobs=api.get('/repos/'+api.repository+'/actions/runs/'+str(run['id'])+'/attempts/'+str(run['run_attempt'])+'/jobs?per_page=100',deadline)
    a.require(jobs['total_count']<100 and len([j for j in jobs['jobs'] if j['name']==p['job'] and j['runner_name']==p['runner'] and j['conclusion']=='success'])==1,'PROBE_RUNNER_ASSOCIATION')
    for key in ('kernel_release','siw_module_required_lines','gid','gid_index','netdev','ip','provider_objects','runner_names')+(('gid_binding',) if r2 else ()):a.require(bind[key]==p[key],'PROBE_BIND_FACT_DRIFT_'+key)
    a.require(pathlib.Path(bind['root']).resolve().parent==pathlib.Path(p['runtime_access']['base']) and pathlib.Path(bind['lock_path']).resolve()==pathlib.Path(p['runtime_access']['lock']),'PROBE_RUN_ROOT_DRIFT')
    lock=pathlib.Path(bind['lock_path']);s=lock.stat();a.require(not lock.is_symlink() and dict(dev=s.st_dev,inode=s.st_ino,uid=s.st_uid)==p['runtime_access']['lock_identity'],'PROBE_LOCK_IDENTITY_DRIFT')
    a.require(os.access(lock,os.W_OK) and os.access(pathlib.Path(bind['root']).parent if r2 else lock.parent,os.W_OK),'PROBE_RUN_PERMISSION_DRIFT')
    a.require(bind['probe_plan_source_sha256']==a.sha(out/'probe/probe.json') and bind['plans_status']=='REVIEWED_RENDERED_OWNED_WRAPPERS','PROBE_PLAN_RENDERER_UNREVIEWED')
    if r2:a.require(all(bind[key]==p['plans'][key] for key in ('setup','down','probes','r2_services','test_env')),'R2_PLAN_DRIFT')
    # Existing guardian still checks fresh_host/job/port/flock before any service.

def r2_metrics(a,metrics):
    counts=[]
    for key in ('active','accepted_total','released_total','rejected_total'):
        matches=re.findall(r'^SeaweedFS_rdma_connections_'+key+r' ([0-9]+)$',metrics,re.M);a.require(len(matches)==1,'R2_METRIC_MISSING_OR_DUPLICATE');counts.append(int(matches[0]))
    return counts

def snapshot(a,root,client,label):
    a.require(re.fullmatch(r'initial|(?:[0-9]|1[0-7])-(?:connected|settled)',label),'R2_SNAPSHOT_LABEL');client=int(client)
    bind=json.loads((root/'r2-bind.json').read_text());expected=json.loads((root/'client.identity.json').read_text());server=int((root/'server/owner.pid').read_text())
    a.require(bind['profile']=='r2-recovery-v1' and expected['pid']==client,'R2_CLIENT_BIND');deadline=min(json.loads((root/'clock.json').read_text())['body_deadline'],time.monotonic()+2)
    actors=[a.r2_identity(pid) for pid in (client,server)];a.require(actors[0]['starttime']==expected['starttime'] and actors[1]['starttime']==int((root/'server/owner.starttime').read_text()),'R2_ACTOR_DRIFT')
    a.require(len({v['namespace'] for v in actors} | {pathlib.Path('/proc/self/ns/net').stat().st_ino})==1,'R2_NAMESPACE_DRIFT');tag='r2-'+str(time.monotonic_ns())
    qps=json.loads(a.command(['rdma','-j','resource','show','qp'],root,tag+'-qp',deadline,limit=1048576));a.require(isinstance(qps,list),'R2_QP_TABLE_SCHEMA');owned=[[],[]];seen=set()
    for qp in qps:
        a.require('ifname' in qp,'R2_QP_DEVICE_MISSING')
        if qp['ifname']!='siw0':continue
        a.require(type(qp.get('pid')) is int and qp['pid']>0 and type(qp.get('lqpn')) is int and qp['lqpn']>0 and qp['lqpn'] not in seen,'R2_QP_OWNER_OR_ID_MISSING');seen.add(qp['lqpn'])
        status=pathlib.Path('/proc')/str(qp['pid'])/'status';tgid=int(re.search(r'^Tgid:\s+(\d+)',status.read_text(),re.M)[1])
        for i,pid in enumerate((client,server)):
            if tgid==pid:a.require(qp.get('type')=='RC','R2_WRONG_QP_TYPE');owned[i].append(qp)
    groups={}
    for i,pid in enumerate((client,server)):
        if i==1 or owned[i]:groups[str(pid)]=a.r2_provider(pid,bind['provider_objects'])
    tcp=pathlib.Path('/proc/net/tcp').read_text().splitlines()[1:];listeners=[line.split()[9] for line in tcp if line.split()[1]=='0100007F:B4A4' and line.split()[3]=='0A']
    a.require(len(listeners)==1 and 'socket:['+listeners[0]+']' in groups[str(server)]['fd_targets'],'R2_METRICS_OWNER_UNPROVEN')
    connection=http.client.HTTPConnection('127.0.0.1',46244,timeout=max(.01,deadline-time.monotonic()));connection.sock=socket.create_connection(('127.0.0.1',46244),timeout=max(.01,deadline-time.monotonic()))
    try:
        a.require(connection.sock.getpeername()==('127.0.0.1',46244),'R2_METRICS_WRONG_PEER');connection.request('GET','/metrics');response=connection.getresponse();a.require(response.status==200,'R2_METRICS_HTTP')
        metrics=a.bounded_read(response,deadline,1048576,root/(tag+'-metrics.raw')).decode();counts=[len(v) for v in owned]
        counts.extend(r2_metrics(a,metrics))
    finally:connection.close()
    counts.append(owned[0][0]['lqpn'] if len(owned[0])==1 else 0);a.require([a.r2_identity(v['pid']) for v in actors]==actors and time.monotonic()<deadline,'R2_OBSERVATION_DRIFT_OR_LATE')
    record=dict(label=label,actors=actors,counts=counts,qp_table=qps,providers=groups,metrics_file=tag+'-metrics.raw',actual_counter_scope='OWNED_TGID_NETNS_SIW_QP_AND_VOLUME_PERMITS')
    with (root/'r2-observations.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
    print('R2_COUNTS',' '.join(map(str,counts)))
