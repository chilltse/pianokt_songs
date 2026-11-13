import unittest, tempfile
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
import pop_catalog as p

class Tests(unittest.TestCase):
    def test_discover(self):
        html='''<a href="/music-sheets/artists/taylor-swift/love-story/love-story-beginner/">Love Story - Beginner Version Beginner · Taylor Swift ·03:50</a><a href="/music-sheets/classical/test-advanced/">Test - Advanced Version Advanced</a>'''
        with patch.object(p,'get_html',return_value=BeautifulSoup(html,'html.parser')):
            songs=p.tuneonmusic_index(None,limit=40)
        self.assertEqual(len(songs),1)
        self.assertEqual(songs[0]['difficulty_label'],'Beginner')
        self.assertEqual(songs[0]['title'],'Love Story')
    def test_midi_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            import pretty_midi
            pm=pretty_midi.PrettyMIDI()
            for name,pitch in [('Left Hand',48),('Right Hand',72)]:
                inst=pretty_midi.Instrument(0,name=name)
                inst.notes.append(pretty_midi.Note(90,pitch,0,1))
                pm.instruments.append(inst)
            loc=Path(temp)/'file.mid';pm.write(str(loc))
            rows=[{'local_midi':str(loc),'title':'Test'}]
            p.inspect_rows(rows)
            self.assertEqual(rows[0]['hand_proof'],'explicit_track_names')
    def test_seed_no_composer_fabrication(self):
        first=p.midishow_seeds()[0]
        self.assertEqual(first['composer'],'')
        self.assertEqual(first['status'],'discovered')
    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            loc=Path(temp)/'c.csv';p.write_csv(loc,p.midishow_seeds())
            self.assertEqual(len(p.read_csv(loc)),10)

if __name__=='__main__':unittest.main()
