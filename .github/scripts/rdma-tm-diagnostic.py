"""Default-off TM build/run adapter. No RUN without independent manager authority.

Owned guardian protocol: flock + START, PID/starttime enrollment, original deadline,
identity-bound down, terminal census, END only on proof. Missing proof retains lock.
"""
import argparse
import ctypes
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('tm_decode', HERE/'rdma-tm-decode.py')
decoder = importlib.util.module_from_spec(spec); spec.loader.exec_module(decoder)
require = decoder.require
PORTS = [46240, 46241, 46242, 46243, 46244, 46245, 46246, 56243]
HEX40 = re.compile('[0-9a-f]{40}')
HEX64 = re.compile('[0-9a-f]{64}')

def sha(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''): h.update(block)
    return h.hexdigest()

def save(path, value):
    path = pathlib.Path(path); temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n'); os.replace(temporary, path)

def routing(event, inputs, environment):
    profile = inputs.get('diagnostic_profile') or 'none'
    if event != 'workflow_dispatch' or profile == 'none': return 'DEFAULT'
    require(profile == 'tm-connected-v1' and inputs.get('runner') == 'tp01' and environment == 'self-hosted', 'PROFILE_OR_RUNNER')
    require(inputs.get('diagnostic_phase') in ('build', 'run') and HEX40.fullmatch(inputs.get('mono_sha', '')), 'PHASE_OR_SHA')
    return 'DIAGNOSTIC'

