#!/usr/bin/env python3
"""PianoKT v4 additive extension & conservative audit for v3 pipelines.

Existing accepted/ and audit.json are never deleted; this program writes a
read-only audit report or appends independently verified candidates.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil, sys, tempfile, time, warnings
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests
import mido
import build
from catalog_source import score_staff_proof

EXT_FIELDS = build.FIELDS + ['source_provider','license_verification','score_url']
MAX_MIDI=3_000_000; MAX_SCORE=1_000_000

def norm(s):
    s=str(s or '').lower().replace('–','-').replace('—','-')
    return re.sub(r'[^a-z0-9]+','',s)

def work_key(r):
    return (norm(r.get('title')),norm(r.get('composer')))

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
        json.dump(value,f,ensure_ascii=False,indent=2);tmp=Path(f.name)
    tmp.replace(path)

def read_audit(out):
    path=out/'audit.json'
    if not path.exists(): return []
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,list): raise ValueError('audit.json should be a list')
    return data

def midi_timing_warnings(path):
    """Detect timing metadata on tracks other than 0 without interpreting timing."""
    mf=mido.MidiFile(str(path))
    warnings=[]
    if mf.type in (0,1):
        for i,tr in enumerate(mf.tracks):
            if i==0: continue
            types={msg.type for msg in tr if msg.is_meta and msg.type in ('set_tempo','time_signature','key_signature')}
            if types: warnings.append({'track':i,'types':sorted(types)})
    return warnings

def audit_existing(out):
    results=read_audit(out)
    accepted=[x for x in results if x.get('status')=='accepted']
    bywork={};byhash={};duplicates=[];missing=[];warnings_list=[]
    for r in accepted:
        path=out/'accepted'/str(r.get('sha256',''))[:16]/'reference.mid'
        if not path.is_file():
            missing.append({'piece_id':r.get('piece_id'),'path':str(path)})
        else:
            try:
                w=midi_timing_warnings(path)
                if w:warnings_list.append({'piece_id':r.get('piece_id'),'title':r.get('title'),'tempo_metadata_warning':w})
            except Exception as e:
                warnings_list.append({'piece_id':r.get('piece_id'),'midi_parse_error':str(e)[:150]})
        wk=work_key(r)
        if all(wk):
            if wk in bywork:duplicates.append({'kind':'title_composer','first_piece_id':bywork[wk],'duplicate_piece_id':r.get('piece_id'),'title':r.get('title')})
            else:bywork[wk]=r.get('piece_id')
        h=r.get('sha256')
        if h:
            if h in byhash:duplicates.append({'kind':'sha256','first_piece_id':byhash[h],'duplicate_piece_id':r.get('piece_id')})
            else:byhash[h]=r.get('piece_id')
    report={'existing_accepted':len(accepted),'existing_rejected':len(results)-len(accepted),
            'potential_duplicates':duplicates,'missing_midi':missing,'midi_tempo_warnings':warnings_list,
            'note':'Non-destructive report; the original 42 accepted records are unchanged. Title duplicates may represent different movements.'}
    atomic_json(out/'v4_existing_audit.json',report)
    return report

def safe_https(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('URL must be HTTPS')
    host=parsed.hostname.lower()
    if host in ('localhost','127.0.0.1','::1') or host.endswith('.local'):
        raise ValueError('Local URL forbidden')
    return url

def download(url,path,max_bytes,session):
    # Only for intentionally supplied source URLs; never fetch private addresses.
    safe_https(url)
    r=session.get(url,stream=True,timeout=(10,45),allow_redirects=False)
    try:
        if 300<=r.status_code<400:raise ValueError('redirect refused; provide direct HTTPS source link')
        r.raise_for_status()
        data=bytearray()
        for part in r.iter_content(32768):
            data.extend(part)
            if len(data)>max_bytes:raise ValueError('download too large')
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('wb',dir=path.parent,delete=False) as f:
            f.write(data);tmp=Path(f.name)
        tmp.replace(path)
    finally:r.close()

def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

def write_manifest(rows,path):
    cols=build.FIELDS
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',newline='',encoding='utf-8',dir=path.parent,delete=False) as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');writer.writeheader();writer.writerows(rows);tmp=Path(f.name)
    tmp.replace(path)

def extend(args):
    out=Path(args.out);root=Path(args.root)
    if not (out/'audit.json').exists():raise SystemExit('No existing audit.json; check --out path')
    if not args.manifest:raise SystemExit('--manifest required for --extend')
    candidates=read_csv(args.manifest)
    audit=read_audit(out)
    old_manifest=read_csv(out/'manifest.csv') if (out/'manifest.csv').exists() else []
    accepted=[r for r in audit if r.get('status')=='accepted']
    existing_ids={str(r.get('piece_id')) for r in audit if r.get('piece_id')}
    existing_hash={str(r.get('sha256')) for r in accepted if r.get('sha256')}
    existing_titles={work_key(r) for r in accepted if all(work_key(r))}
    session=requests.Session();session.headers.update({'User-Agent':'PianoKT-curation-v4/1.0'})
    accepted_now=0;log=[]
    for idx,r in enumerate(candidates,1):
        if len(accepted)+accepted_now>=args.target:break
        cid=str(r.get('piece_id') or f'external-{idx}')
        if cid in existing_ids:
            log.append({'piece_id':cid,'status':'skipped','reason':'already_in_existing_audit'});continue
        entry={k:str(r.get(k) or '').strip() for k in EXT_FIELDS}
        entry['piece_id']=cid
        res={'piece_id':cid,'source_url':entry.get('source_url','')}
        try:
            if entry['license_verification']!='manually_verified':raise ValueError('license_verification_required')
            if entry['license'] not in build.ALLOWED:raise ValueError('unsupported_license')
            if not entry['license_url'] or not entry['source_url']:raise ValueError('missing_license_evidence')
            if not entry['title'] or not entry['composer'] or not entry['genre']:raise ValueError('missing_metadata')
            if work_key(entry) in existing_titles:raise ValueError('possible_duplicate_title_composer')
            # Score proof must exist and hand mapping be explicit. External
            # candidates cannot use an unverified claim of 'original_explicit'.
            if entry['hand_source']!='score_staff_verified':raise ValueError('external_requires_explicit_score_staff_proof')
            local=root/'external'/re.sub('[^a-zA-Z0-9_-]','_',cid)
            midi=local/'reference.mid';score=local/'score.ly'
            if not midi.is_file():
                if not entry['midi_url']:raise ValueError('missing_midi_url')
                download(entry['midi_url'],midi,MAX_MIDI,session)
            if not score.is_file():
                if not entry['score_url']:raise ValueError('missing_score_url')
                download(entry['score_url'],score,MAX_SCORE,session)
            if midi.read_bytes()[:4]!=b'MThd':raise ValueError('not_standard_midi')
            if not score_staff_proof(score.read_text(encoding='utf-8-sig',errors='replace')):
                raise ValueError('score_hand_proof_missing')
            tm=midi_timing_warnings(midi)
            if tm:raise ValueError('tempo_metadata_warning_requires_manual_review')
            sha=hashlib.sha256(midi.read_bytes()).hexdigest()
            if sha in existing_hash:raise ValueError('duplicate_sha256')
            entry['file']=str(midi.relative_to(root))
            entry['score_file']=str(score.relative_to(root))
            # Existing build.process independently validates license and hand track mapping.
            with warnings.catch_warnings():
                warnings.simplefilter('error',RuntimeWarning)
                res=build.process(entry,root,out,(args.min_sec,args.max_sec,args.ideal_sec))
            if res.get('status')=='accepted':
                accepted_now+=1;existing_hash.add(sha);existing_titles.add(work_key(entry))
        except Exception as e:
            res={**entry,'status':'rejected','reason':str(e)[:180]}
        audit.append(res);old_manifest.append(entry);existing_ids.add(cid)
        log.append({'piece_id':cid,'status':res.get('status'),'reason':res.get('reason','')})
        # Save after every candidate; resume is always possible.
        if not args.dry_run:
            atomic_json(out/'audit.json',audit)
            write_manifest(old_manifest,out/'manifest.csv')
            build.write_reports(audit,out,args.target,{'source':'Mutopia + manually verified external manifests'})
        print(f'[{idx}/{len(candidates)}] {cid} {res.get("status")} {res.get("reason","")}',flush=True)
    atomic_json(out/'v4_import_log.json',log)
    print(f'Added accepted={accepted_now}; total accepted={len(accepted)+accepted_now}; target={args.target}')

def main():
    p=argparse.ArgumentParser(description='Non-destructive v4 audit and optional externally curated MIDI import')
    p.add_argument('--out',default='output');p.add_argument('--root',default='input')
    p.add_argument('--audit',action='store_true',help='Audit existing 42 accepted without modifying them')
    p.add_argument('--extend',action='store_true',help='Import a vetted external CSV, preserving existing outputs')
    p.add_argument('--manifest',help='External candidate CSV with real metadata and verified licensing')
    p.add_argument('--target',type=int,default=300)
    p.add_argument('--min-sec',type=float,default=20);p.add_argument('--max-sec',type=float,default=40);p.add_argument('--ideal-sec',type=float,default=30)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if a.audit:
        result=audit_existing(Path(a.out));print(json.dumps(result,ensure_ascii=False,indent=2))
    if a.extend:
        if a.dry_run:raise SystemExit('Dry-run for imports is not implemented; use --audit first')
        if not (Path(a.out)/'audit.json').exists():raise SystemExit('Missing audit.json')
        backup=Path(a.out)/'v4_backups'/time.strftime('%Y%m%d_%H%M%S')
        backup.mkdir(parents=True,exist_ok=False)
        for name in ('audit.json','manifest.csv','accepted_catalog.csv','summary.json'):
            file=Path(a.out)/name
            if file.is_file():shutil.copy2(file,backup/name)
        print('Backup:',backup)
        extend(a)
    if not a.audit and not a.extend:p.error('specify --audit or --extend')

if __name__=='__main__':main()
