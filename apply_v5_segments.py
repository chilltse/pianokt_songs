#!/usr/bin/env python3
"""Safely replace accepted segment_*.mid with verified phrase_v5 output.
Dry run by default. Does not change reference.mid or difficulty_bin.
"""
import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

PATTERN = re.compile(r'^segment_(\d{3})\.mid$')


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_atomic(path, obj):
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def numbered(files):
    ids=[]
    for p in files:
        match=PATTERN.fullmatch(p.name)
        if not match: raise ValueError(f'unexpected segment filename: {p.name}')
        ids.append(int(match.group(1)))
    if sorted(ids)!=list(range(1,len(ids)+1)):
        raise ValueError(f'missing or duplicated segment numbering: {sorted(ids)}')


def gather(out):
    source = out/'phrase_v5'
    accepted = out/'accepted'
    audit_path=out/'audit.json'
    if not source.is_dir() or not accepted.is_dir() or not audit_path.exists():
        raise RuntimeError('Expected output/phrase_v5, output/accepted, output/audit.json')
    audit=load_json(audit_path)
    if not isinstance(audit,list): raise ValueError('audit.json should be a list')
    by_key={}
    for i,r in enumerate(audit):
        if r.get('status')=='accepted':
            key=str(r.get('sha256',''))[:16]
            if not key or key in by_key: raise ValueError(f'ambiguous audit accepted sha: {key}')
            by_key[key]=i
    plans=[]; skipped=[]
    for src in sorted(source.iterdir()):
        if not src.is_dir(): continue
        key=src.name
        dst=accepted/key
        meta_src=src/'metadata_v5.json'
        meta_dst=dst/'metadata.json'
        if not dst.is_dir() or key not in by_key:
            skipped.append((key,'not accepted / no matching audit record'));continue
        if not meta_src.exists() or not meta_dst.exists():
            skipped.append((key,'missing v5 or original metadata'));continue
        mv=load_json(meta_src); mo=load_json(meta_dst)
        if mv.get('segmentation_version')!='phrase_v5':
            skipped.append((key,'v5 metadata does not specify phrase_v5'));continue
        if mv.get('sha256','')[:16]!=key or mo.get('sha256','')[:16]!=key:
            skipped.append((key,'SHA/metadata mismatch'));continue
        clips=mv.get('segments') or []
        files=sorted(src.glob('segment_*.mid'))
        if not clips or len(files)!=len(clips):
            skipped.append((key,'v5 segment metadata/file count mismatch'));continue
        try:
            numbered(files)
            for j,(clip,fp) in enumerate(zip(clips,files),1):
                if fp.stat().st_size<20: raise ValueError('empty/tiny MIDI')
                expect=f'segment_{j:03d}.mid'
                if fp.name!=expect: raise ValueError('nonsequential MIDI files')
                if not (20-0.02 <= float(clip['duration_sec']) <= 40+0.02):
                    raise ValueError(f'out-of-range duration: {clip["duration_sec"]}')
                if abs((float(clip['end_sec'])-float(clip['start_sec']))-float(clip['duration_sec']))>0.05:
                    raise ValueError('inconsistent segment start/end/duration')
        except (ValueError,KeyError,TypeError) as e:
            skipped.append((key,str(e)));continue
        if not (dst/'reference.mid').exists():
            skipped.append((key,'missing reference.mid'));continue
        old=sorted(dst.glob('segment_*.mid'))
        new_clips=copy.deepcopy(clips)
        for j,clip in enumerate(new_clips,1):
            clip['midi_path']=str(Path('accepted')/key/f'segment_{j:03d}.mid')
        updated={**mo,'segments':new_clips,'segmentation_version':'phrase_v5',
                 'human_audition_status':'pending'}
        plans.append((key,src,dst,files,old,updated,by_key[key]))
    return audit,plans,skipped


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',default='output')
    ap.add_argument('--apply',action='store_true',help='Actually replace old segments (default: dry run)')
    ap.add_argument('--strict',action='store_true',help='Abort if any v5 folder cannot be applied')
    args=ap.parse_args()
    out=Path(args.out).resolve()
    audit,plans,skipped=gather(out)
    print(f'Eligible songs: {len(plans)}; skipped: {len(skipped)}; mode: {"APPLY" if args.apply else "DRY RUN"}')
    for key,src,dst,files,old,updated,idx in plans:
        print(f'  {key}: old {len(old)} -> v5 {len(files)} segments')
    for key,why in skipped: print(f'  SKIP {key}: {why}')
    if args.strict and skipped: raise SystemExit('Strict mode: skipped v5 entries; no changes made')
    if not args.apply:
        print('No changes made. Run with --apply after reviewing counts.');return
    if not plans: raise SystemExit('No eligible files; nothing modified.')
    backup=out/'v5_apply_backups'/dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(out/'audit.json',backup/'audit.json')
    changed=[]
    try:
        for key,src,dst,files,old,updated,idx in plans:
            bk=backup/key;bk.mkdir()
            shutil.copy2(dst/'metadata.json',bk/'metadata.json')
            for f in old: shutil.copy2(f,bk/f.name)
            # Stage files in the same filesystem before replacing anything.
            stage=dst/'.v5_segment_stage'
            if stage.exists(): raise RuntimeError(f'staging directory already exists: {stage}')
            stage.mkdir()
            try:
                for f in files: shutil.copy2(f,stage/f.name)
                for f in old: f.unlink()
                for f in sorted(stage.glob('segment_*.mid')): os.replace(f,dst/f.name)
                write_atomic(dst/'metadata.json',updated)
            finally:
                shutil.rmtree(stage,ignore_errors=True)
            audit[idx]={**audit[idx],'segments':copy.deepcopy(updated['segments']),
                        'segmentation_version':'phrase_v5','human_audition_status':'pending'}
            changed.append((key,dst,bk))
        write_atomic(out/'audit.json',audit)
    except Exception:
        # Restore the current piece as well as completed pieces.
        for key,src,dst,files,old,updated,idx in plans:
            bk=backup/key
            if not (bk/'metadata.json').exists():continue
            for f in dst.glob('segment_*.mid'):f.unlink()
            for f in bk.glob('segment_*.mid'):shutil.copy2(f,dst/f.name)
            shutil.copy2(bk/'metadata.json',dst/'metadata.json')
        shutil.copy2(backup/'audit.json',out/'audit.json')
        print('ERROR: restored previous segments and metadata.');raise
    print(f'Updated {len(changed)} songs. Backup: {backup}')
    print('reference.mid and difficulty_bin were not changed.')

if __name__=='__main__':main()
