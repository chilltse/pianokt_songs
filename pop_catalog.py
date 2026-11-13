#!/usr/bin/env python3
"""PianoKT pop MIDI discovery and guarded local import.

No fabricated rights or hand verification. Site catalogue entries are candidates,
not automatically deployable songs. Does not bypass paywalls or login.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, re, shutil, sys, tempfile, time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

COLUMNS=['candidate_id','provider','title','composer','genre','source_url','midi_url',
         'difficulty_label','local_midi','license','license_url','attribution','rights_note',
         'hand_proof','status','notes']
SEED_MIDISHOW=[
 ('All About U','G.E.M.','https://www.midishow.com/midi/all-about-u-midi-download-253453'),
 ('18','G.E.M.','https://www.midishow.com/midi/253444.html'),
 ('新的心跳','G.E.M.','https://www.midishow.com/midi/253458.html'),
 ('后会无期','G.E.M.','https://www.midishow.com/midi/32731.html'),
 ('唯一','G.E.M.','https://www.midishow.com/midi/215272.html'),
 ('夜曲','周杰伦','https://www.midishow.com/midi/263688.html'),
 ('哪里都是你','周杰伦','https://www.midishow.com/midi/78407.html'),
 ('晴天','周杰伦','https://www.midishow.com/midi/183422'),
 ('江南','林俊杰','https://www.midishow.com/midi/41911.html'),
 ('新地球','林俊杰','https://www.midishow.com/midi/147532.html')]

UA='PianoKT research catalogue assistant/1.0 (manual licensing review required)'

def read_csv(path):
    path=Path(path)
    if not path.exists():return []
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def write_csv(path, rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS,extrasaction='ignore');w.writeheader();w.writerows(rows)

def cid(provider,url):return provider+'_'+hashlib.sha256(url.encode()).hexdigest()[:16]

def add(existing,new):
    by_id={r.get('candidate_id'):r for r in existing}
    for r in new:
        k=r['candidate_id'];old=by_id.get(k)
        if old:
            for key in ('title','composer','genre','midi_url','difficulty_label'):
                if not old.get(key) and r.get(key):old[key]=r[key]
        else:by_id[k]=r
    return list(by_id.values())

def get_html(s,url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in ('tuneonmusic.com','www.tuneonmusic.com','midishow.com','www.midishow.com'):
        raise ValueError('Not an approved website')
    r=s.get(url,timeout=25,allow_redirects=True)
    r.raise_for_status()
    if urlparse(r.url).hostname not in ('tuneonmusic.com','www.tuneonmusic.com','midishow.com','www.midishow.com'):
        raise ValueError('Unexpected HTML redirect')
    if len(r.content)>3_000_000:raise ValueError('HTML too large')
    return BeautifulSoup(r.text,'html.parser')

def infer_title_and_level(anchor):
    txt=' '.join(anchor.get_text(' ',strip=True).split())
    # Tuneonmusic catalogue links contain title + difficulty + collection + duration.
    m=re.search(r'\b(Beginner|Intermediate|Advanced)\s*(?:Version)?\b',txt,re.I)
    level=m.group(1).capitalize() if m else ''
    title=txt[:m.start()].strip(' -·') if m else txt
    title=re.sub(r'\s*-\s*(Beginner|Intermediate|Advanced)\s+Version\s*$','',title,flags=re.I).strip()
    return title,level

def tuneonmusic_index(s,query='',limit=300):
    soup=get_html(s,'https://tuneonmusic.com/midi-files/')
    out=[];seen=set()
    for a in soup.find_all('a',href=True):
        href=urljoin('https://tuneonmusic.com/',a['href'])
        if '/music-sheets/' not in urlparse(href).path:continue
        if href in seen:continue
        title,level=infer_title_and_level(a)
        if not title or len(title)>180:continue
        if level not in ('Beginner','Intermediate'):continue
        # Search is local over public catalogue; no hidden or private endpoint.
        if query and query.casefold() not in (title+' '+href).casefold():continue
        seen.add(href)
        path=urlparse(href).path.lower()
        genre='Pop / English' if '/artists/' in path else 'Piano / Other'
        if any(x in path for x in ('/anime/','/naruto/','/attack-on-titan/')):genre='Anime'
        if '/games/' in path or '/kingdom-hearts/' in path:genre='Game OST'
        out.append(dict(candidate_id=cid('tuneonmusic',href),provider='tuneonmusic',title=title,
                        composer='',genre=genre,source_url=href,midi_url='',difficulty_label=level,
                        status='discovered',notes='Catalogue entry; composer and rights require review'))
        if len(out)>=limit:break
    return out

def midishow_seeds(query=''):
    out=[]
    for title,artist,url in SEED_MIDISHOW:
        if query and query.casefold() not in (title+' '+artist).casefold():continue
        out.append(dict(candidate_id=cid('midishow',url),provider='midishow',title=title,
                        composer='',genre='C-Pop',source_url=url,midi_url='',difficulty_label='',
                        status='discovered',notes='Artist is performer/reference, NOT verified composer; download manually'))
    return out

def extract_direct_midi(soup,source_url):
    """Never guess download endpoints or scrape protected player assets."""
    for a in soup.find_all('a',href=True):
        href=urljoin(source_url,a['href'])
        p=urlparse(href)
        if p.scheme!='https':continue
        if p.hostname not in ('tuneonmusic.com','www.tuneonmusic.com'):continue
        if re.search(r'\.midi?$',p.path,re.I):return href
    return ''

def enrich(session,rows,limit=50):
    done=0
    for r in rows:
        if done>=limit:break
        if r['provider']!='tuneonmusic' or r.get('midi_url'):continue
        try:
            soup=get_html(session,r['source_url'])
            text=soup.get_text(' ',strip=True)
            if 'Instrumentation' in text and 'Solo Piano' not in text:
                r['status']='needs_score_review'
            r['midi_url']=extract_direct_midi(soup,r['source_url'])
            if not r['midi_url']:
                r['notes']='Open song page and use its official Download MIDI control; direct URL not exposed in HTML'
            done+=1
            print('[DETAIL]',r['title'],'direct_midi='+str(bool(r['midi_url'])))
        except (requests.RequestException,ValueError) as e:
            r['notes']='Detail fetch error: '+str(e)[:100]
        time.sleep(.3)

def download_rows(rows,destination,limit=50):
    """Only explicit direct links from Tuneonmusic; local research, not publish authorization."""
    s=requests.Session();s.headers['User-Agent']=UA
    root=Path(destination);root.mkdir(parents=True,exist_ok=True);count=0
    for r in rows:
        if count>=limit:break
        if r.get('local_midi'):continue
        url=r.get('midi_url','');p=urlparse(url)
        if r.get('provider')!='tuneonmusic' or p.scheme!='https' or p.hostname not in ('tuneonmusic.com','www.tuneonmusic.com') or not re.search(r'\.midi?$',p.path,re.I):continue
        dest=root/(r['candidate_id']+'.mid')
        try:
            with s.get(url,timeout=35,stream=True,allow_redirects=True) as resp:
                resp.raise_for_status()
                if urlparse(resp.url).hostname not in ('tuneonmusic.com','www.tuneonmusic.com'):raise ValueError('Download redirected to unknown host')
                data=b''
                for chunk in resp.iter_content(65536):
                    data+=chunk
                    if len(data)>5_000_000:raise ValueError('Too large')
            if not data.startswith(b'MThd'):raise ValueError('Not MIDI bytes')
            dest.write_bytes(data)
            r['local_midi']=str(dest)
            r['status']='downloaded_pending_review';count+=1
            print('[DOWNLOADED]',r['title'])
        except (ValueError,requests.RequestException) as e:
            r['notes']='Download failed: '+str(e)[:130]
    return count

def inspect_rows(rows):
    try:import pretty_midi
    except ImportError:raise SystemExit('pip install pretty_midi')
    for r in rows:
        p=Path(r.get('local_midi') or '')
        if not r.get('local_midi') or not p.is_file():continue
        try:
            pm=pretty_midi.PrettyMIDI(str(p))
            note_tracks=[i for i in pm.instruments if i.notes]
            names=[i.name for i in note_tracks]
            if len(note_tracks)==2 and not any(i.is_drum for i in note_tracks):
                import re
                norm=[re.sub(r'[^a-z]','',x.lower()) for x in names]
                L={'left','lefthand','lh','manosinistra'};R={'right','righthand','rh','manodestra'}
                proved=(norm[0] in L and norm[1] in R) or (norm[1] in L and norm[0] in R)
            else:proved=False
            r['hand_proof']='explicit_track_names' if proved else 'unverified'
            r['notes']=f'tracks={len(note_tracks)}; names={names}; duration={pm.get_end_time():.1f}s'
            r['status']='hands_verified_rights_pending' if proved else 'hand_mapping_pending'
            print('[INSPECT]',r['title'],r['status'],names)
        except Exception as e:r['status']='invalid_midi';r['notes']=str(e)[:140]

def save_atomic_json(path,content):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.pop-',dir=p.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:
            json.dump(content,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def ingest(rows,root,out,target,max_new):
    """Strictly use existing validator, then the v5 phrase method. No licensing shortcuts."""
    import build, phrase_v5, pretty_midi
    root=Path(root);out=Path(out);root.mkdir(exist_ok=True,parents=True);out.mkdir(exist_ok=True,parents=True)
    audit_file=out/'audit.json';existing=json.loads(audit_file.read_text()) if audit_file.exists() else []
    if not isinstance(existing,list):raise ValueError('Existing audit must be a list')
    backup=out/'pop_import_backups'/datetime.now().strftime('%Y%m%d_%H%M%S')
    backup.mkdir(parents=True,exist_ok=True)
    if audit_file.exists():shutil.copy2(audit_file,backup/'audit.json')
    existing_urls={x.get('source_url') for x in existing if x.get('status')=='accepted'}
    existing_hashes={x.get('sha256') for x in existing if x.get('status')=='accepted'}
    added=0;outcomes=[]
    for r in rows:
        if added>=max_new or len(existing_hashes)>=target:break
        candidate=r.get('candidate_id','');file=Path(r.get('local_midi') or '')
        if not candidate or not r.get('local_midi') or not file.is_file():continue
        if r.get('source_url') in existing_urls:continue
        if r.get('hand_proof')!='explicit_track_names':continue
        # Composer must be actual composer; do not convert performing artist into composer.
        if not all(r.get(k,'').strip() for k in ('title','composer','license','license_url','source_url')):continue
        if r['license'] not in build.ALLOWED:continue
        if r.get('rights_note','').strip().lower() not in ('verified-for-publication','verified-for-research'):
            continue
        sha=hashlib.sha256(file.read_bytes()).hexdigest()
        if sha in existing_hashes:continue
        dest_input=root/('pop_'+candidate+'.mid')
        if file.resolve()!=dest_input.resolve():shutil.copyfile(file,dest_input)
        obj=dict(file=dest_input.name,title=r['title'],composer=r['composer'],genre=r.get('genre') or 'Pop',
                 license=r['license'],license_url=r['license_url'],source_url=r['source_url'],
                 attribution=r.get('attribution') or '',hand_source='original_explicit',
                 midi_url=r.get('midi_url',''),piece_id=candidate)
        record=build.process(obj,root,out,(20.,40.,30.))
        if record.get('status')=='accepted':
            # Use v5 on new record before committing it. If v5 fails, leave as a
            # review candidate (not accepted) and clean only its own new directory.
            dest=out/'accepted'/sha[:16]
            pm=pretty_midi.PrettyMIDI(str(dest/'reference.mid'))
            hands=phrase_v5._hand_lookup(pm,record)
            cuts,reason=(phrase_v5.segment_bounds(pm,hands) if hands else ([], 'unverified_hands_v5'))
            if reason:
                shutil.rmtree(dest,ignore_errors=True)
                record={**obj,'status':'rejected','reason':'v5_'+str(reason)}
            else:
                parts=[]
                scratch=dest/'_v5_staging';scratch.mkdir()
                try:
                    for ix,(a,b,metrics) in enumerate(cuts,1):
                        name=f'segment_{ix:03d}.mid'
                        build.write_clip(pm,hands,a,b,scratch/name)
                        check=pretty_midi.PrettyMIDI(str(scratch/name))
                        if len([x for x in check.instruments if x.notes])!=2:raise ValueError('v5_clip_lost_hand')
                        parts.append(dict(segment_id=f'{sha[:16]}_v5_{ix:03d}',start_sec=float(a),end_sec=float(b),duration_sec=round(float(b-a),3),midi_path=f'accepted/{sha[:16]}/{name}',cut_metrics={k:(v.item() if hasattr(v,'item') else v) for k,v in metrics.items()}))
                    for p in dest.glob('segment_*.mid'):p.unlink()
                    for p in scratch.glob('*.mid'):shutil.move(str(p),dest/p.name)
                    scratch.rmdir()
                    record.update(segments=parts,segmentation_version='phrase_v5',human_audition_status='pending',rights_review_status=r['rights_note'])
                    (dest/'metadata.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
                    existing_hashes.add(sha);existing_urls.add(r['source_url']);added+=1
                    r['status']='accepted_pending_listening';print('[ACCEPT]',r['title'])
                except Exception as e:
                    shutil.rmtree(dest,ignore_errors=True)
                    record={**obj,'status':'rejected','reason':'v5_export_error:'+str(e)[:100]}
        if record.get('status')!='accepted':
            r['status']='rejected';r['notes']=record.get('reason','')
            print('[REJECT]',r['title'],r['notes'])
        existing.append(record);outcomes.append(record)
        save_atomic_json(audit_file,existing)
        build.write_reports(existing,out,target,{'source':'Mutopia + vetted pop additions'})
    print(json.dumps({'previous_records':len(existing)-len(outcomes),'new_attempts':len(outcomes),'added':added,'total_accepted':len(existing_hashes),'backup':str(backup)},ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser(description='Find C-Pop/English pop piano MIDI candidates and import only verified files')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('discover');a.add_argument('--provider',choices=['tuneonmusic','midishow','all'],default='all');a.add_argument('--query',default='');a.add_argument('--catalog',default='pop_candidates.csv');a.add_argument('--limit',type=int,default=500);a.add_argument('--enrich',type=int,default=0)
    a=sub.add_parser('download');a.add_argument('--catalog',default='pop_candidates.csv');a.add_argument('--dir',default='input/pop');a.add_argument('--limit',type=int,default=100)
    a=sub.add_parser('inspect');a.add_argument('--catalog',default='pop_candidates.csv')
    a=sub.add_parser('ingest');a.add_argument('--catalog',default='pop_candidates.csv');a.add_argument('--root',default='input');a.add_argument('--out',default='output');a.add_argument('--target',type=int,default=300);a.add_argument('--max-new',type=int,default=300)
    a=p.parse_args()
    if a.command=='discover':
        old=read_csv(a.catalog);found=[]
        if a.provider in ('midishow','all'):found+=midishow_seeds(a.query)
        if a.provider in ('tuneonmusic','all'):
            s=requests.Session();s.headers['User-Agent']=UA
            try:found+=tuneonmusic_index(s,a.query,a.limit)
            except Exception as e:print('[WARN] Tuneonmusic discovery failed:',e,file=sys.stderr)
        merged=add(old,found)
        if a.enrich:
            s=requests.Session();s.headers['User-Agent']=UA
            enrich(s,merged,a.enrich)
        write_csv(a.catalog,merged)
        print(json.dumps({'new':len(merged)-len(old),'total':len(merged),'catalog':a.catalog},ensure_ascii=False))
    else:
        rows=read_csv(a.catalog)
        if not rows:raise SystemExit('Catalogue is empty: run discover first')
        if a.command=='download':download_rows(rows,a.dir,a.limit)
        elif a.command=='inspect':inspect_rows(rows)
        elif a.command=='ingest':ingest(rows,a.root,a.out,a.target,a.max_new)
        write_csv(a.catalog,rows)
if __name__=='__main__':main()
