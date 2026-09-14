import os,pathlib,re,hashlib,json,subprocess,sys
if os.environ.get('SWEEP_DRY_RUN')=='1': print('DRY codex02-retain-coverage-profile'); raise SystemExit
r=pathlib.Path(sys.argv[1]); profile=sys.argv[2]
out=r/(profile+'-binaries');out.mkdir(exist_ok=True)
rows=[]
for match in re.finditer(r'Running .*? \((/[^\n]+)\)',(r/(profile+'.log')).read_text(errors='replace')):
 src=pathlib.Path(match[1]); assert src.is_file(),src
 dst=out/src.name
 subprocess.run(['cp','--reflink=auto',str(src),str(dst)],check=True)
 def digest(path):
  h=hashlib.sha256()
  with path.open('rb') as f:
   for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
  return h.hexdigest()
 source_hash=digest(src); assert source_hash==digest(dst)
 rows.append({'profile':profile,'source':str(src),'retained':str(dst),'sha256':source_hash,'bytes':src.stat().st_size})
assert rows,'No instrumented test binaries identified'
(r/(profile+'-instrumented-binaries.json')).write_text(json.dumps(rows,indent=2))
print(profile+' binaries_retained='+str(len(rows)),flush=True)