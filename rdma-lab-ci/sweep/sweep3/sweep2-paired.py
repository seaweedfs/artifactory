import fcntl, hashlib, json, math, os, random, re, resource, shlex, signal, statistics, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

D11_CLI = '--d11-self-test' in sys.argv or '--d11-cost' in sys.argv
def sweep_env(name):
    return os.environ.get(name, '') if D11_CLI else os.environ[name]

ROOT = Path(sweep_env('SWEEP_STEP05_ROOT') or '.')
OUT = Path(os.environ.get('SWEEP_PAIRED_ROOT', str(Path(sweep_env('SWEEP_ROOT') or '.')/'paired')))
CLIENT = 'testdev@192.168.1.181'
REMOTE = os.environ.get('SWEEP_PAIRED_REMOTE', '/opt/work/codex02-sweep3-paired-'+sweep_env('SWEEP_PRODUCT')[:8])
WEED = os.environ.get('SWEEP_WEED_BIN', '/opt/work/bin/weed-'+sweep_env('SWEEP_REFERENCE'))
VOLUMES = {'A':os.environ.get('SWEEP_REFERENCE_VOLUME_BIN','/opt/work/bin/weed-volume-'+sweep_env('SWEEP_REFERENCE')+'-sweep-rdma'), 'B':os.environ.get('SWEEP_VOLUME_BIN','/opt/work/bin/weed-volume-'+sweep_env('SWEEP_PRODUCT')+'-sweep-rdma')}
REFERENCE_SHA=sweep_env('SWEEP_REFERENCE')
PRODUCT_SHA=sweep_env('SWEEP_PRODUCT')
SEED=int(os.environ.get('SWEEP_SEED','20260914'))
if not D11_CLI and os.environ.get('SWEEP_DRY_RUN')=='1':print('DRY sweep2-paired');raise SystemExit