def command(argv, output, label, deadline, env=None, cwd=None):
    remaining = deadline-time.monotonic(); require(remaining > 0, 'ABSOLUTE_DEADLINE')
    child=None; identity=None; primary=None; cleanup=None;rc=None
    try:
        with (output/(label+'.stdout.raw')).open('wb') as out, (output/(label+'.stderr.raw')).open('wb') as err:
            child = subprocess.Popen(argv, stdout=out, stderr=err, env=env, cwd=cwd, start_new_session=True)
            identity=proc(child.pid)
            rc=child.wait(timeout=max(.001,deadline-time.monotonic()))
    except BaseException as error:
        primary=error
        if child is not None:
            # Unreaped direct Popen child owns this PID; never address a foreign reaped PID.
            cleanup=dict(pid=child.pid,identity=identity,tail_seconds=2,checked=False)
            try:
                if child.poll() is None:
                    actual=proc(child.pid)
                    require(actual and (identity is None or actual['starttime']==identity['starttime']),'COMMAND_CHILD_IDENTITY_DRIFT')
                    identity=actual;cleanup['identity']=actual
                    try:os.killpg(child.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:os.killpg(child.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        child.wait(timeout=1)
                require(child.returncode is not None,'COMMAND_CHILD_NOT_REAPED')
                cleanup.update(checked=True,reaped=True,actual_exit=child.returncode)
            except BaseException as stop_error:cleanup['error']=repr(stop_error)
        rc=124 if isinstance(error,subprocess.TimeoutExpired) else child.returncode if child is not None else None
    finally:
        save(output/(label+'.exit.json'),dict(argv=argv,exit=rc,deadline=deadline,end=time.monotonic(),primary_refusal=repr(primary) if primary else None,cleanup=cleanup))
    if primary is not None and not isinstance(primary,subprocess.TimeoutExpired):raise primary
    require(rc == 0 and time.monotonic() < deadline, 'COMMAND_REFUSED '+label)
    return (output/(label+'.stdout.raw')).read_bytes()

def manifest(root, expected=None):
    root = pathlib.Path(root); f = root/'manifest.sha256'
    if expected is not None: require(sha(f) == expected, 'INPUT_MANIFEST_DRIFT')
    seen = set()
    for line in f.read_text().splitlines():
        digest, name = line.split('  ', 1); relative = pathlib.PurePosixPath(name)
        require(HEX64.fullmatch(digest) and not relative.is_absolute() and '..' not in relative.parts and name not in seen and '\\' not in name, 'MANIFEST_PATH')
        target = root/name
        require(name != 'manifest.sha256' and target.is_file() and target.resolve().is_relative_to(root.resolve()) and not any(x.is_symlink() for x in [target, *target.parents] if x.is_relative_to(root)), 'MANIFEST_FILE')
        require(sha(target) == digest, 'INPUT_BYTE_DRIFT'); seen.add(name)
    require(seen == {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p != f}, 'UNLISTED_INPUT')
    require(not any('launch' in n.lower() or 'envelope' in n.lower() for n in seen), 'AUTHORITY_CYCLE')
    return seen

def seal(root):
    files = sorted(p for p in root.rglob('*') if p.is_file() and p != root/'manifest.sha256')
    (root/'manifest.sha256').write_text(''.join(sha(p)+'  '+p.relative_to(root).as_posix()+'\n' for p in files))

def select_elf(raw, target, test):
    items = [v['executable'] for v in map(json.loads, raw.decode().splitlines()) if v.get('reason') == 'compiler-artifact' and v.get('target', {}).get('name') == target and v.get('profile', {}).get('test', False) == test and v.get('executable')]
    require(len(items) == 1, 'ELF_SELECTION'); return pathlib.Path(items[0])

def exact_row(raw):
    require(raw.decode().splitlines().count(decoder.ROW+': test') == 1, 'ROW_SELECTION')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None

def bounded_read(response, deadline, limit, raw_path):
    data=bytearray()
    previous=signal.getsignal(signal.SIGALRM)
    def expired(signum, frame): raise ValueError('API_ABSOLUTE_DEADLINE')
    require(deadline>time.monotonic(),'API_DEADLINE')
    signal.signal(signal.SIGALRM,expired); signal.setitimer(signal.ITIMER_REAL,deadline-time.monotonic())
    try:
        while True:
            part=response.read1(min(1048576,limit+1-len(data))); data.extend(part)
            require(len(data)<=limit and time.monotonic()<deadline,'API_SIZE_OR_DEADLINE')
            if not part:return bytes(data)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);raw_path.write_bytes(data)

class Actions:
    def __init__(self, repository, token, output):
        require(re.fullmatch(r'[\w.-]+/[\w.-]+', repository), 'REPOSITORY')
        self.repository, self.token, self.output = repository, token, output
    def get(self, path, deadline):
        require(path.startswith('/repos/'+self.repository+'/') and '?' not in path.split('/')[3], 'API_SCOPE')
        request = urllib.request.Request('https://api.github.com'+path, headers={'Authorization':'Bearer '+self.token, 'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'})
        remaining = deadline-time.monotonic(); require(remaining > 0, 'API_DEADLINE')
        with urllib.request.urlopen(request, timeout=min(20, remaining)) as response: raw = bounded_read(response,deadline,16*1024*1024,self.output/('api-'+hashlib.sha256(path.encode()).hexdigest()[:16]+'.raw'))
        require(len(raw) <= 16*1024*1024 and time.monotonic() < deadline, 'API_SIZE_OR_DEADLINE')
        save(self.output/('api-'+hashlib.sha256(path.encode()).hexdigest()[:16]+'.json'), json.loads(raw))
        return json.loads(raw)
    def artifact(self, reference, dest, deadline):
        prefix = '/repos/'+self.repository+'/actions/'
        run = self.get(prefix+'runs/'+str(reference['run_id']), deadline)
        require(str(run['repository']['id']) == str(os.environ['GITHUB_REPOSITORY_ID']) and run['id'] == reference['run_id'] and run['run_attempt'] == reference['attempt'] and run['workflow_id'] == reference['workflow_id'] and run['head_sha'] == reference['head_sha'] and str(run['actor']['id']) == str(reference['actor_id']) and run['conclusion'] == 'success', 'ARTIFACT_RUN_ASSOCIATION')
        artifact = self.get(prefix+'artifacts/'+str(reference['artifact_id']), deadline)
        require(not artifact['expired'] and artifact['workflow_run']['id'] == run['id'] and artifact['name'] == reference['name'], 'ARTIFACT_IDENTITY')
        request = urllib.request.Request('https://api.github.com'+prefix+'artifacts/'+str(reference['artifact_id'])+'/zip', headers={'Authorization':'Bearer '+self.token})
        try: urllib.request.build_opener(NoRedirect).open(request, timeout=min(20, deadline-time.monotonic()))
        except urllib.error.HTTPError as error:
            require(error.code == 302 and error.headers['Location'].startswith('https://'), 'ARTIFACT_REDIRECT')
            location = error.headers['Location']
        else: raise ValueError('ARTIFACT_REDIRECT_MISSING')
        # Do not forward the Actions token to the signed storage URL.
        with urllib.request.urlopen(location, timeout=min(20, deadline-time.monotonic())) as response: raw = bounded_read(response,deadline,256*1024*1024,self.output/('artifact-'+str(reference['artifact_id'])+'.zip.raw'))
        require(len(raw) <= 256*1024*1024 and time.monotonic() < deadline, 'ARTIFACT_SIZE_OR_DEADLINE')
        require(artifact.get('digest') == 'sha256:'+hashlib.sha256(raw).hexdigest(), 'ARTIFACT_ZIP_DIGEST')
        dest.mkdir(); seen = set()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for item in archive.infolist():
                name = pathlib.PurePosixPath(item.filename)
                require(not name.is_absolute() and '..' not in name.parts and '\\' not in item.filename and item.filename not in seen and (item.external_attr >> 16)&0o170000 != 0o120000, 'ARTIFACT_PATH')
                seen.add(item.filename); require(sum(i.file_size for i in archive.infolist()) <= 256*1024*1024, 'ARTIFACT_UNPACK_SIZE')
                archive.extract(item, dest)
        return run, artifact

def authorize(bind, envelope, trust):
    require(trust.get('manager_actor_id') and trust.get('publication_workflow_id') and HEX40.fullmatch(trust.get('publication_head_sha', '')), 'AUTHORITY_UNBOUND')
    require(str(trust['dispatch_actor_id']) == str(trust['manager_actor_id']), 'FOREIGN_MANAGER_DISPATCH')
    publication = trust['publication']
    require(str(publication['actor']['id']) == str(trust['manager_actor_id']) and str(publication['workflow_id']) == str(trust['publication_workflow_id']) and publication['head_sha'] == trust['publication_head_sha'], 'FOREIGN_AUTHORITY_PUBLICATION')
    require(not any(k in bind for k in ('launch', 'envelope', 'envelope_id', 'envelope_digest')), 'AUTHORITY_CYCLE')
    require(envelope['workspace'] == 'seaweed' and envelope['channelId'] == '6a9c32d9c96e4d19d8100d51' and envelope['sender'] == '1207574175858819073', 'FOREIGN_HULY_AUTHORITY')
    launches=re.findall(r'(?<!\w)LAUNCH\s+([0-9a-f]{64})(?![0-9a-f])',envelope['text'])
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',bind['run_id']),'LOGICAL_RUN_ID')
    require(envelope['id']==trust['envelope_id'] and launches==[trust['input_manifest']] and re.search(r'(?<![\w.-])'+re.escape(bind['run_id'])+r'(?![\w.-])',envelope['text']),'LAUNCH_CROSS_REFERENCE')

def fetch_policy(source, env):
    require(not any(k.startswith('CARGO_SOURCE_') or k.startswith('CARGO_REGISTRIES_') for k in env),'FETCH_REGISTRY_OVERRIDE')
    paths=set()
    for workspace in ('enterprise/rust','enterprise/seaweed-volume'):
        directory=source/workspace
        require(all(v=='registry+https://github.com/rust-lang/crates.io-index' for v in re.findall(r'^source = "([^"]+)"',(directory/'Cargo.lock').read_text(),re.M)),'FETCH_NON_CRATES_IO_SOURCE')
        for ancestor in (directory,*directory.parents):
            paths.update(ancestor/'.cargo'/name for name in ('config','config.toml'))
    home=pathlib.Path(env.get('CARGO_HOME',str(pathlib.Path.home()/'.cargo')))
    paths.update(home/name for name in ('config','config.toml'))
    for path in paths:
        if path.is_file():require(not re.search(r'\[\s*(?:source|registries)(?:\.|\])|replace-with',path.read_text()),'FETCH_CONFIG_OVERRIDE')
    return dict(env,CARGO_NET_RETRY='0',CARGO_REGISTRIES_CRATES_IO_PROTOCOL='sparse',CARGO_REGISTRIES_CRATES_IO_INDEX='sparse+https://index.crates.io/')

def runner_build_root(env, fresh=True):
    workspace=pathlib.Path(env['GITHUB_WORKSPACE']).resolve(strict=True)
    require(workspace.is_dir() and workspace.stat().st_uid==os.getuid(),'RUNNER_WORKSPACE_OWNER')
    require(all(re.fullmatch(r'[1-9][0-9]*',env[k]) for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')),'BUILD_RUN_IDENTITY')
    target=workspace/('codex03-tm-build-'+env['GITHUB_RUN_ID']+'-'+env['GITHUB_RUN_ATTEMPT'])
    require(not target.is_symlink(),'BUILD_ROOT_ALIAS')
    if fresh:require(not target.exists(),'BUILD_ROOT_NOT_FRESH')
    return target


def runner_preflight(out, env):
    deadline=time.monotonic()+20;previous=signal.getsignal(signal.SIGALRM)
    def expired(signum,frame):raise ValueError('RUNNER_PREFLIGHT_DEADLINE')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,20)
    record=dict(state='FAIL',scope='BUILD_ONLY_RUNNER_PREFLIGHT')
    try:
        require(env.get('TM_BUILD_AUTHORITY')=='BUILD_ONLY '+env['TM_SOURCE_SHA'] and env.get('TM_MANAGER_ACTOR_ID')==env.get('GITHUB_ACTOR_ID') and env.get('TM_MANAGER_ACTOR_ID'),'BUILD_AUTHORITY_UNBOUND')
        target=runner_build_root(env);workspace=target.parent
        record.update(root=str(target),workspace=str(workspace),workspace_alias=env['GITHUB_WORKSPACE'],uid=os.getuid(),runner=env['RUNNER_NAME'],run_id=env['GITHUB_RUN_ID'],attempt=env['GITHUB_RUN_ATTEMPT'])
        require(all(shutil.which(name,path=env.get('PATH')) for name in ('whoami','rustc','cargo','go','cc','readelf','pkg-config')),'BUILD_TOOLCHAIN_MISSING')
        record['whoami']=command(['whoami'],out,'runner-whoami',deadline,env=env).decode().strip()
        record['free_bytes']=shutil.disk_usage(workspace).free;record['capacity']='CAPACITY_UNQUALIFIED';record['filesystem_device']=workspace.stat().st_dev
        target.mkdir(mode=0o700);root_stat=target.stat();require(root_stat.st_uid==os.getuid(),'BUILD_ROOT_OWNER');record['root_identity']=dict(dev=root_stat.st_dev,inode=root_stat.st_ino,uid=root_stat.st_uid)
        probe=target/'admission-probe'
        fd=os.open(probe,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            require(os.write(fd,b'write-probe')==11,'BUILD_PROBE_SHORT_WRITE');os.fsync(fd);opened=os.fstat(fd);actual=probe.stat();require(opened.st_uid==os.getuid() and (opened.st_dev,opened.st_ino)==(actual.st_dev,actual.st_ino),'BUILD_PROBE_IDENTITY');record['probe_identity']=dict(dev=actual.st_dev,inode=actual.st_ino,uid=actual.st_uid)
        finally:os.close(fd);probe.unlink()
        record['writable']=True
        tools={name:shutil.which(name,path=env.get('PATH')) for name in ('rustc','cargo','go','cc','readelf','pkg-config')}
        require(all(tools.values()),'BUILD_TOOLCHAIN_MISSING');record['tool_paths']=tools
        for name,argv in [('rustc',['rustc','-Vv']),('cargo',['cargo','-Vv']),('go',['go','version']),('cc',['cc','--version'])]:
            raw=command(argv,out,'runner-'+name,deadline,env=dict(env,GOTOOLCHAIN='local',RUSTUP_AUTO_INSTALL='0'))
            if name=='go':
                found=re.search(r'go(\d+)\.(\d+)\.(\d+)',raw.decode());require(found and tuple(map(int,found.groups()))>=(1,26,6),'GO_TOOLCHAIN_UNBOUND')
        require(time.monotonic()<deadline,'RUNNER_PREFLIGHT_DEADLINE');record['state']='PASS'
    except BaseException as error:
        record['error']=repr(error);record['error_code']=str(error) if isinstance(error,ValueError) else 'RUNNER_PREFLIGHT_'+type(error).__name__;raise
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);save(out/'runner-preflight.json',record)


def admitted_build_root(out,env):
    target=runner_build_root(env,fresh=False);preflight=json.loads((out/'runner-preflight.json').read_text());require(preflight['state']=='PASS' and preflight['root']==str(target) and preflight['run_id']==env['GITHUB_RUN_ID'] and preflight['attempt']==env['GITHUB_RUN_ATTEMPT'],'RUNNER_PREFLIGHT_UNBOUND');actual=target.stat();require(dict(dev=actual.st_dev,inode=actual.st_ino,uid=actual.st_uid)==preflight['root_identity'] and actual.st_uid==os.getuid(),'BUILD_ROOT_IDENTITY_DRIFT')
    return target


def build(source, out, env):
    require(command(['git','rev-parse','HEAD'],out,'mono-head',time.monotonic()+10,cwd=source).decode().strip() == env['TM_SOURCE_SHA'], 'SOURCE_HEAD_DRIFT')
    require(not command(['git','status','--porcelain','--untracked-files=normal'],out,'mono-clean-before',time.monotonic()+10,cwd=source).strip(),'SOURCE_TREE_DIRTY')
    require((source/'enterprise/rust/sw-rdma-loader/src/tm_adapter_tests.rs').is_file(), 'SOURCE_ROW_MISSING')
    snapshots = {str(p.relative_to(source)):sha(p) for name in ('enterprise/rust','enterprise/seaweed-volume') for p in [source/name/'Cargo.toml',source/name/'Cargo.lock']}
    snapshots.update({name:sha(source/name) for name in ('enterprise/go.mod','enterprise/go.sum')})
    target=admitted_build_root(out,env)
    safe = dict(env, CARGO_TARGET_DIR=str(target/'target'), GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off', GOFLAGS='-mod=readonly', RUSTUP_AUTO_INSTALL='0')
    for label, argv in [('rustc',['rustc','-Vv']),('cargo',['cargo','-Vv']),('go',['go','version']),('cc',['cc','--version'])]: command(argv,out,'toolchain-'+label,time.monotonic()+10,env=safe)
    save(out/'toolchain-files.json',{name:dict(path=str(pathlib.Path(shutil.which(name)).resolve()),sha256=sha(pathlib.Path(shutil.which(name)).resolve())) for name in ('rustc','cargo','go','cc')})
    command(['pkg-config','--modversion','libibverbs','librdmacm'],out,'rdma-core-version',time.monotonic()+10,env=safe)
    command(['pkg-config','--cflags','--libs','libibverbs','librdmacm'],out,'rdma-core-flags',time.monotonic()+10,env=safe)
    save(out/'prebuild-environment.json',dict(python=sys.version,python_path=str(pathlib.Path(sys.executable).resolve()),python_sha256=sha(sys.executable),cargo_home=env.get('CARGO_HOME',str(pathlib.Path.home()/'.cargo')),target_dir=safe['CARGO_TARGET_DIR'],target='x86_64-unknown-linux-gnu',flags={k:env.get(k,'') for k in ('RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS')}))
    version = (out/'toolchain-go.stdout.raw').read_text(); found=re.search(r'go(\d+)\.(\d+)\.(\d+)',version); require(found and tuple(map(int,found.groups())) >= (1,26,6), 'GO_TOOLCHAIN_UNBOUND')
    if env.get('TM_FETCH_AUTHORITY'):
        require(env['TM_FETCH_AUTHORITY'] == 'FETCH_LOCKED '+env['TM_SOURCE_SHA'] and env.get('TM_BUILD_AUTHORITY'), 'FETCH_AUTHORITY')
        fetch_env=fetch_policy(source,safe)
        for name in ('enterprise/rust','enterprise/seaweed-volume'):
            command(['cargo','fetch','--locked','--target','x86_64-unknown-linux-gnu','--manifest-path',str(source/name/'Cargo.toml')],out,'fetch-'+name.split('/')[-1],time.monotonic()+300,env=fetch_env,cwd=source)
    products = {}
    for label, directory, argv, target, test in [('loader','enterprise/rust',['test','-p','seaweedfs-sw-rdma-loader','--features','real-rdma','--lib','--no-run'],'sw_rdma_loader',True),('server','enterprise/seaweed-volume',['build','--features','rdma','--bin','weed-volume'],'weed-volume',False)]:
        args = ['cargo',*argv,'--release','--offline','--locked','--message-format=json','--manifest-path',str(source/directory/'Cargo.toml')]
        raw = command(args,out,'build-'+label,time.monotonic()+1200,env=safe,cwd=source)
        binary = select_elf(raw,target,test); dest = out/(label+'.elf'); shutil.copyfile(binary,dest); dest.chmod(0o700)
        products[label] = dict(file=dest.name,sha256=sha(dest),argv=args,source_sha=env['TM_SOURCE_SHA'])
    master = out/'master.elf'
    args = ['go','build','-trimpath','-tags','5BytesOffset','-o',str(master),'./weed']
    command(args,out,'build-master',time.monotonic()+1200,env=safe,cwd=source/'enterprise')
    products['master'] = dict(file=master.name,sha256=sha(master),argv=args,source_sha=env['TM_SOURCE_SHA'])
    command(['go','version','-m',str(master)],out,'master-provenance',time.monotonic()+15,env=safe)
    for label in products:
        binary = out/products[label]['file']
        for suffix, args in [('ELF',['readelf','-h',str(binary)]),('dependencies',['ldd',str(binary)])]: command(args,out,label+'-'+suffix,time.monotonic()+15)
    listing = command([str(out/'loader.elf'),'--list','--ignored'],out,'loader-list',time.monotonic()+15); exact_row(listing)
    command([str(out/'server.elf'),'--help'],out,'server-help',time.monotonic()+15)
    command([str(master),'version'],out,'master-version',time.monotonic()+15)
    command([str(master),'help'],out,'master-help',time.monotonic()+15)
    require(all(sha(source/k) == v for k,v in snapshots.items()), 'BUILD_MANIFEST_DRIFT')
    require(not command(['git','status','--porcelain','--untracked-files=normal'],out,'mono-clean-after',time.monotonic()+10,cwd=source).strip(),'SOURCE_TREE_DIRTY')
    save(out/'build.json',dict(source_sha=env['TM_SOURCE_SHA'],ci_sha=env['GITHUB_SHA'],run_id=int(env['GITHUB_RUN_ID']),attempt=int(env['GITHUB_RUN_ATTEMPT']),job=env['GITHUB_JOB'],runner=env['RUNNER_NAME'],products=products,source_manifests=snapshots,list_sha256=sha(out/'loader-list.stdout.raw'),flags={k:env.get(k,'') for k in ('RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS')}))
    # Targets are build intermediates, not runtime INPUT or retained binary substitutes.
    return 'BUILD_ONLY'

def proc(pid):
    try:
        text = pathlib.Path(f'/proc/{pid}/stat').read_text(); fields = text[text.rindex(')')+2:].split()
        return dict(pid=pid,state=fields[0],starttime=int(fields[19]),sid=int(fields[3]))
    except FileNotFoundError: return None

def ports_free():
    occupied = set()
    for path in ('/proc/net/tcp','/proc/net/tcp6'):
        occupied.update(int(line.split()[1].split(':')[1],16) for line in pathlib.Path(path).read_text().splitlines()[1:])
    require(not occupied.intersection(PORTS), 'PRE_SPAWN_PORT_COLLISION')

def enrollment(root):
    owners = []
    for path in root.glob('*/*.pid'):
        pid = int(path.read_text()); start = int(path.with_suffix('.starttime').read_text()); actual = proc(pid)
        require(actual is None or actual['starttime'] == start, 'OWNER_IDENTITY_CHANGED')
        owners.append(dict(pid=pid,starttime=start,record=str(path)))
    return owners

def census(root, owners, deadline=None):
    alive = [p for v in owners if (p := proc(v['pid'])) and p['starttime'] == v['starttime'] and p['state'] != 'Z']
    for entry in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        require(deadline is None or time.monotonic()<deadline,'CENSUS_DEADLINE')
        try:
            if str(root).encode() in entry.read_bytes() and int(entry.parent.name) != os.getpid():
                p = proc(int(entry.parent.name))
                if p and p['state'] != 'Z': alive.append(p)
        except FileNotFoundError: pass
    return alive

def render(row, replacements, root):
    require(isinstance(row['argv'],list) and all(isinstance(v,str) for v in row['argv']), 'ARGV_UNBOUND')
    def replace(value):
        for key,text in replacements.items(): value = value.replace('${'+key+'}',text)
        require('${' not in value, 'OPERAND_UNBOUND'); return value
    argv = [replace(v) for v in row['argv']]; env = {k:replace(v) for k,v in row.get('env',{}).items()}
    if 'RUN' in env: require(pathlib.Path(env['RUN']).is_relative_to(root), 'SERVICE_ROOT_ESCAPE')
    return argv, env

def service_env(extra):
    require(not any('TOKEN' in k or 'SECRET' in k for k in extra),'SERVICE_CREDENTIAL_OVERRIDE')
    return {**{k:v for k,v in os.environ.items() if 'TOKEN' not in k and 'SECRET' not in k},**extra}

def guardian(bundle, output, replacements):
    root = pathlib.Path(bundle['root']); root.mkdir(mode=0o700)
    lock = open(bundle['lock_path'],'a'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    ctypes.CDLL(None).prctl(36,1,0,0,0)
    origin = time.monotonic(); end = origin+bundle.get('whole_seconds',600); body = end-bundle.get('reserve_seconds',60); setup_deadline=min(body,origin+90)
    save(root/'clock.json',dict(origin=origin,body_deadline=body,terminal_deadline=end))
    save(root/'guard.identity.json',proc(os.getpid())); (root/'ledger.jsonl').write_text(json.dumps(dict(event='START',run_id=bundle['run_id'],guardian=proc(os.getpid()),origin=origin))+'\n'); (root/'state').write_text('SETUP')
    owners=[]; errors=[]; primary=None; status='FAIL'; client=None
    def execute(row,label,cap):
        argv,extra = render(row,replacements,root)
        return command(argv,root,label,min(setup_deadline,time.monotonic()+cap),env=service_env(extra))
    try:
        if not bundle.get('local_inert_fixture',False):
            api=Actions(os.environ['GITHUB_REPOSITORY'],os.environ['GH_TOKEN'],root)
            wait_ci(api,bundle,root,min(setup_deadline,time.monotonic()+60))
            fresh_host(bundle,root,setup_deadline)
            for label in ('loader','server','master'):
                binary=pathlib.Path(replacements['BUILD'])/(label+'.elf')
                header=command(['readelf','-h',str(binary)],root,'fresh-'+label+'-ELF',min(setup_deadline,time.monotonic()+10))
                require(b'ELF64' in header and b'X86-64' in header,'ELF_MACHINE')
                deps=command(['ldd',str(binary)],root,'fresh-'+label+'-dependencies',min(setup_deadline,time.monotonic()+10))
                require(b'not found' not in deps,'DEPENDENCY_MISSING')
                command([str(binary),*({'loader':['--list','--ignored'],'server':['--help'],'master':['help']}[label])],root,'fresh-'+label+'-entry',min(setup_deadline,time.monotonic()+10))
        ports_free()
        for i,row in enumerate(bundle['setup']):
            ports_free(); execute(row,'setup-'+str(i),min(90,body-time.monotonic())); owners=enrollment(root); save(root/'enrollment.json',owners)
        for i,row in enumerate(bundle['probes']): execute(row,'probe-'+str(i),min(15,body-time.monotonic()))
        (root/'state').write_text('READY')
        while time.monotonic()<body:
            if (root/'finish.json').exists(): status=json.loads((root/'finish.json').read_text())['status']; break
            caller=proc(bundle['caller']['pid'])
            if not caller or caller['starttime'] != bundle['caller']['starttime']: raise ValueError('CALLER_LOST')
            if (root/'client.identity.json').exists(): client=json.loads((root/'client.identity.json').read_text())
            time.sleep(.05)
        else: raise ValueError('BODY_DEADLINE')
    except BaseException as error: primary=repr(error)
    finally:
        (root/'state').write_text('STOPPING')
        try:
            if (root/'client.identity.json').exists():
                client=json.loads((root/'client.identity.json').read_text()); owners.append(client); actual=proc(client['pid'])
                require(actual is None or actual['starttime']==client['starttime'],'CLIENT_IDENTITY_CHANGED')
                if actual and actual['starttime']==client['starttime'] and actual['state']!='Z':
                    os.killpg(client['pid'],signal.SIGTERM); limit=min(end,time.monotonic()+2)
                    while time.monotonic()<limit and (actual:=proc(client['pid'])) and actual['state']!='Z': time.sleep(.05)
                    if actual and actual['state']!='Z': os.killpg(client['pid'],signal.SIGKILL)
            for i,row in enumerate(bundle['down']):
                try:
                    argv,extra=render(row,replacements,root); command(argv,root,'down-'+str(i),min(end,time.monotonic()+14),env=service_env(extra))
                except BaseException as error: errors.append(repr(error))
            # Enrollment and a root argv scan are both required; parse/read errors retain lock.
            for owner in enrollment(root):
                if owner not in owners: owners.append(owner)
            for owner in owners:
                actual=proc(owner['pid'])
                if actual and actual['state']=='Z' and actual['starttime']==owner['starttime']:
                    try: os.waitpid(owner['pid'],os.WNOHANG)
                    except ChildProcessError: pass
            live=census(root,owners,end)
            require(not live and not errors, 'TERMINAL_UNCERTAIN')
            require(time.monotonic()<end,'TERMINAL_DEADLINE')
            save(root/'terminal.json',dict(status=status,primary_setup_error=primary,owners=owners,errors=errors,live=live,wall=time.monotonic()-origin))
            (root/'state').write_text(status)
            ledger=(root/'ledger.jsonl').read_text(); temp=root/'ledger.tmp'; temp.write_text(ledger+json.dumps(dict(event='END',run_id=bundle['run_id'],status=status,live=live,errors=errors))+'\n'); os.replace(temp,root/'ledger.jsonl')
            lock.close()
        except BaseException as error:
            try: save(root/'uncertainty.json',dict(primary=primary,errors=errors,proof_error=repr(error))); (root/'state').write_text('FAILED_LOCK_RETAINED')
            except BaseException: pass
            # No terminal/END assertion; admitted lock remains held by this owned guardian.
            while True: time.sleep(1)

def fresh_host(bind, output, deadline):
    require(bind['kernel_release'] and bind['siw_module_required_lines'] and bind['gid'] and bind['netdev'] and bind['ip'],'HOST_IDENTITY_UNBOUND')
    for label,args in [('kernel',['uname','-r']),('siw-module',['modinfo','siw']),('rdma-link',['rdma','link','show']),('devices',['ibv_devinfo','-v']),('memlock',['bash','-c','ulimit -l'])]:
        raw=command(args,output,'fresh-'+label,min(deadline,time.monotonic()+15)).decode()
        if label=='kernel': require(raw.strip()==bind['kernel_release'],'KERNEL_IDENTITY')
        if label=='siw-module': require(all(line in raw for line in bind['siw_module_required_lines']),'SIW_MODULE_IDENTITY')
        if label=='devices': require('siw0' in raw and bind['gid'] in raw,'SIW_DEVICE_OR_GID')
        if label=='memlock': require(raw.strip()=='unlimited','MEMLOCK')
        if label=='rdma-link': require(re.search(r'siw0/1 .*netdev '+re.escape(bind['netdev'])+r'(?:\s|$)',raw),'IP_RDMA_NETDEV')
    addresses=json.loads(command(['ip','-j','addr','show','dev',bind['netdev']],output,'fresh-IP',min(deadline,time.monotonic()+15)))
    require(any(v.get('local')==bind['ip'] for row in addresses for v in row.get('addr_info',[])),'CM_IP_IDENTITY')
    for library,identity in bind['provider_objects'].items():
        path=pathlib.Path(identity['path']); st=path.stat()
        require(library in ('libsiw','libibverbs','librdmacm') and sha(path)==identity['sha256'] and st.st_ino==identity['inode'] and f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}'==identity['dev'],'FRESH_PROVIDER_IDENTITY')
    require(set(bind['provider_objects'])=={'libsiw','libibverbs','librdmacm'},'PROVIDER_SET')

def wait_ci(api, bind, output, deadline):
    while True:
        prefix='/repos/'+api.repository+'/actions/'
        names=set(bind['runner_names']); require(len(names)==2 and os.environ['RUNNER_NAME'] in names, 'RUNNER_INVENTORY')
        runs=api.get(prefix+'runs?status=in_progress&per_page=100',deadline); require(runs['total_count']<100,'JOB_CENSUS_TRUNCATED')
        active=[]
        for run in runs['workflow_runs']:
            jobs=api.get(prefix+'runs/'+str(run['id'])+'/jobs?per_page=100',deadline); require(jobs['total_count']<100,'JOB_CENSUS_TRUNCATED')
            active.extend(v for v in jobs['jobs'] if v['status']=='in_progress' and v.get('runner_name') in names and str(run['id'])!=os.environ['GITHUB_RUN_ID'])
        save(output/'runner-job-states.json',dict(scope=api.repository,runner_names=sorted(names),current_runner=os.environ['RUNNER_NAME'],runs=runs,foreign_active_jobs=active))
        if not active: return
        require(deadline-time.monotonic()>1,'CI_ACTIVE_SETUP_BLOCKED'); time.sleep(min(1,deadline-time.monotonic()))

def owned_root(root, lock_path, work='/opt/work'):
    root=pathlib.Path(root); base=pathlib.Path(work).resolve()
    require(not root.exists() and not root.is_symlink(),'OWNED_ROOT_NOT_FRESH')
    canonical=root.resolve()
    require(canonical.parent==base and canonical.name.startswith('codex03-tm-') and len(canonical.name)>len('codex03-tm-') and pathlib.Path(lock_path).resolve()==base/'siw-lab.lock','OWNED_ROOT_OR_LOCK')
    return canonical

def test_environment(bind, root, deadline):
    ms=int((deadline-time.monotonic())*1000)
    require(0 < ms <= 540000,'TEST_BODY_BUDGET')
    return service_env({**bind['test_env'],'TM_CHILD_EVIDENCE_FILE':str(root/'tm-child.raw'),'TM_BODY_REMAINING_MS':str(ms)})

def runtime(bind, input_root, build_root, output, api, private=False):
    root=pathlib.Path(bind['root']); require(not root.exists(),'OWNED_ROOT_NOT_FRESH')
    if not private:
        root=owned_root(root,bind['lock_path'])
        require(bind['whole_seconds']==600 and bind['reserve_seconds']==60 and bind['ports']==PORTS,'CLOCK_OR_PORTS')
    replacements={'ROOT':str(root),'INPUT':str(input_root),'BUILD':str(build_root)}
    b=dict(bind,root=str(root),caller=proc(os.getpid()));
    if private: b['local_inert_fixture']=True
    else: require('local_inert_fixture' not in b,'PRIVATE_FIXTURE_FORBIDDEN')
    save(output/'guardian-input.json',dict(bind=b,replacements=replacements))
    child=subprocess.Popen([sys.executable,str(HERE/'rdma-tm-diagnostic.py'),'guardian',str(output/'guardian-input.json'),str(output)],start_new_session=True,env={k:v for k,v in os.environ.items() if k!='RUNNER_TRACKING_ID'})
    parent_deadline=time.monotonic()+b['whole_seconds']+2; status='FAIL'; error=None
    try:
        while time.monotonic()<parent_deadline:
            state=(root/'state').read_text() if (root/'state').exists() else 'ADMISSION'
            if state=='READY': break
            require(state in ('ADMISSION','SETUP') and child.poll() is None,'SETUP_REFUSED'); time.sleep(.05)
        else: raise ValueError('GUARD_READY_DEADLINE')
        clock=json.loads((root/'clock.json').read_text()); deadline=min(clock['body_deadline'],time.monotonic()+360)
        env=test_environment(bind,root,deadline)
        argv=[str(build_root/'loader.elf'),'--ignored','--exact',decoder.ROW,'--nocapture','--test-threads=1']
        with (root/'test.stdout.raw').open('wb') as out,(root/'test.stderr.raw').open('wb') as err:
            test=subprocess.Popen(argv,env=env,stdout=out,stderr=err,start_new_session=True)
            save(root/'client.identity.json',proc(test.pid))
            try: rc=test.wait(timeout=max(.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired: raise ValueError('TEST_DEADLINE')
        require(rc==0 and time.monotonic()<deadline,'TEST_FAILED_OR_LATE')
        evidence_deadline=min(clock['body_deadline'],time.monotonic()+60)
        for identity in bind['provider_objects'].values():
            path=pathlib.Path(identity['path']); stat=path.stat(); require(sha(path)==identity['sha256'] and stat.st_ino==identity['inode'] and f'{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}'==identity['dev'],'INSTALLED_PROVIDER_DRIFT')
        save(root/'decoded.json',decoder.decode((root/'tm-child.raw').read_bytes(),(root/'test.stdout.raw').read_text(),bind['provider_objects'],json.loads((root/'client.identity.json').read_text())))
        require(time.monotonic()<evidence_deadline,'EVIDENCE_DEADLINE')
        status='PASS'
    except BaseException as failure: error=repr(failure)
    finally:
        if root.exists(): save(root/'finish.json',dict(status=status,primary_error=error))
        while time.monotonic()<parent_deadline:
            if child.poll() is not None or (root/'state').exists() and (root/'state').read_text()=='FAILED_LOCK_RETAINED': break
            time.sleep(.05)
        terminal=json.loads((root/'terminal.json').read_text()) if (root/'terminal.json').is_file() else None
        clean_end=child.poll()==0 and (root/'state').is_file() and (root/'state').read_text()==status and (root/'ledger.jsonl').is_file() and json.loads((root/'ledger.jsonl').read_text().splitlines()[-1])['event']=='END'
        if root.exists():
            for f in root.rglob('*'):
                if f.is_file() and not f.is_symlink() and (f.suffix in ('.raw','.json','.jsonl','.log','.pid','.starttime') or f.name=='state'):
                    require(f.stat().st_size<=256*1024*1024,'EVIDENCE_SIZE_REFUSAL')
                    dest=output/'runtime-raw'/f.relative_to(root); dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(f,dest)
        save(output/'runtime-result.json',dict(status=status,primary_error=error,terminal=terminal,guardian=proc(child.pid),state='RUNTIME_PASS_PUBLICATION_PENDING' if status=='PASS' and terminal and terminal['status']=='PASS' and clean_end else 'FAILED_OR_UNKNOWN',run_root=str(root)))
    require(status=='PASS' and terminal and terminal['status']=='PASS' and clean_end,'RUNTIME_FAILED_OR_UNKNOWN')
    require(not census(root,terminal['owners']),'POST_END_CENSUS_UNCERTAIN')
    return 'RUNTIME_PASS_PUBLICATION_PENDING'

def run_phase(out, env):
    deadline=time.monotonic()+180; api=Actions(env['GITHUB_REPOSITORY'],env['GH_TOKEN'],out)
    event=json.loads(pathlib.Path(env['GITHUB_EVENT_PATH']).read_text()); request=json.loads(event['inputs']['diagnostic_objects'])
    trust=dict(manager_actor_id=env.get('TM_MANAGER_ACTOR_ID'),publication_workflow_id=env.get('TM_PUBLICATION_WORKFLOW_ID'),publication_head_sha=env.get('TM_PUBLICATION_HEAD_SHA'),dispatch_actor_id=event['sender']['id'],envelope_id=request['envelope_id'],input_manifest=request['input_manifest'])
    require(trust['manager_actor_id'] and trust['publication_workflow_id'] and trust['publication_head_sha'],'AUTHORITY_UNBOUND')
    require(str(trust['dispatch_actor_id'])==str(trust['manager_actor_id']),'FOREIGN_MANAGER_DISPATCH')
    run,artifact=api.artifact(request['input'],out/'input',deadline); manifest(out/'input',request['input_manifest'])
    bind=json.loads((out/'input/bind.json').read_text()); require('local_inert_fixture' not in bind,'PRIVATE_FIXTURE_FORBIDDEN'); require(bind['source_sha']==env['TM_SOURCE_SHA'] and bind['ci_sha']==env['GITHUB_SHA'],'SOURCE_OR_ADAPTER_BIND_DRIFT')
    for name in ('rdma-tm-diagnostic.py','rdma-tm-decode.py'): require(sha(out/'input'/name)==sha(HERE/name),'ADAPTER_DRIFT')
    pub,_=api.artifact(request['authorization'],out/'authorization',deadline); trust['publication']=pub
    file=out/'authorization/envelope.json'; require(sha(file)==request['envelope_sha256'],'ENVELOPE_DRIFT')
    authorize(bind,json.loads(file.read_text()),trust); save(out/'authority-association.json',dict(request=request,trust=trust))
    build_run,_=api.artifact(bind['build_reference'],out/'build',deadline); require(build_run['head_sha']==env['GITHUB_SHA'],'BUILD_CI_SOURCE_DRIFT'); manifest(out/'build')
    build_info=json.loads((out/'build/build.json').read_text()); require(build_info['source_sha']==env['TM_SOURCE_SHA'] and sha(out/'build/build.json')==bind['build_json_sha256'],'BUILD_BIND_DRIFT')
    require(build_info['ci_sha']==build_run['head_sha'] and build_info['run_id']==build_run['id'] and build_info['attempt']==build_run['run_attempt'] and build_info['job']=='tm-connected-diagnostic','BUILD_JOB_ASSOCIATION')
    jobs=api.get('/repos/'+api.repository+'/actions/runs/'+str(build_run['id'])+'/attempts/'+str(build_run['run_attempt'])+'/jobs?per_page=100',deadline)
    require(jobs['total_count']<100 and len([j for j in jobs['jobs'] if j['name']==build_info['job'] and j['runner_name']==build_info['runner'] and j['conclusion']=='success'])==1,'BUILD_RUNNER_ASSOCIATION')
    for label in ('loader','server','master'):
        row=build_info['products'][label]; require(row['source_sha']==env['TM_SOURCE_SHA'] and sha(out/'build'/row['file'])==row['sha256']==bind['product_hashes'][label],'BUILD_ELF_DRIFT')
        (out/'build'/row['file']).chmod(0o700)
    require(sha(out/'build/loader-list.stdout.raw')==build_info['list_sha256']==bind['list_sha256'],'BUILD_LIST_DRIFT')
    exact_row((out/'build/loader-list.stdout.raw').read_bytes())
    require(set(bind['test_env'])=={'TM_RDMA_ADDR','TM_CONTROL_ADDR'},'TEST_ENV_UNBOUND')
    require(len(bind['setup'])==2 and len(bind['down'])==2 and len(bind['probes'])>=2,'TOPOLOGY_UNBOUND')
    return runtime(bind,out/'input',out/'build',out,api)

def upload_proof(output, env, artifact_id, artifact_digest):
    api=Actions(env['GITHUB_REPOSITORY'],env['GH_TOKEN'],output); prefix='/repos/'+api.repository+'/actions/'
    row=api.get(prefix+'artifacts/'+str(int(artifact_id)),time.monotonic()+20)
    require(row['workflow_run']['id']==int(env['GITHUB_RUN_ID']) and not row['expired'] and row['digest']=='sha256:'+artifact_digest,'UPLOAD_FAILED_OR_FOREIGN')
    save(output/'publication-proof.json',dict(state='UPLOAD_OBSERVED_NOT_FINAL_RUN_VERDICT',artifact=row))

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=['preflight','prepare','build','run','guardian','publish']); parser.add_argument('output'); parser.add_argument('extra',nargs='*'); args=parser.parse_args()
    if args.phase=='guardian':
        item=json.loads(pathlib.Path(args.output).read_text()); guardian(item['bind'],pathlib.Path(args.extra[0]),item['replacements']); return
    out=pathlib.Path(args.output).resolve(); env=os.environ.copy()
    if args.phase=='prepare':
        event=json.loads(pathlib.Path(env['GITHUB_EVENT_PATH']).read_text()); require(routing(event['event_name'] if 'event_name' in event else env['GITHUB_EVENT_NAME'],event.get('inputs',{}),env['RUNNER_ENVIRONMENT'])=='DIAGNOSTIC','ROUTING')
        out.mkdir(exist_ok=True); save(out/'dispatch.json',dict(event=event,ci_sha=env['GITHUB_SHA'],runner=env['RUNNER_NAME'])); return
    require(out.is_dir(),'OUTPUT_MISSING')
    if args.phase=='publish': upload_proof(out,env,*args.extra); return
    try:
        if args.phase=='preflight':runner_preflight(out,env);return
        if args.phase=='build':
            require(env.get('TM_BUILD_AUTHORITY')=='BUILD_ONLY '+env['TM_SOURCE_SHA'] and env.get('TM_MANAGER_ACTOR_ID') and env.get('TM_MANAGER_ACTOR_ID')==env.get('GITHUB_ACTOR_ID'),'BUILD_AUTHORITY_UNBOUND')
            state=build(pathlib.Path(env['GITHUB_WORKSPACE'])/'seaweedfs-source',out,env)
        else: state=run_phase(out,env)
        save(out/'state.json',dict(state=state,publication='PENDING',runtime_authority='SEPARATE_MANAGER_DISPATCH'))
    except BaseException as error:
        save(out/'state.json',dict(state='REFUSED_OR_UNKNOWN',error=repr(error),publication='PENDING')); raise
    finally: seal(out)

if __name__=='__main__': main()
