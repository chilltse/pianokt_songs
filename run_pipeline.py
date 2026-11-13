#!/usr/bin/env python3
"""One command: discover -> source evidence -> download -> verify -> phrase-split.

Current automatic provider: Mutopia only. Do not misrepresent its Romantic/
Baroque/Ragtime variety as including recent copyrighted Pop/Anime/OST songs.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import build
from catalog_source import Fetcher, FetchError, listing_url, parse_listing, parse_detail, parse_ly_header, score_staff_proof, DETAIL_URL, MAX_LY, MAX_MIDI


def safe_piece_id(i):
    if not str(i).isdigit():raise ValueError('invalid id')
    return int(i)


def persist_manifest(rows,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=build.FIELDS,extrasaction='ignore');w.writeheader();w.writerows(rows)


def collect_ids(fetcher,max_pages):
    ids=[];seen=set();offset=0;visited=set()
    for p in range(max_pages):
        if offset in visited:break
        visited.add(offset)
        url=listing_url(offset)
        try:chunk,next_offset=parse_listing(fetcher.get(url).decode('utf-8',errors='replace'))
        except (FetchError,ValueError) as e:
            print(f'[discovery warning] page={p} offset={offset}: {e}',file=sys.stderr)
            break
        for n in chunk:
            if n not in seen:ids.append(n);seen.add(n)
        if next_offset is None:break
        offset=next_offset
        if (p+1)%20==0:print(f'[discover] pages={p+1}, unique works={len(ids)}',flush=True)
    return ids


def load_previous(out):
    path=out/'audit.json'
    if not path.is_file():return [],[]
    try:
        results=json.loads(path.read_text(encoding='utf-8'))
        with (out/'manifest.csv').open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
        return results,rows
    except (OSError,ValueError,KeyError,FileNotFoundError):return [],[]


def run(args):
    if args.min_sec<=0 or args.max_sec<args.min_sec or args.target<1:raise SystemExit('Invalid cut/target')
    root=Path(args.root);out=Path(args.out)
    root.mkdir(parents=True,exist_ok=True);out.mkdir(parents=True,exist_ok=True)
    fetcher=Fetcher(args.delay,args.timeout,args.retries)
    print('[1/3] Discovering per-piece Mutopia catalogue entries...',flush=True)
    ids=collect_ids(fetcher,args.max_pages)
    if not ids:
        print('No discoveries: check access to www.mutopiaproject.org',file=sys.stderr)
        return 2
    # Source pages are sorted by composer. Reproducible shuffling avoids a
    # catalogue dominated by the first alphabetical few composers.
    random.Random(args.seed).shuffle(ids)
    results,rows=load_previous(out) if args.resume else ([],[])
    # v2 could incorrectly reject every work due to its over-strict metadata
    # parser. Re-queue ONLY those failed parser records; keep other audit data.
    parser_fail_ids={str(x.get('piece_id')) for x in results
                     if x.get('status')=='rejected'
                     and x.get('reason')=='Cannot read instrument/style on Mutopia page'}
    if parser_fail_ids:
        results=[x for x in results if str(x.get('piece_id')) not in parser_fail_ids]
        rows=[x for x in rows if str(x.get('piece_id')) not in parser_fail_ids]
        print(f'[resume] retrying {len(parser_fail_ids)} entries rejected by the v2 instrument/style parser',flush=True)
    prior={str(x.get('piece_id')) for x in results if x.get('piece_id')}

    already_accepted=Counter(r.get('genre') for r in results if r.get('status')=='accepted')
    composers=Counter(r.get('composer') for r in results if r.get('status')=='accepted')
    kept=sum(x.get('status')=='accepted' for x in results)
    print(f'[2/3] Found {len(ids)} work IDs; previous attempts={len(prior)}, accepted={kept}',flush=True)
    performed=0
    parser_error_streak=0
    for pid in ids:
        if kept>=args.target or performed>=args.max_attempts:break
        if str(pid) in prior:continue
        performed+=1
        url=DETAIL_URL.format(id=pid)
        rec={'piece_id':str(pid),'source_url':url}
        row={'piece_id':str(pid)}
        try:
            html=fetcher.get(url).decode('utf-8',errors='replace')
            try:
                info=parse_detail(html,url)
            except ValueError as error:
                # Keep a real page snapshot, not just a generic exception. This
                # makes changes in source markup immediately debuggable.
                if 'Cannot read instrument/style' in str(error):
                    debug_dir=out/'debug';debug_dir.mkdir(parents=True,exist_ok=True)
                    if len(list(debug_dir.glob('detail_*.html'))) < 3:
                        (debug_dir/f'detail_{pid}.html').write_text(html,encoding='utf-8')
                raise
            parser_error_streak=0

            row.update(info)
            if info['instrument'].strip().lower()!='piano':raise ValueError('not_solo_piano')
            if args.max_per_genre and already_accepted[info['genre']]>=args.max_per_genre:
                raise ValueError('genre_quota_reached')
            if args.max_per_composer and composers[info['composer']]>=args.max_per_composer:
                raise ValueError('composer_quota_reached')
            # Official LilyPond source is downloaded and kept as provenance.
            ly_rel=f'{pid}.ly';ly_path=root/ly_rel
            if ly_path.is_file():ly=ly_path.read_text(encoding='utf-8-sig',errors='replace')
            else:
                raw=fetcher.get(info['ly_url'],MAX_LY)
                ly=raw.decode('utf-8-sig',errors='replace');ly_path.write_text(ly,encoding='utf-8')
            header=parse_ly_header(ly)
            if header['composer']:
                composer=re.sub(r'\s*\([^)]*\d{3,4}[^)]*\)\s*$','',header['composer']).strip()
                if composer:row['composer']=composer
            if header['title'] and header['title'].strip():row['title']=header['title'].strip()
            # The actual per-piece page is authoritative for the license, not
            # the source file's possibly inconsistent header.
            score_map=score_staff_proof(ly)
            row.update({'score_file':ly_rel,'file':f'{pid}.mid',
                        'hand_source':'score_staff_verified' if score_map else 'original_explicit'})
            dest=root/row['file']
            if not dest.is_file() or not dest.read_bytes().startswith(b'MThd'):
                raw=fetcher.get(info['midi_url'],MAX_MIDI)
                if not raw.startswith(b'MThd'):raise ValueError('download_not_standard_midi')
                dest.write_bytes(raw)
            # Need an original source/scores-derived hand proof; nothing is
            # manually assigned based solely on high/low pitch.
            accepted=build.process(row,root,out,(args.min_sec,args.max_sec,args.ideal_sec))
            rec=accepted
            if rec.get('status')=='accepted':
                kept+=1;already_accepted[info['genre']]+=1;composers[row['composer']]+=1
                print(f'[ACCEPT {kept}/{args.target}] {row["title"]} — {row["composer"]} ({info["genre"]}); segments={len(rec["segments"])}',flush=True)
        except (FetchError,ValueError,UnicodeError,OSError) as e:
            rec={**{k:row.get(k,'') for k in build.FIELDS},'piece_id':str(pid),'source_url':url,
                 'status':'rejected','reason':str(e)[:140]}
        except Exception as e:
            rec={**{k:row.get(k,'') for k in build.FIELDS},'piece_id':str(pid),'source_url':url,
                 'status':'rejected','reason':'unexpected:'+str(e)[:140]}
        results.append(rec);prior.add(str(pid));rows.append(row)
        if rec.get('reason')=='Cannot read instrument/style on Mutopia page':
            parser_error_streak+=1
        else:
            parser_error_streak=0

        if performed%args.checkpoint_every==0 or rec.get('status')=='accepted':
            persist_manifest(rows,out/'manifest.csv')
            summary=build.write_reports(results,out,args.target,{'discovered_source_ids':len(ids),'source':'Mutopia'})
            if performed%args.checkpoint_every==0:
                print(f'[progress] processed={performed}, total attempted={len(results)}, accepted={kept}, top_rejections={Counter(r.get("reason") for r in results if r.get("status")!="accepted").most_common(3)}',flush=True)
        if parser_error_streak>=args.fail_fast_parser_errors:
            persist_manifest(rows,out/'manifest.csv')
            build.write_reports(results,out,args.target,{'discovered_source_ids':len(ids),'source':'Mutopia','attempted_this_run':performed})
            print(f'[FATAL] {parser_error_streak} consecutive instrument/style parse failures; stopping early. Inspect {out}/debug/detail_*.html',file=sys.stderr)
            return 2

    persist_manifest(rows,out/'manifest.csv')
    summary=build.write_reports(results,out,args.target,{'discovered_source_ids':len(ids),'source':'Mutopia','attempted_this_run':performed})
    print('[3/3] Final report:\n'+json.dumps(summary,indent=2,ensure_ascii=False),flush=True)
    if kept==0:print('0 accepted: inspect output/audit.json. MIDI track names may not match score staff names; this is intentionally fail-closed.',file=sys.stderr)
    return 0 if kept>=args.target else 1


def main():
    ap=argparse.ArgumentParser(description='PianoKT automatic rights-first Mutopia MIDI collector + hand audit + 20–40s segmentation')
    ap.add_argument('--target',type=int,default=300)
    ap.add_argument('--root',default='input');ap.add_argument('--out',default='output')
    ap.add_argument('--max-pages',type=int,default=240,help='Mutopia pages (10 works/page)')
    ap.add_argument('--max-attempts',type=int,default=1500,help='Maximum newly attempted works per run')
    ap.add_argument('--max-per-composer',type=int,default=25,help='Quota after verified acceptance (0 disables)')
    ap.add_argument('--max-per-genre',type=int,default=100,help='Quota after verified acceptance (0 disables)')
    ap.add_argument('--min-sec',type=float,default=20);ap.add_argument('--max-sec',type=float,default=40)
    ap.add_argument('--ideal-sec',type=float,default=30)
    ap.add_argument('--delay',type=float,default=0.3,help='Minimum interrequest pause in seconds')
    ap.add_argument('--timeout',type=float,default=25)
    ap.add_argument('--retries',type=int,default=2)
    ap.add_argument('--seed',type=int,default=3407)
    ap.add_argument('--checkpoint-every',type=int,default=10)
    ap.add_argument('--resume',action=argparse.BooleanOptionalAction,default=True)
    ap.add_argument('--fail-fast-parser-errors',type=int,default=5,help='Abort early if Mutopia page format changes instead of burning through the entire catalogue')

    args=ap.parse_args();sys.exit(run(args))

if __name__=='__main__':main()
