#!/usr/bin/env python3
"""Optional staged workflow: list per-work source/rights records (no download yet)."""
import argparse,csv,json,random
from catalog_source import Fetcher,FetchError,parse_listing,parse_detail,listing_url,DETAIL_URL

def discover(fetcher,max_pages=240,max_pieces=1200,seed=3407):
    ids=[];seen=set();offset=0
    for _ in range(max_pages):
        items,next_offset=parse_listing(fetcher.get(listing_url(offset)).decode('utf-8',errors='replace'))
        for i in items:
            if i not in seen:seen.add(i);ids.append(i)
        if next_offset is None:break
        offset=next_offset
    random.Random(seed).shuffle(ids)
    records=[]
    for pid in ids[:max_pieces]:
        url=DETAIL_URL.format(id=pid)
        try:
            info=parse_detail(fetcher.get(url).decode('utf-8',errors='replace'),url)
            if info['instrument'].strip().lower()=='piano':records.append({'piece_id':str(pid),**info})
        except (ValueError,FetchError):continue
    return records

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--max-pages',type=int,default=240);p.add_argument('--max-pieces',type=int,default=1500)
    p.add_argument('--out',default='candidate_catalog.csv');p.add_argument('--delay',type=float,default=0.3)
    args=p.parse_args();records=discover(Fetcher(args.delay),args.max_pages,args.max_pieces)
    fields=['piece_id','title','composer','genre','license','license_url','source_url','attribution','instrument','midi_url','ly_url','source_license_label']
    with open(args.out,'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(records)
    print(json.dumps({'source':'Mutopia','solo_piano_candidates':len(records),'catalog':args.out},indent=2))

if __name__=='__main__':main()