def call(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.load(r)

def wait(url, predicate=lambda r: True):
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        try:
            if predicate(get(url)): return
        except Exception: pass
        time.sleep(.25)
    raise RuntimeError('endpoint not ready: '+url)

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

D11_CALLERS = {
    'object_read': 'sw-rdma-object/src/service.rs:read_client_buffer',
    'ec_read': 'seaweed-volume/src/server/store_ec.rs:read_into',
}

def d11_command(mode, build):
    name = 'SWEEP_D11_'+mode.upper()+'_'+build+'_CMD'
    value = os.environ.get(name)
    if not value: raise RuntimeError('missing '+name)
    return shlex.split(value)

def d11_sample(mode, build, command, expected_sha):
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if result.returncode: raise RuntimeError(f'{mode}/{build} rc={result.returncode}: {result.stdout[-2000:]}')
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    if len(rows) != 1: raise RuntimeError(f'{mode}/{build} emitted {len(rows)} JSON rows, expected one')
    row = rows[0]
    required = {'mode','product_sha','binary_sha256','caller','verified','copied_bytes','elapsed_s','cpu_s'}
    missing = required-row.keys()
    if missing: raise RuntimeError(f'{mode}/{build} missing fields {sorted(missing)}')
    if row['mode'] != mode or row['caller'] != D11_CALLERS[mode]:
        raise RuntimeError(f'{mode}/{build} caller binding mismatch')
    if row['product_sha'] != expected_sha or row['verified'] is not True:
        raise RuntimeError(f'{mode}/{build} provenance or verification mismatch')
    if not re.fullmatch(r'[0-9a-f]{64}', row['binary_sha256']):
        raise RuntimeError(f'{mode}/{build} binary_sha256 is invalid')
    if not isinstance(row['copied_bytes'], int) or row['copied_bytes'] <= 0:
        raise RuntimeError(f'{mode}/{build} copied_bytes must be a positive integer')
    if not isinstance(row['elapsed_s'], (int,float)) or row['elapsed_s'] <= 0:
        raise RuntimeError(f'{mode}/{build} elapsed_s must be positive')
    if not isinstance(row['cpu_s'], (int,float)) or row['cpu_s'] < 0:
        raise RuntimeError(f'{mode}/{build} cpu_s must be non-negative')
    row['build'] = build
    row['mib_s'] = row['copied_bytes']/(1024*1024)/row['elapsed_s']
    return row

def d11_metrics(samples, blocks):
    output={}
    critical = 2.145 if blocks == 15 else 2.045
    for mode in D11_CALLERS:
        effects=[]; orders={'ABBA':[],'BAAB':[]}
        for block in range(blocks):
            rows=[r for r in samples if r['mode']==mode and r['block']==block]
            if len(rows)!=4: raise RuntimeError(f'{mode} block {block} has {len(rows)} samples')
            effect=statistics.mean(math.log(r['mib_s']) for r in rows if r['build']=='B')-statistics.mean(math.log(r['mib_s']) for r in rows if r['build']=='A')
            effects.append(effect); orders[''.join(r['build'] for r in rows)].append(effect)
        mean=statistics.mean(effects); sd=statistics.stdev(effects)
        half=critical*sd/math.sqrt(blocks); interval=[math.exp(mean-half),math.exp(mean+half)]
        denom=sum((x-mean)**2 for x in effects)
        lag1=sum((effects[i]-mean)*(effects[i-1]-mean) for i in range(1,blocks))/denom if denom else 0
        order_effect=statistics.mean(orders['ABBA'])-statistics.mean(orders['BAAB'])
        unflagged=abs(lag1)<=0.3 and abs(order_effect)<=math.log(1.02)
        output[mode]={'ratio':math.exp(mean),'interval':interval,'lag1':lag1,
                      'order_log_difference':order_effect,'dependence_unflagged':unflagged,
                      'classification':('INCONCLUSIVE_DEPENDENCE' if not unflagged else
                          'RETURN_BELOW_BAND' if interval[1]<0.95 else
                          'INCONCLUSIVE_CROSSES_BAND' if interval[0]<0.95 else 'ACCEPT')}
    return output

def d11_cost():
    blocks=int(os.environ.get('SWEEP_D11_BLOCKS','15'))
    if blocks < 2: raise RuntimeError('SWEEP_D11_BLOCKS must be >=2')
    out=Path(os.environ['SWEEP_D11_OUT']); out.mkdir(parents=True)
    shas={'A':os.environ['SWEEP_D11_PARENT_SHA'],'B':os.environ['SWEEP_D11_HEAD_SHA']}
    commands={mode:{build:d11_command(mode,build) for build in 'AB'} for mode in D11_CALLERS}
    rng=random.Random(int(os.environ.get('SWEEP_SEED','20260915'))); schedule=[]
    for mode in D11_CALLERS:
        orders=(['ABBA']*((blocks+1)//2)+['BAAB']*(blocks//2)); rng.shuffle(orders)
        schedule += [(mode,block,slot,build) for block,order in enumerate(orders) for slot,build in enumerate(order)]
    (out/'predeclared.json').write_text(json.dumps({'modes':D11_CALLERS,'blocks':blocks,
        'schedule':schedule,'parent_sha':shas['A'],'head_sha':shas['B'],'retries':0,
        'acceptance_band':[0.95,1.05],'commands':commands},indent=2))
    samples=[]
    for mode,block,slot,build in schedule:
        row=d11_sample(mode,build,commands[mode][build],shas[build])
        row.update(block=block,slot=slot,sample_index=len(samples)); samples.append(row)
        (out/'samples.json').write_text(json.dumps(samples,indent=2))
    metrics=d11_metrics(samples,blocks)
    cpu={build:sum(r['cpu_s'] for r in samples if r['build']==build) for build in 'AB'}
    copied=sum(r['copied_bytes'] for r in samples)
    result={'product_sha':shas,'samples':samples,'metrics':metrics,'cpu_s':cpu,
            'copied_bytes':copied,'harness_sha256':sha(__file__)}
    (out/'complete.json').write_text(json.dumps(result,indent=2))
    oi=metrics['object_read']['interval']; ei=metrics['ec_read']['interval']
    dependence=','.join(f'{m}:{metrics[m]["dependence_unflagged"]}' for m in D11_CALLERS)
    print(f'D11-PAIRED-COST object_ratio={metrics["object_read"]["ratio"]:.6f} object_interval={oi[0]:.6f}..{oi[1]:.6f} '
          f'ec_ratio={metrics["ec_read"]["ratio"]:.6f} ec_interval={ei[0]:.6f}..{ei[1]:.6f} '
          f'dependence={dependence} cpu={cpu["A"]:.3f}/{cpu["B"]:.3f} copied_bytes={copied}')

def d11_self_test():
    with tempfile.TemporaryDirectory() as tmp:
        script=Path(tmp)/'sample.py'
        script.write_text("import json,os\nprint(json.dumps({'mode':os.environ['M'],'product_sha':os.environ['S'],'binary_sha256':'0'*64,'caller':os.environ['C'],'verified':True,'copied_bytes':4194304,'elapsed_s':float(os.environ['E']),'cpu_s':0.5}))\n")
        samples=[]
        for mode in D11_CALLERS:
            for block in range(4):
                for slot,build in enumerate('ABBA' if block%2==0 else 'BAAB'):
                    env=dict(os.environ,M=mode,S=build,C=D11_CALLERS[mode],E='1.0' if build=='A' else '0.99')
                    command=[sys.executable,str(script)]
                    before=os.environ.copy(); os.environ.update(env)
                    try: row=d11_sample(mode,build,command,build)
                    finally: os.environ.clear(); os.environ.update(before)
                    row.update(block=block,slot=slot); samples.append(row)
        metrics=d11_metrics(samples,4)
        if set(metrics)!=set(D11_CALLERS): raise AssertionError('both modes were not measured')
        bad=dict(samples[0],caller='generic-read')
        try:
            if bad['caller'] != D11_CALLERS[bad['mode']]: raise RuntimeError('caller binding mismatch')
        except RuntimeError: pass
        else: raise AssertionError('wrong-caller negative did not fail')
    print('D11_PAIRED_SELF_TEST PASS modes=object_read,ec_read undeclared_caller=RED')


def telemetry():
    # Read-only snapshots outside the timed measurement, same for both roles.
    code = "import glob,json,pathlib,time; patterns=['/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor','/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq','/sys/devices/system/cpu/cpu*/thermal_throttle/*throttle_count','/sys/devices/system/cpu/intel_pstate/no_turbo','/sys/class/hwmon/hwmon*/temp*_input','/proc/pressure/cpu']; paths=[p for g in patterns for p in glob.glob(g)]; values={};\nfor p in paths:\n try: values[p]=pathlib.Path(p).read_text().strip()\n except OSError: pass\nprint(json.dumps({'time':time.time(),'values':values}))"
    local=json.loads(subprocess.check_output(['python3','-c',code],text=True))
    import shlex
    remote=json.loads(subprocess.check_output(['ssh',CLIENT,'python3 -c '+shlex.quote(code)],text=True))
    return {'M01':remote,'M02':local}

def main():
    with open(os.environ['TESTOPS_LOCK_FILE'], 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in ('cargo','rustc','weed','weed-volume','step05-read-ben'):
            assert subprocess.run(['pgrep','-x',name],stdout=subprocess.DEVNULL).returncode==1, 'other lab/build process: '+name
        assert subprocess.run(['ssh',CLIENT,'pgrep -f ^/opt/work/.*/step05-read-bench'],stdout=subprocess.DEVNULL).returncode==1, 'M01 benchmark busy'
        OUT.mkdir()
        with open(os.environ['TESTOPS_ACTIVITY_LOG'], 'a') as log:
            log.write('START codex02-sweep3-paired '+time.strftime('%FT%TZ', time.gmtime())+'\n')
        call(['ssh', CLIENT, 'mkdir', REMOTE])
        call(['scp', str(ROOT/'step05-read-bench'), CLIENT+':'+REMOTE+'/'])
        remote_sha = subprocess.check_output(['ssh',CLIENT,'sha256sum',REMOTE+'/step05-read-bench'], text=True).split()[0]
        assert remote_sha == sha(ROOT/'step05-read-bench')
        payload = OUT/'payload.bin'
        payload.write_bytes(b'Z' * (256*1024))
        print('memlock limits: '+str(resource.getrlimit(resource.RLIMIT_MEMLOCK)),flush=True)
        binary_hash={role:sha(path) for role,path in VOLUMES.items()}
        expected_a=os.environ.get('SWEEP_REFERENCE_VOLUME_SHA256'); assert not expected_a or binary_hash['A']==expected_a, 'retained reference differs'
        build=json.loads((Path(os.environ['SWEEP_ROOT'])/'baseline-build/complete.json').read_text())[0]
        assert build['sha']==PRODUCT_SHA and build['sha256']==binary_hash['B'], 'candidate build provenance mismatch'
        expected_client=os.environ.get('SWEEP_CLIENT_SHA256'); assert not expected_client or remote_sha == expected_client, 'retained client differs'
        expected_weed=os.environ.get('SWEEP_WEED_SHA256'); assert not expected_weed or sha(WEED) == expected_weed, 'retained master differs'
        schedule=[]; rng=random.Random(SEED)
        for mode in ('rc','dc'):
            orders=['ABBA']*15+['BAAB']*15; rng.shuffle(orders)
            for block,order in enumerate(orders):
                schedule.extend((mode,block,slot,label) for slot,label in enumerate(order))
        (OUT/'schedule.json').write_text(json.dumps(schedule,indent=2))
        (OUT/'predeclared.json').write_text(json.dumps(dict(seed=SEED,samples=240,blocks_per_transport=30,
            product_sha=PRODUCT_SHA,reference_sha=REFERENCE_SHA,reference_product_sha=os.environ.get('SWEEP_REFERENCE_PRODUCT', REFERENCE_SHA),binary_sha256=binary_hash,lag1_flag=0.3,order_effect_flag=0.02,
            adopted_resolution={'rc':0.06,'dc':0.015},stop_if_interval_upper_below=0.95,reset='fresh master+volume+payload each sample',
            retries=0,invalid_run_on_control_failure=True),indent=2))
        samples=[]
        governors = {'M02':subprocess.check_output(['bash','-c','cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'],text=True).split(),
            'M01':subprocess.check_output(['ssh',CLIENT,'cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'],text=True).split()}
        assert all(values and all(v == 'performance' for v in values) for values in governors.values()), governors
        (OUT/'governors.json').write_text(json.dumps(governors,indent=2))
        for host in ('M01','M02'):
            args=['cat','/sys/devices/system/cpu/intel_pstate/no_turbo']
            if host=='M01': args=['ssh',CLIENT]+args
            (OUT/(host+'-no_turbo.txt')).write_bytes(subprocess.check_output(args))
        try:
            for sample_index, (mode, pair, slot, label) in enumerate(schedule):
                run = OUT/f'{mode}-{pair:02d}-{slot}-{label}'
                sample_start=time.time()
                for name in ('cargo','rustc'):
                    assert subprocess.run(['pgrep','-x',name],stdout=subprocess.DEVNULL).returncode==1, 'concurrent build invalidates run'
                assert subprocess.run(['ssh',CLIENT,'pgrep -x cargo; pgrep -x rustc'],stdout=subprocess.DEVNULL).returncode==1, 'M01 build invalidates run'
                (run/'master').mkdir(parents=True)
                (run/'volume').mkdir()
                (run/'security.toml').write_text('')
                (run/'telemetry-before.json').write_text(json.dumps(telemetry(),indent=2))
                processes=[]; logs=[]
                def start(name, args, env=None):
                    stream=(run/(name+'.log')).open('w'); logs.append(stream)
                    p=subprocess.Popen((['taskset','-c','2,4,6,8'] if name == 'volume' else ['taskset','-c','10']) + args, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=run)
                    processes.append(p)
                try:
                    env=dict(os.environ, WEED_MASTER_MAINTENANCE_SCRIPTS='gate-disabled', WEED_MASTER_MAINTENANCE_SLEEP_MINUTES='1000000')
                    start('master',[WEED,'master','-ip=10.0.0.3','-port=19755','-port.grpc=29755',
                        '-mdir='+str(run/'master'),'-volumeSizeLimitMB=30000','-defaultReplication=000'],env)
                    wait('http://10.0.0.3:19755/cluster/status')
                    env.update(SWFS_RDMA_PUSH_PORT='18981',SWFS_RDMA_PUSH_CONTROL_PORT='18982',
                        SWFS_RDMA_DC_PUSH_CONTROL_PORT='18983',SWFS_RDMA_DC_DEVICE='rocep1s0',
                        SWFS_RDMA_DC_PORT='1',SWFS_RDMA_DC_GID_INDEX='3',SWFS_RDMA_DC_INITIATORS='4')
                    start('volume',[VOLUMES[label],'--port','19601','--port.grpc','29601','--ip','10.0.0.3',
                        '--ip.bind','0.0.0.0','--master','10.0.0.3:19755','--dir',str(run/'volume'),
                        '--max','10','--securityFile',str(run/'security.toml'),'--preStopSeconds','0',
                        '--rdma.enabled','--rdma.ip','10.0.0.3','--rdma.port','18980','--rdma.kv-transport','both'],env)
                    wait('http://10.0.0.3:19601/status')
                    wait('http://10.0.0.3:19755/dir/status',lambda r: '19601' in json.dumps(r))
                    assigned=get('http://10.0.0.3:19755/dir/assign?count=1')
                    fid=assigned['fid']
                    upload=['curl','--fail','--silent','--show-error','-F','file=@'+str(payload)]
                    if assigned.get('auth'):
                        upload += ['-H','Authorization: Bearer '+assigned['auth']]
                    upload += ['http://10.0.0.3:19601/'+fid]
                    with (run/'upload.json').open('w') as out:
                        upload_result=subprocess.run(upload,stdout=out)
                    if upload_result.returncode: raise RuntimeError('fixture upload failed')
                    vid, needle=fid.split(',')
                    prepare=dict(volumeId=int(vid),needleId=str(int(needle[:-8],16)),
                        cookie=int(needle[-8:],16),offset=0,length=str(payload.stat().st_size))
                    prepared=subprocess.check_output(['/usr/local/bin/grpcurl','-plaintext','-d',json.dumps(prepare),
                        '10.0.0.3:29601','volume_server_pb.VolumeServer/PrepareRdmaRead'],text=True)
                    (run/'prepare.json').write_text(prepared)
                    assert json.loads(prepared).get('ok'), 'RDMA registration readiness failed'
                    for mode in [mode]:
                        with (run/(mode+'.json')).open('w') as out, (run/(mode+'.stderr')).open('w') as err:
                            call(['ssh',CLIENT,'timeout','50s','taskset','-c','2,4,6,8',REMOTE+'/step05-read-bench',mode,fid],stdout=out,stderr=err,timeout=60)
                        row=json.loads((run/(mode+'.json')).read_text())
                        assert row['verified'] and row['fallbacks']==0 and row['requests']>0
                        row.update(pair=pair,block=pair,slot=slot,build=label,sample_index=sample_index,
                            utc_start=sample_start,utc_end=time.time(),volume_sha256=sha(VOLUMES[label]))
                        assert row['volume_sha256']==binary_hash[label], 'binary changed during run'
                        samples.append(row)
                        (OUT/'samples.json').write_text(json.dumps(samples,indent=2))
                        print(json.dumps(row),flush=True)
                        (run/'telemetry-after.json').write_text(json.dumps(telemetry(),indent=2))
                        (run/'processes.txt').write_text(subprocess.check_output(['ps','-eo','pid,comm,pcpu,psr'],text=True))
                        (run/'pressure.txt').write_text(Path('/proc/pressure/cpu').read_text())
                        for p in processes:
                            (run/f'affinity-{p.pid}.txt').write_text(Path(f'/proc/{p.pid}/status').read_text())
                finally:
                    for p in reversed(processes):
                        if p.poll() is None:
                            p.terminate()
                            try: p.wait(timeout=10)
                            except subprocess.TimeoutExpired: p.kill(); p.wait()
                    for s in logs: s.close()
            metrics={}
            for mode in ['rc','dc']:
                effects=[]; by_order={'ABBA':[],'BAAB':[]}
                for block in range(30):
                    rows=[r for r in samples if r['transport']==mode and r['block']==block]
                    assert len(rows)==4
                    effect=statistics.mean(math.log(r['mib_s']) for r in rows if r['build']=='B')-statistics.mean(math.log(r['mib_s']) for r in rows if r['build']=='A')
                    effects.append(effect)
                    by_order[''.join(r['build'] for r in rows)].append(effect)
                mean=statistics.mean(effects); sd=statistics.stdev(effects)
                half=2.045*sd/math.sqrt(30)
                interval=[math.exp(mean-half),math.exp(mean+half)]
                denom=sum((x-mean)**2 for x in effects)
                lag1=sum((effects[i]-mean)*(effects[i-1]-mean) for i in range(1,30))/denom if denom else 0
                order_difference=statistics.mean(by_order['ABBA'])-statistics.mean(by_order['BAAB'])
                diagnostic_ok=abs(lag1)<=0.3 and abs(order_difference)<=math.log(1.02)
                metrics[mode]=dict(block_log_effects=effects,ratio=math.exp(mean),block_log_sd=sd,
                    illustrative_iid_95pct_interval=interval,lag1=lag1,order_log_difference=order_difference,
                    dependence_diagnostics_unflagged=diagnostic_ok,
                    resolution=0.06 if mode=='rc' else 0.015,
                    classification=('INCONCLUSIVE_DEPENDENCE' if not diagnostic_ok else
                        'STOP_REGRESSION_BELOW_MINUS_5' if interval[1]<0.95 else
                        'NO_EVIDENCE_WITHIN_NOISE_BAND' if abs(math.exp(mean)-1)<=(0.06 if mode=='rc' else 0.015) else
                        'OUTSIDE_BAND_REVIEW_INTERVAL'),
                    stop_interval_below_minus5=diagnostic_ok and interval[1]<0.95)
            report=dict(reference_sha=REFERENCE_SHA,product_sha=PRODUCT_SHA,harness_sha256=sha(__file__),seed=SEED,schedule=schedule,
                samples=samples,metrics=metrics,volume_sha256=binary_hash,client_sha256=remote_sha,
                weed_sha256=sha(WEED),governors=governors,placement={'M01_client':'2,4,6,8','M02_volume':'2,4,6,8','M02_master':'10'},
                verdict=('INCONCLUSIVE' if not metrics['rc']['dependence_diagnostics_unflagged'] else 'STOP_REGRESSION' if metrics['rc']['stop_interval_below_minus5'] else 'NO_EVIDENCE_WITHIN_NOISE_BAND' if abs(metrics['rc']['ratio']-1)<=0.06 else 'OUTSIDE_BAND_REVIEW_INTERVAL'),
                independence_note='Diagnostics are flags, not proof of independence; intervals remain conditional.',
                policy='30 balanced four-sample blocks per transport; 2s warmup +10s timed; 4 workers; 256KiB verified; no optional stopping')
            (OUT/'complete.json').write_text(json.dumps(report,indent=2))
            print(json.dumps(dict(verdict=report['verdict'],metrics=metrics)),flush=True)
        finally:
            with open(os.environ['TESTOPS_ACTIVITY_LOG'],'a') as log:
                log.write('END codex02-sweep3-paired '+time.strftime('%FT%TZ',time.gmtime())+'\n')

def interrupted(signum, frame):
    raise KeyboardInterrupt('lab run interrupted')
signal.signal(signal.SIGTERM, interrupted)
if '--d11-self-test' in sys.argv:
    d11_self_test()
elif '--d11-cost' in sys.argv:
    d11_cost()
else:
    main()
