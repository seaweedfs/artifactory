"""Decode exact TM1-R/TM1-C/TM2-G evidence; public handles are not PD/MR census."""
import hashlib
import json
import pathlib
import re

ROW = 'tests::tm_adapter_tests::tm1_r_tm1_c_tm2_g_connected_registration'
SIZES = (65536, 4194304, 8388608)

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def frames(raw):
    offset = 0
    records = []
    while offset < len(raw):
        end = raw.find(b'\n', offset)
        require(end >= 0, 'PARTIAL_HEADER')
        match = re.fullmatch(rb'TM_RAW name=("(?:[^"\\]|\\.)*") bytes=(\d+)', raw[offset:end])
        require(match is not None, 'TM_RAW_HEADER')
        size = int(match[2]); start = end + 1; stop = start + size
        require(size <= 16 * 1024 * 1024 and stop < len(raw) and raw[stop:stop+1] == b'\n', 'PARTIAL_BODY')
        name = json.loads(match[1]); body = raw[start:stop]
        require(re.search(rb'\bREAD_ERROR\b', body) is None, 'CENSUS_READ_ERROR')
        records.append((name, body.decode('utf-8', errors='strict')))
        offset = stop + 1
    return records

def values(text):
    return dict(re.findall(r'([A-Za-z_][\w]*)=([^\s]+)', text))

def correlation(text):
    row = values(text)
    require(row.get('native_pd_relation') == 'BLOCKED', 'NATIVE_PD_CLAIM')
    require(row.get('client_object', '').startswith('0x') and row.get('pipe_ordinal') == '0', 'CLIENT_IDENTITY')
    if row.get('phase') == 'pool_baseline':
        return row, None
    key = tuple(int(row[k]) for k in ('block', 'bytes', 'slots', 'operation'))
    return row, key

def provider(group, identities):
    maps = group.get('/proc/self/maps', [])
    require(len(maps) == 1, 'MAPS_MISSING_OR_DUPLICATE')
    for library in ('libsiw', 'libibverbs', 'librdmacm'):
        identity = identities[library]
        matching = [line.split(None, 5) for line in maps[0].splitlines() if len(line.split(None, 5)) == 6 and line.split(None, 5)[5] == identity['path']]
        require(bool(matching) and all(v[3] == identity['dev'] and int(v[4]) == identity['inode'] for v in matching), 'MAPPED_PROVIDER_IDENTITY')
        require(re.fullmatch('[0-9a-f]{64}', identity['sha256']) is not None, 'PROVIDER_HASH_UNBOUND')
    fds = set()
    for text in group.get('process_fd', []):
        match = re.search(r'fd="(\d+)" target=Ok\("/dev/infiniband/(uverbs\d+)"\)', text)
        if match:
            fds.add((match[1], match[2]))
    require(bool(fds), 'UVERBS_FD_MISSING')
    for fd, node in fds:
        matches = [text for text in group.get('uverbs_ibdev', []) if f'fd="{fd}"' in text and f'/sys/class/infiniband_verbs/{node}/ibdev' in text]
        require(len(matches) == 1, 'UVERBS_CORRELATION')
        text = matches[0]
        if 'ibdev=Ok(' in text:
            require(re.search(r'ibdev=Ok\("(?:[^" ]*/)?siw0"\)', text) is not None, 'WRONG_IBDEV')
        else:
            require(group.get(f'/sys/class/infiniband_verbs/{node}/ibdev') == ['siw0\n'], 'WRONG_IBDEV')

