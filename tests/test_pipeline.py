import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pretty_midi
from catalog_source import parse_listing,parse_detail,score_staff_proof,parse_ly_header
from build import process,segment_bounds

SCORE=r'''
\header {
 title = "l'Arabesque"
 composer = "Johann Friedrich Franz Burgmüller (1806-1874)"
 copyright = "Public Domain"
}
\score { {
\context PianoStaff <<
  \context Staff = "up" << \Global \clef treble \context Voice = "V1" \MD >>
  \context Staff = "down" << \Global \clef bass \context Voice = "V2" \MS >>
>>
}
\layout {}
\midi { \tempo 4 = 120 }
}
'''

DETAIL='''<html><body><h2>l'Arabesque</h2><h4>by J. F. F. Burgmüller (1806–1874)</h4>
<p>Instrument(s): Piano | Style: Romantic</p>
<p>Opus: Opus 100. | Date of composition: 19th Century</p>
<p>Source: Collection Litolff, 19th Century | Copyright: <a href="/license.html">Public Domain</a></p>
<p>Last updated: 2013/Jan/12 | Music ID Number: Mutopia-2013/01/12-203</p>
<p>Download: <a href="/ftp/BurgmullerJFF/O100/25EF-02/25EF-02.ly">LilyPond file</a> | <a href="/ftp/BurgmullerJFF/O100/25EF-02/25EF-02.mid">MIDI file</a></p>
<p>Maintainer: Bas Wassink | basvanlola (at) zonnet.com</p></body></html>'''

def sample_pm():
    pm=pretty_midi.PrettyMIDI(initial_tempo=120)
    for hand,base in [('up',72),('down',48)]:
        ins=pretty_midi.Instrument(program=0,name=hand)
        for beat in range(120):
            start=beat*.5
            ins.notes.append(pretty_midi.Note(velocity=80,pitch=base+beat%4,start=start,end=start+.33))
        pm.instruments.append(ins)
    return pm

def test_listing():
    ids,nxt=parse_listing('<a href="piece-info.cgi?id=203">More Information</a><a href="make-table.cgi?Instrument=Piano&startat=10">Next 10</a>')
    assert ids==[203] and nxt==10

def test_detail_and_score():
    x=parse_detail(DETAIL,'https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=203')
    assert x['title']=="l'Arabesque" and x['composer']=='J. F. F. Burgmüller'
    assert x['genre']=='Romantic' and x['license']=='Public Domain'
    assert x['midi_url'].endswith('25EF-02.mid') and x['attribution'].startswith('Typeset by')
    assert score_staff_proof(SCORE)=={'up':'R','down':'L'}
    assert parse_ly_header(SCORE)['composer'].startswith('Johann Friedrich')

def test_pipeline_build(tmp_path):
    input=tmp_path/'input';out=tmp_path/'output';input.mkdir()
    pm=sample_pm();pm.write(str(input/'203.mid'))
    (input/'203.ly').write_text(SCORE,encoding='utf-8')
    row={'file':'203.mid','score_file':'203.ly','title':"l'Arabesque",'composer':'Johann Friedrich Franz Burgmüller',
         'genre':'Romantic','license':'Public Domain','license_url':'https://www.mutopiaproject.org/license.html',
         'source_url':'https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=203','hand_source':'score_staff_verified','piece_id':'203'}
    x=process(row,input,out)
    assert x['status']=='accepted',x
    assert x['segments']
    assert all(20 <= s['duration_sec'] <=40 for s in x['segments'])
    assert all((out/s['midi_path']).exists() for s in x['segments'])
    seg=pretty_midi.PrettyMIDI(str(out/x['segments'][0]['midi_path']))
    assert len(seg.instruments)==2
    assert set(p.name for p in seg.instruments)=={'Left Hand','Right Hand'}

def test_fail_closed(tmp_path):
    input=tmp_path/'input';out=tmp_path/'output';input.mkdir()
    pm=sample_pm();pm.instruments[0].name='Piano';pm.instruments[1].name='Piano'
    pm.write(str(input/'203.mid'))
    (input/'203.ly').write_text(SCORE,encoding='utf-8')
    row={'file':'203.mid','score_file':'203.ly','title':'Arabesque','composer':'Burgmuller','genre':'Romantic',
         'license':'Public Domain','license_url':'https://www.mutopiaproject.org/license.html',
         'source_url':'https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=203','hand_source':'score_staff_verified'}
    x=process(row,input,out)
    assert x['status']=='rejected' and x['reason']=='midi_track_names_do_not_prove_hands'

def test_full_automatic_pipeline_with_mocked_official_source(tmp_path,monkeypatch):
    # This proves discover + detail + .ly + .mid + manifest + segment export
    # without implying the remote server was fetched in this environment.
    import argparse
    import run_pipeline
    pm=sample_pm();src=tmp_path/'fixture.mid';pm.write(str(src))
    midi=src.read_bytes()
    listing=b'<html><a href="piece-info.cgi?id=203">More Information</a></html>'
    class FakeFetcher:
        def __init__(self,*a,**kw):pass
        def get(self,url,max_bytes=None):
            if 'make-table.cgi' in url:return listing
            if 'piece-info.cgi' in url:return DETAIL.encode()
            if url.endswith('.ly'):return SCORE.encode()
            if url.endswith('.mid'):return midi
            raise AssertionError(url)
    monkeypatch.setattr(run_pipeline,'Fetcher',FakeFetcher)
    args=argparse.Namespace(target=1,root=str(tmp_path/'input'),out=str(tmp_path/'out'),
        max_pages=1,max_attempts=5,max_per_composer=25,max_per_genre=100,min_sec=20,max_sec=40,ideal_sec=30,
        delay=0.0,timeout=1,retries=0,seed=3407,checkpoint_every=1,resume=True)
    assert run_pipeline.run(args)==0
    summary=__import__('json').loads((tmp_path/'out'/'summary.json').read_text())
    assert summary['accepted']==1 and summary['target_reached']
    with (tmp_path/'out'/'manifest.csv').open() as f:
        rows=list(__import__('csv').DictReader(f))
    assert len(rows)==1 and rows[0]['title']=="l'Arabesque"
    assert rows[0]['composer']=='Johann Friedrich Franz Burgmüller'
    assert rows[0]['license']=='Public Domain' and rows[0]['piece_id']=='203'
