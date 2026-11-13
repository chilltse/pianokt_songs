#!/usr/bin/env python3
"""Discover real Mutopia score candidates, plus PDMX v9 metadata shortlist.

Mutopia mode uses official MutopiaProject GitHub score files and compiles them
with locally installed LilyPond. Does not scrape copyrighted song downloads.
PDMX mode writes a shortlist only; neither rights nor hand verification is
silently assumed. Source is the user's locally downloaded PDMX.csv.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, re, shutil, subprocess, tempfile, time
from pathlib import Path
from urllib.parse import quote
import requests

TREE='https://api.github.com/repos/MutopiaProject/MutopiaProject/git/trees/master?recursive=1'
RAW='https://raw.githubusercontent.com/MutopiaProject/MutopiaProject/master/'
LICENSE_URL='https://creativecommons.org/publicdomain/mark/1.0/'

def git_request(session,url,max_bytes=8_000_000,attempts=3):
    if not url.startswith((TREE.split('/git/')[0],RAW)):
        raise ValueError('unexpected GitHub host')
    last=None
    for i in range(attempts):
        try:
            with session.get(url,timeout=(15,80),stream=True) as r:
                if r.status_code in (403,429):raise RuntimeError('GitHub API rate limited; resume later or set GITHUB_TOKEN')
                r.raise_for_status()
                data=bytearray()
                for chunk in r.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>max_bytes:raise ValueError('remote file exceeds safety limit')
                return bytes(data)
        except requests.RequestException as e:
            last=e;time.sleep(min(2**i,8))
    raise RuntimeError(f'Unable to download {url}: {last}')

def header_value(source,key):
    m=re.search(r'(?m)^\s*'+re.escape(key)+r'\s*=\s*"([^"\n]+)"',source)
    return m.group(1).strip() if m else ''

def score_candidate(source):
    from catalog_source import score_staff_proof
    title=header_value(source,'title') or header_value(source,'mutopiatitle')
    composer=header_value(source,'composer')
    instr=header_value(source,'mutopiainstrument')
    genre=header_value(source,'style') or 'Classical / Unclassified'
    rights=header_value(source,'copyright')
    maintainer=header_value(source,'maintainer')
    if not title or not composer or not instr or 'piano' not in instr.lower():return None
    if ',' in instr or 'voice' in instr.lower() or 'organ' in instr.lower():return None
    if rights.lower().strip() != 'public domain':return None
    hands=score_staff_proof(source)
    if not hands:return None
    if '\\midi' not in source:return None
    return {'title':title,'composer':composer,'genre':genre,'license':'Public Domain',
            'license_url':LICENSE_URL, 'attribution':('Typeset by '+maintainer+'; Mutopia Project') if maintainer else 'Mutopia Project',
            'hand_source':'score_staff_verified','license_evidence_type':'score_header_declaration',
            'rights_review_status':'pending', 'score_staff_names':hands}

def create_session():
    s=requests.Session();s.headers['User-Agent']='PianoKT-catalog-v5/1.0 (GitHub public music research)'
    if os.environ.get('GITHUB_TOKEN'):s.headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
    return s

def fetch_tree(s,cache):
    cache=Path(cache)
    if cache.exists():obj=json.loads(cache.read_text())
    else:
        obj=json.loads(git_request(s,TREE,10_000_000))
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps(obj),encoding='utf8')
    if obj.get('truncated'):raise RuntimeError('GitHub tree truncated; refuse incomplete discovery')
    items=[x['path'] for x in obj['tree'] if x['path'].startswith('ftp/') and x['path'].endswith('.ly')]
    return sorted(items)

def discover_mutopia(out='output',root='input',max_scanned=1200,max_candidates=500,delay=.15,compile_midi=False,target=300):
    import build
    if compile_midi:
        from phrase_v5 import segment_bounds as phrase_bounds
        def _quality_bounds(pm,hands,min_s=20.,max_s=40.,ideal_s=30.):
            segments,reason=phrase_bounds(pm,hands,min_s,max_s,ideal_s)
            return [(a,b) for a,b,_ in segments] if not reason else []
        build.segment_bounds=_quality_bounds
    session=create_session();out=Path(out);root=Path(root)
    cache=out/'v5_discovery';cache.mkdir(parents=True,exist_ok=True)
    root.mkdir(parents=True,exist_ok=True)
    paths=fetch_tree(session,cache/'mutopia_git_tree.json')
    audit_path=out/'audit.json'
    existing=json.loads(audit_path.read_text(encoding='utf8')) if audit_path.exists() else []
    known={str(r.get('source_url','')).strip() for r in existing}
    existing_sha={r.get('sha256','') for r in existing if r.get('status')=='accepted'}
    # Versioned records in a separate state file. Original audit untouched until
    # one work completes successfully and is checkpointed.
    progress_path=cache/'git_progress.json'
    state=json.loads(progress_path.read_text(encoding='utf8')) if progress_path.exists() else {}
    listfile=cache/'mutopia_score_candidates.csv'
    if listfile.exists():
        with listfile.open(newline='',encoding='utf8') as f:candidates=list(csv.DictReader(f))
    else:candidates=[]
    known_candidate_urls={r.get('source_url') for r in candidates}
    scans=0;added=0;accepted=0;rejected=0
    if compile_midi and audit_path.exists():
        import shutil as _shutil
        bak=cache/'audit_before_first_v5_import.json'
        if not bak.exists():_shutil.copy2(audit_path,bak)
    for path in paths:
        if scans>=max_scanned or added>=max_candidates:break
        url=RAW+quote(path,safe='/')
        if url in known:continue
        st=state.get(path)
        if st and st.get('terminal') and (not compile_midi or st.get('reason')!='discovered_not_compiled'):continue
        scans+=1
        ly_path=root/'v5_mutopia'/hashlib.sha256(path.encode()).hexdigest()[:20]/Path(path).name
        ly_path.parent.mkdir(parents=True,exist_ok=True)
        try:
            if not ly_path.exists():
                ly_path.write_bytes(git_request(session,url,1_000_000))
                time.sleep(delay)
            content=ly_path.read_text(encoding='utf8',errors='replace')
            candidate=score_candidate(content)
            if not candidate:
                state[path]={'terminal':True,'reason':'not_verified_two_staff_pd_piano_score'}
                rejected+=1
                continue
            candidate.update({'source_url':url,'score_url':url,'score_file':str(ly_path.relative_to(root)),
                              'piece_id':'git_'+hashlib.sha256(path.encode()).hexdigest()[:16]})
            if url not in known_candidate_urls:
                candidates.append(candidate);known_candidate_urls.add(url)
            added+=1
            print(f'[FOUND {added}] {candidate["title"]} / {candidate["composer"]} ({candidate["genre"]})',flush=True)
            if compile_midi:
                if shutil.which('lilypond') is None:raise RuntimeError('LilyPond unavailable; macOS: brew install lilypond')
                with tempfile.TemporaryDirectory() as temp:
                    prefix=Path(temp)/'score'
                    proc=subprocess.run(['lilypond','-dno-point-and-click','-o',str(prefix),str(ly_path.resolve())],
                                        capture_output=True,text=True,timeout=120)
                    midi=sorted(Path(temp).glob('score*.midi'))+sorted(Path(temp).glob('score*.mid'))
                    if proc.returncode or len(midi)!=1:raise RuntimeError('lilypond_compile_failed_or_ambiguous_midi:'+proc.stderr[-170:])
                    midi_path=ly_path.parent/'rendered.mid'
                    shutil.copyfile(midi[0],midi_path)
                candidate.update({'file':str(midi_path.relative_to(root)),'midi_url':'generated_from_licensed_lilypond'})
                result=build.process(candidate,root,out,(20.,40.,30.))
                if result.get('status')=='accepted' and result.get('sha256') in existing_sha:
                    result={**candidate,'status':'rejected','reason':'duplicate_of_existing_accepted_sha'}
                if result.get('status')=='accepted':
                    for seg in result.get('segments',[]):seg['phrase_boundary_method']='phrase_v5_measure_rest_no_sustain_dp'
                    meta_file=out/'accepted'/result['sha256'][:16]/'metadata.json'
                    if meta_file.exists():meta_file.write_text(json.dumps({**result,'segmentation_version':'phrase_v5'},ensure_ascii=False,indent=2),encoding='utf8')
                    result['license_evidence_type']='score_header_declaration'
                    result['rights_review_status']='pending'
                    existing_sha.add(result['sha256']);existing.append(result);accepted+=1
                    print(f'   [ACCEPT] {candidate["title"]}',flush=True)
                else:
                    result={**candidate,**result};existing.append(result);rejected+=1
                    print(f'   [REJECT] {result.get("reason")}',flush=True)
                state[path]={'terminal':True,'reason':result.get('reason','accepted')}
                # Save a backup once; all old results are preserved.
                import build as validator
                validator.write_reports(existing,out,target,{'source':'Mutopia official + GitHub score extensions'})
            else:state[path]={'terminal':True,'reason':'discovered_not_compiled'}
        except (Exception,KeyboardInterrupt) as e:
            if isinstance(e,KeyboardInterrupt):raise
            state[path]={'terminal':False,'reason':str(e)[:240]}
            print('[RETRYABLE]',path,str(e)[:150],flush=True)
        finally:
            progress_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
            if candidates:
                with (cache/'mutopia_score_candidates.csv').open('w',newline='',encoding='utf8') as f:
                    fields=list(dict.fromkeys(['piece_id','title','composer','genre','license','license_url','source_url','score_url','score_file','attribution','hand_source','license_evidence_type','rights_review_status']))
                    w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(candidates)
    summary={'scores_in_repo':len(paths),'examined_this_run':scans,'new_candidates':added,'new_accepted':accepted,
             'new_rejected_or_skipped':rejected, 'output':str(cache/'mutopia_score_candidates.csv')}
    print(json.dumps(summary,indent=2,ensure_ascii=False));return summary


def _yes(v):return str(v).strip().lower() in ('true','1','yes')

def pdmx_shortlist(csv_file,out='output',top=2500):
    """Discover genuinely new genre-rich PDMX records from user's official CSV.

    This is a rights/metadata shortlist, NOT accepted MIDI. PDMX MID/MXL files
    live in large archive downloads and require further staff verification.
    """
    src=Path(csv_file)
    if not src.is_file():raise FileNotFoundError(f'Download PDMX.csv from https://zenodo.org/records/15571083 : {src}')
    import heapq
    h=[];seq=0
    with src.open(newline='',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if _yes(r.get('license_conflict')) or _yes(r.get('has_paywall')):continue
            if not r.get('mid') or not r.get('mxl') or r['mid'].strip().lower() in ('na','n/a') or r['mxl'].strip().lower() in ('na','n/a'):continue
            if not r.get('composer_name') or not (r.get('song_name') or r.get('title')):continue
            if str(r.get('license','')).lower().replace(' ','') not in ('publicdomain','cc0','cc0-1.0'):continue
            tracks=str(r.get('tracks','')).lower()
            if not ('piano' in tracks or 'keyboard' in tracks):continue
            try:
                complexity=float(r.get('complexity') or 4)
                duration=float(r.get('song_length.seconds') or 0)
                n_tracks=int(float(r.get('n_tracks') or 0))
            except ValueError:continue
            if complexity>1.8 or duration<22 or duration>600 or not 1<=n_tracks<=2:continue
            genre=r.get('genres') or 'Unknown'
            try:favorites=max(0.,float(r.get('n_favorites') or 0))
            except ValueError:favorites=0.
            score=(2.0-complexity)*6 + (4 if _yes(r.get('subset:deduplicated')) else 0) + \
                  (3 if _yes(r.get('is_rated')) else 0) +min(5.,favorites/20)
            item={'title':r.get('song_name') or r.get('title'),'composer':r.get('composer_name'),
                  'genre':genre,'license':r.get('license'),'license_url':r.get('license_url'),
                  'mid_archive_member':r.get('mid'),'mxl_archive_member':r.get('mxl'),
                  'complexity':complexity,'n_tracks':n_tracks,'source_dataset':'PDMX v9',
                  'rights_status':'pending_per_piece_review','hands_status':'unverified_mxl_staves',
                  'pdmx_metadata_path':r.get('metadata')}
            seq+=1
            if len(h)<top:heapq.heappush(h,(score,seq,item))
            elif score>h[0][0]:heapq.heapreplace(h,(score,seq,item))
    rows=[x[2] for x in sorted(h,reverse=True)]
    folder=Path(out)/'v5_discovery';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'pdmx_candidate_shortlist.csv'
    if rows:
        with path.open('w',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps({'shortlisted':len(rows),'csv':str(path),
                      'WARNING':'PDMX files not downloaded. None is validated as two-handed; do not import directly.'},indent=2))

def main():
    p=argparse.ArgumentParser(description='PianoKT real discovery: Mutopia score library / PDMX large-scale shortlist')
    sub=p.add_subparsers(dest='source',required=True)
    m=sub.add_parser('mutopia-github');m.add_argument('--out',default='output');m.add_argument('--root',default='input')
    m.add_argument('--max-scanned',type=int,default=1200);m.add_argument('--max-candidates',type=int,default=500)
    m.add_argument('--compile',action='store_true',help='Compile LilyPond MIDI and run real acceptance; needs lilypond')
    m.add_argument('--target',type=int,default=300)
    x=sub.add_parser('pdmx');x.add_argument('--csv',required=True);x.add_argument('--out',default='output');x.add_argument('--top',type=int,default=2500)
    a=p.parse_args()
    if a.source=='mutopia-github':discover_mutopia(a.out,a.root,a.max_scanned,a.max_candidates,compile_midi=a.compile,target=a.target)
    else:pdmx_shortlist(a.csv,a.out,a.top)
if __name__=='__main__':main()