def decode(raw, stdout, identities, child_identity):
    groups = []; current = None
    for name, body in frames(raw):
        if name == 'correlation':
            row, key = correlation(body); current = (row, key, {}); groups.append(current)
        else:
            require(current is not None, 'ORPHAN_FRAME')
            current[2].setdefault(name, []).append(body)
    expected = [(block, SIZES[(j + block) % 3], slots, operation)
                for block in range(3) for j in range(3) for slots in (1, 8) for operation in range(31)]
    require(len(groups) == 1 + len(expected) * 3, 'SNAPSHOT_COUNT')
    require(groups[0][0]['phase'] == 'pool_baseline', 'BASELINE_ORDER')
    client = groups[0][0]['client_object']
    required = ('/proc/self/stat', '/proc/self/status', '/proc/self/limits', '/proc/self/numa_maps', '/proc/sys/kernel/osrelease', '/proc/modules', '/proc/self/maps', 'typed_census')
    for row, key, group in groups:
        require(row['client_object'] == client and all(len(group.get(k, [])) == 1 for k in required), 'CENSUS_IDENTITY_OR_FIELDS')
        require('native_pd_relation=BLOCKED' in group['typed_census'][0], 'TYPED_CENSUS_SCOPE')
        require(re.search(r'process_fd_count=\d+', group['typed_census'][0]) is not None, 'TYPED_CENSUS_COUNT')
        stat=group['/proc/self/stat'][0]; fields=stat[stat.rindex(')')+2:].split()
        require(int(stat.split(' ',1)[0])==child_identity['pid'] and int(fields[19])==child_identity['starttime'],'CHILD_PID_STARTTIME')
        provider(group, identities)
    by_operation = {}
    for index, key in enumerate(expected):
        triple = groups[1 + index*3:4 + index*3]
        require([r[0]['phase'] for r in triple] == ['before', 'live', 'after_checked_close'] and all(r[1] == key for r in triple), 'SNAPSHOT_ORDER_OR_CORRELATION')
        live = triple[1][2].get('public_live_mr', [])
        require(not triple[0][2].get('public_live_mr') and not triple[2][2].get('public_live_mr'),'PUBLIC_HANDLE_OUTSIDE_LIVE')
        require(len(live) == key[2], 'LIVE_HANDLE_COUNT')
        for ordinal, text in enumerate(live):
            row = values(text)
            require(tuple(int(row[k]) for k in ('block', 'bytes', 'slots', 'operation')) == key and row.get('client_object') == client, 'FOREIGN_PUBLIC_HANDLE')
            require(int(row['ordinal']) == ordinal and int(row['len']) == key[1]//key[2] and int(row['lkey']) > 0 and int(row['rkey']) > 0, 'PUBLIC_HANDLE_FIELDS')
        by_operation[key] = triple
    points = []; seen = set()
    for line in stdout.splitlines():
        if not line.startswith('TM1-R/TM1-C '):
            continue
        row = values(line); key = tuple(int(row[k]) for k in ('block', 'bytes', 'slots', 'operation')); ordinal = int(row['ordinal'])
        require(key in by_operation and (key, ordinal) not in seen and 0 <= ordinal < key[2], 'TIMING_CORRELATION')
        seen.add((key, ordinal))
        require(row['warmup'] == str(key[3] == 0).lower() and row['cleanup_error'] == 'None' and row['remaining_mrs'] == '0' and row['retained_bytes'] == '0', 'CHECKED_CLOSE_FAILED')
        require(row['native_pd_relation'] == 'BLOCKED' and row['same_client_object'] == 'true', 'LIFETIME_SCOPE')
        require(int(row['created_mrs']) == key[2] and int(row['mr_len']) == key[1]//key[2], 'REGISTRATION_GEOMETRY')
        cleanup = re.fullmatch(r'Some\((\d+)\)', row['cleanup_ns'])
        require(cleanup is not None and int(cleanup[1]) > 0 and int(row['registration_ns']) > 0, 'TIMING_INVALID')
        if key[3] != 0:
            points.append(dict(block=key[0],bytes=key[1],slots=key[2],operation=key[3],ordinal=ordinal,registration_ns=int(row['registration_ns']),cleanup_ns=int(cleanup[1])))
    require(len(seen) == 2511 and len(points) == 2430 and stdout.count('TM_SETUP connection_ns=') == 1, 'TIMING_OR_SETUP_COUNT')
    return dict(state='CONNECTED_DIAGNOSTIC_ONLY',points=points,operations=558,measured_operations=540,
                native_pd='UNAVAILABLE',kernel_mr='UNAVAILABLE',raw_sha256=hashlib.sha256(raw).hexdigest())
