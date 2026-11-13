import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pretty_midi
from phrase_v5 import segment_bounds
from discover_v5 import score_candidate

class Tests(unittest.TestCase):
    def score(self):
        return r'''\header { 
 title = "Easy piece" 
 composer = "Example Composer"
 mutopiainstrument = "Piano"
 style = "Folk"
 copyright = "Public Domain"
 }
\score { \context PianoStaff <<
 \context Staff = "up" { c''4 d''4 }
 \context Staff = "down" { c4 d4 }
 >> \midi { } }
'''
    def test_real_score_header_evidence(self):
        c=score_candidate(self.score());self.assertEqual(c['license'],'Public Domain');self.assertEqual(c['score_staff_names'],{'up':'R','down':'L'})
    def test_reject_undocumented_rights(self):
        self.assertIsNone(score_candidate(self.score().replace('Public Domain','copyright unknown')))
    def make(self,cut_phrase=True):
        p=pretty_midi.PrettyMIDI(initial_tempo=120)
        left=pretty_midi.Instrument(0,name='Left Hand');right=pretty_midi.Instrument(0,name='Right Hand')
        for bar in range(40):
            # 2 seconds/bar. Every 12 bars makes a 24-second natural pause.
            for i in (0,1,2):
                t=bar*2+i*.5
                if cut_phrase and bar%12==11 and i>=1:continue
                right.notes.append(pretty_midi.Note(90,70,t,t+.30))
                left.notes.append(pretty_midi.Note(70,48,t,t+.42))
        p.instruments=[left,right]
        return p,{'L':left,'R':right}
    def test_phrase_returns_full_coverage(self):
        p,h=self.make();segments,reason=segment_bounds(p,h)
        self.assertIsNone(reason)
        self.assertTrue(segments)
        self.assertTrue(all(20<=b-a<=40 for a,b,_ in segments))
        self.assertTrue(all(not x['abrupt'] for _,_,x in segments[:-1]))
    def test_reject_abrupt_when_only_crossing_options(self):
        p,h=self.make(False)
        for inst in p.instruments:
            for n in inst.notes:n.end=n.start+2.01
        segs,reason=segment_bounds(p,h)
        self.assertEqual(segs,[])
        self.assertIn('no_nonabrupt',reason)

if __name__=='__main__':unittest.main()
