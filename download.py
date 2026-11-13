#!/usr/bin/env python3
"""Optional staged workflow: authorized source catalog -> local original MIDI + score + manifest.

Downloaded does not mean verified; run build.py to accept only checked scores.
"""
import argparse,csv,json,re
from pathlib import Path
import build
from catalog_source import Fetcher,FetchError,MAX_LY,MAX_MIDI,parse_ly_header,score_staff_proof

def download(records,fetcher,root,max_downloads):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    manifest=[];issues=[]
    for rec in records:
        if len(manifest)>=max_downloads:break
        pid=rec.get('piece_id','').strip()
        if not pid.isdigit() or not rec.get('midi_url') or not rec.get('ly_url'):
            issues.append({'piece_id':pid,'error':'missing_id_or_urls'});continue
        try:
            score_path=root/(pid+'.ly');midi_path=root/(pid+'.mid')
            if score_path.exists():source=score_path.read_text(encoding='utf-8-sig',errors='replace')
            else:
                source=fetcher.get(rec['ly_url'],MAX_LY).decode('utf-8-sig',errors='replace')
                score_path.write_text(source,encoding='utf-8')
            if not midi_path.exists() or not midi_path.read_bytes().startswith(b'MThd'):
                data=fetcher.get(rec['midi_url'],MAX_MIDI)
                if not data.startswith(b'MThd'):raise ValueError('not_standard_midi')
                midi_path.write_bytes(data)
            header=parse_ly_header(source)
            composer=re.sub(r'\s*\([^)]*\d{3,4}[^)]*\)\s*$','',header.get('composer','')).strip()
            row={**rec,'file':pid+'.mid','score_file':pid+'.ly',
                 'hand_source':'score_staff_verified' if score_staff_proof(source) else 'original_explicit',
                 'composer':composer or rec.get('composer',''),
                 'title':header.get('title') or rec.get('title',''),'difficulty_override':''}
            manifest.append(row)
        except (ValueError,FetchError,OSError) as e:
            issues.append({'piece_id':pid,'error':str(e)[:150]})
    return manifest,issues

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--catalog',default='candidate_catalog.csv');p.add_argument('--root',default='input')
    p.add_argument('--manifest',default='manifest.csv');p.add_argument('--max-downloads',type=int,default=1500)
    p.add_argument('--delay',type=float,default=0.3)
    args=p.parse_args()
    with open(args.catalog,encoding='utf-8-sig',newline='') as f:records=list(csv.DictReader(f))
    rows,issues=download(records,Fetcher(args.delay),args.root,args.max_downloads)
    with open(args.manifest,'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=build.FIELDS,extrasaction='ignore');w.writeheader();w.writerows(rows)
    Path('download_issues.json').write_text(json.dumps(issues,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'downloaded_with_manifest':len(rows),'download_failures':len(issues),'not_yet_hand_verified':len(rows)},indent=2))

if __name__=='__main__':main()
