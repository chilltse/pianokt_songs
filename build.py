#!/usr/bin/env python3
"""Audited piano-MIDI validator/segmenter, usable standalone or in run_pipeline.py.

Fail closed: exactly two actual note tracks, with explicit hand names OR a
LilyPond source that explicitly names two staff tracks and matches MIDI names.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import shutil
from collections import Counter
from pathlib import Path

import mido
import pretty_midi
from catalog_source import score_staff_proof

ALLOWED={'CC0-1.0','PDM','Public Domain'} | {f'CC-BY-{v}' for v in ('2.5','3.0','4.0')} | {f'CC-BY-SA-{v}' for v in ('1.0','2.0','2.5','3.0','4.0')} | {'CC-BY-SA-3.0-DE'}
FIELDS=['file','title','composer','genre','license','license_url','source_url','attribution','hand_source','difficulty_override','score_file','midi_url','piece_id']


def field(row,key):return str(row.get(key) or '').strip()


def _name(s):return re.sub(r'[^a-z]','',str(s).lower())


def classify_tracks(pm,score_map=None,hand_source=''):
    note_inst=[i for i in pm.instruments if i.notes]
    if len(note_inst)!=2 or any(i.is_drum for i in note_inst):return None,'not_exactly_two_non_drum_note_tracks'
    hands={'L':None,'R':None}
    for i in note_inst:
        n=_name(i.name)
        if n in ('lefthand','lh','left','manosinistra'):side='L'
        elif n in ('righthand','rh','right','manodestra'):side='R'
        elif score_map and hand_source=='score_staff_verified':
            match=[v for k,v in score_map.items() if _name(k)==n]
            side=match[0] if len(match)==1 else None
        else:side=None
        if side is None or hands[side] is not None:return None,'midi_track_names_do_not_prove_hands'
        hands[side]=i
    if hands['L'] is None or hands['R'] is None:return None,'midi_track_names_do_not_prove_hands'
    if min(len(hands['L'].notes),len(hands['R'].notes))<8:return None,'insufficient_notes_per_hand'
    return hands,None


def difficulty(hands):
    notes=hands['L'].notes+hands['R'].notes
    if not notes:return 5,{}
    lo=min(n.start for n in notes);hi=max(n.end for n in notes)
    rate=len(notes)/max(1,hi-lo)
    pitches=[n.pitch for n in notes]
    groups=Counter(round(n.start,2) for n in notes)
    chord=sum(n>=3 for n in groups.values())/max(1,len(groups))
    both_count=min(len(hands['L'].notes),len(hands['R'].notes))
    # Screening ONLY, not a formal pedagogical grade.
    score=1+(rate>3)+(rate>5)+(max(pitches)-min(pitches)>42)+(chord>0.3)
    return int(score),{'notes_per_second':round(rate,2),'pitch_span':max(pitches)-min(pitches),
                      'chord_onset_fraction':round(chord,3),'min_hand_notes':both_count}


def segment_bounds(pm,hands,min_s=20.0,max_s=40.0,ideal_s=30.0):
    notes=hands['L'].notes+hands['R'].notes
    first=min(n.start for n in notes);last=max(n.end for n in notes)
    if last-first<min_s:return []
    # All interior boundaries must be musical measure boundaries, not raw 30s cuts.
    downbeats=pm.get_downbeats()
    candidates=sorted(set([round(first,5),round(last,5)]+[round(float(x),5) for x in downbeats if first+1e-4<x<last-1e-4]))
    if len(candidates)<3:return []
    n=len(candidates)
    dp=[math.inf]*n;prev=[-1]*n;dp[0]=0.
    def cut_cost(t):
        sustaining=sum(x.start<t-.025 and x.end>t+.025 for x in notes)
        attacking=sum(abs(x.start-t)<.12 for x in notes)
        # Two-hand phrase ends are favored by a shared silence around the cut.
        left_gap=not any(x.start<t+.1 and x.end>t-.22 for x in hands['L'].notes)
        right_gap=not any(x.start<t+.1 and x.end>t-.22 for x in hands['R'].notes)
        return 15*sustaining+3*attacking-(8 if left_gap and right_gap else 0)
    for j in range(1,n):
        for i in range(j-1,-1,-1):
            duration=candidates[j]-candidates[i]
            if duration>max_s+1e-6:break
            if duration<min_s-1e-6 or not math.isfinite(dp[i]):continue
            cost=dp[i]+(duration-ideal_s)**2/7.5+(cut_cost(candidates[j]) if j<n-1 else 0)
            if cost<dp[j]:dp[j]=cost;prev[j]=i
    if prev[-1]==-1:return []
    i=n-1;result=[]
    while i>0:
        j=prev[i];result.append((candidates[j],candidates[i]));i=j
    return result[::-1]


def write_clip(pm,hands,a,b,path):
    """Preserve tempo changes and meter in time-relative segment MIDI using mido."""
    tpq=480
    times,bpms=pm.get_tempo_changes()
    changes=sorted((float(t),int(round(60_000_000/float(bpm)))) for t,bpm in zip(times,bpms))
    if not changes:changes=[(0.,500000)]
    times_only=[t for t,_ in changes]
    at=max(0,bisect.bisect_right(times_only,a)-1)
    local=[(0.,changes[at][1])]+[(t-a,val) for t,val in changes if a+1e-6<t<b-1e-6]
    local=sorted(local)
    starts=[x[0] for x in local]
    prefix=[0.]
    for ix in range(1,len(local)):
        dt=local[ix][0]-local[ix-1][0]
        prefix.append(prefix[-1]+dt*1_000_000*tpq/local[ix-1][1])
    def tick(sec):
        ix=max(0,bisect.bisect_right(starts,sec)-1)
        return int(round(prefix[ix]+(sec-starts[ix])*1_000_000*tpq/local[ix][1]))
    midi=mido.MidiFile(type=1,ticks_per_beat=tpq)
    meta=mido.MidiTrack();midi.tracks.append(meta)
    meta_events=[(tick(sec),0,mido.MetaMessage('set_tempo',tempo=tempo)) for sec,tempo in local]
    tss=sorted(pm.time_signature_changes,key=lambda x:x.time)
    active_ts=next((ts for ts in reversed(tss) if ts.time<=a+1e-6),None)
    if active_ts is not None: meta_events.append((0,1,mido.MetaMessage('time_signature',numerator=active_ts.numerator,denominator=active_ts.denominator)))
    for ts in tss:
        if a+1e-6<ts.time<b-1e-6:
            meta_events.append((tick(ts.time-a),1,mido.MetaMessage('time_signature',numerator=ts.numerator,denominator=ts.denominator)))
    def insert(track,events):
        current=0
        for t,_,message in sorted(events,key=lambda e:(e[0],e[1])):
            message.time=max(0,t-current);track.append(message);current=t
        track.append(mido.MetaMessage('end_of_track',time=0))
    insert(meta,meta_events)
    for side,label in [('L','Left Hand'),('R','Right Hand')]:
        src=hands[side];track=mido.MidiTrack();midi.tracks.append(track)
        events=[(0,0,mido.MetaMessage('track_name',name=label)),(0,1,mido.Message('program_change',channel=0 if side=='L' else 1,program=src.program))]
        ch=0 if side=='L' else 1
        for note in src.notes:
            if note.end<=a or note.start>=b:continue
            start=max(0.,note.start-a);end=min(b-a,note.end-a)
            if end-start<.025:continue
            events.append((tick(start),3,mido.Message('note_on',channel=ch,note=note.pitch,velocity=max(1,note.velocity))))
            events.append((max(tick(start)+1,tick(end)),2,mido.Message('note_off',channel=ch,note=note.pitch,velocity=0)))
        insert(track,events)
    midi.save(str(path))


def process(row,root,out,limits=(20.,40.,30.)):
    base={k:field(row,k) for k in ('file','title','composer','genre','license','license_url','source_url','attribution','hand_source','score_file','midi_url','piece_id')}
    def reject(reason):return {**base,'status':'rejected','reason':reason}
    src=root/field(row,'file')
    if not src.is_file():return reject('file_missing')
    if not field(row,'title') or not field(row,'composer'):return reject('missing_title_or_composer')
    if field(row,'license') not in ALLOWED:return reject('unsupported_license')
    if not field(row,'license_url') or not field(row,'source_url'):return reject('missing_license_evidence')
    if field(row,'license').startswith('CC-BY') and not field(row,'attribution'):return reject('attribution_required')
    hand_source=field(row,'hand_source')
    if hand_source not in ('original_explicit','score_staff_verified'):return reject('hand_source_not_verified')
    score_map=None
    if hand_source=='score_staff_verified':
        proof_file=root/field(row,'score_file')
        if not proof_file.is_file():return reject('score_proof_file_missing')
        try:score_map=score_staff_proof(proof_file.read_text(encoding='utf-8-sig'))
        except (UnicodeError,OSError):return reject('score_proof_unreadable')
        if not score_map:return reject('score_does_not_explicitly_identify_two_hand_staves')
    try:pm=pretty_midi.PrettyMIDI(str(src))
    except Exception as e:return reject('midi_parse_error:'+str(e)[:60])
    hands,error=classify_tracks(pm,score_map,hand_source)
    if error:return reject(error)
    lvl,metrics=difficulty(hands)
    if field(row,'difficulty_override'):
        # Reject arbitrarily asserted difficulty values in automatic workflow;
        # override is only used for externally authenticated grading.
        return reject('unverified_difficulty_override')
    if lvl>3:return reject('above_intermediate_heuristic')
    try:segments=segment_bounds(pm,hands,*limits)
    except Exception as e:return reject('segmentation_error:'+str(e)[:60])
    if not segments:return reject('no_musical_measure_aligned_20_40_sec_segmentation')
    sha=hashlib.sha256(src.read_bytes()).hexdigest()
    dest=out/'accepted'/sha[:16]
    if dest.exists():return reject('duplicate_file_sha256')
    dest.mkdir(parents=True,exist_ok=False)
    try:
        shutil.copyfile(src,dest/'reference.mid')
        parts=[]
        for ix,(a,b) in enumerate(segments,1):
            p=dest/f'segment_{ix:03d}.mid';write_clip(pm,hands,a,b,p)
            test=pretty_midi.PrettyMIDI(str(p))
            if len(test.instruments)!=2 or any(len(k.notes)<2 for k in test.instruments):
                p.unlink(missing_ok=True);continue
            parts.append({'segment_id':f'{sha[:16]}_{ix:03d}', 'start_sec':round(a,4),'end_sec':round(b,4),
                          'duration_sec':round(b-a,4),'midi_path':str(p.relative_to(out)),
                          'phrase_boundary_method':'measure+two_hand_rest+onset_sustain_penalty'})
        if not parts:
            shutil.rmtree(dest);return reject('segments_lack_both_hands')
        metadata={**base,'status':'accepted','sha256':sha,'hand_separation_verified':True,
                  'hand_separation_method':hand_source,'score_staff_names':score_map,
                  'difficulty_bin':lvl,'difficulty_is_heuristic':True,'features':metrics,
                  'duration_sec':round(pm.get_end_time(),2),'segments':parts,
                  'human_audition_status':'pending','rights_evidence_page':field(row,'source_url')}
        (dest/'metadata.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
        return metadata
    except Exception:
        shutil.rmtree(dest,ignore_errors=True)
        raise


def write_reports(results,out,target,extra=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    accepted=[r for r in results if r.get('status')=='accepted']
    keys=['title','composer','genre','license','license_url','source_url','attribution','sha256','difficulty_bin','duration_sec','hand_separation_verified','hand_separation_method','piece_id']
    with (out/'accepted_catalog.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(accepted)
    summary={'candidates':len(results),'accepted':len(accepted),'target':target,'target_reached':len(accepted)>=target,
             'rejections':dict(Counter(r.get('reason','unknown') for r in results if r.get('status')!='accepted')),
             'genres':dict(Counter(r['genre'] for r in accepted)),
             'pending_human_audition':len(accepted)}
    if extra:summary.update(extra)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest',required=True);ap.add_argument('--root',required=True)
    ap.add_argument('--out',default='output');ap.add_argument('--target',type=int,default=300)
    ap.add_argument('--min-sec',type=float,default=20);ap.add_argument('--max-sec',type=float,default=40);ap.add_argument('--ideal-sec',type=float,default=30)
    args=ap.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    with open(args.manifest,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows:print('WARNING: manifest.csv contains 0 songs. Run: python run_pipeline.py --target 300')
    results=[];seen=set()
    for row in rows:
        if field(row,'file') in seen:continue
        seen.add(field(row,'file'))
        try:r=process(row,Path(args.root),out,(args.min_sec,args.max_sec,args.ideal_sec))
        except Exception as e:r={'file':field(row,'file'),'status':'rejected','reason':'unexpected_error:'+str(e)[:80]}
        results.append(r)
        if sum(x.get('status')=='accepted' for x in results)>=args.target:break
    print(json.dumps(write_reports(results,out,args.target),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
