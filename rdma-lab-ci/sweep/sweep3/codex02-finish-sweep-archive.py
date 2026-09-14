import pathlib,hashlib,json
r=pathlib.Path('/data/nvme/testdev/codex02-integration-368fe309e-20260912')
a=r.parent/'codex02-integration-368fe309e-20260912-evidence.tgz';d=pathlib.Path('/mnt/smb/work/share/testops/results')/a.name
assert a.is_file() and d.is_file()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
x=sha(a);assert sha(d)==x,'Shared payload hash differs'
record={'archive':str(a),'share':str(d),'sha256':x,'bytes':a.stat().st_size,'files':len((r/'archive-files.nul').read_bytes().split(b'\0'))-1,'share_hash_readback':True,'publication_note':'CIFS rejected copy2 timestamp preservation AFTER the data copy. Both complete payload hashes independently match; archive/tests not rerun.'}
(r/'archive-result.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))