import csv,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discover_v5 import pdmx_shortlist

class TestPDMX(unittest.TestCase):
    def test_shortlist_filters_rights_and_genres(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            fields=['license_conflict','has_paywall','mid','mxl','composer_name','title','license','license_url','tracks','complexity','song_length.seconds','n_tracks','genres','n_favorites','subset:deduplicated']
            good=dict(license_conflict='False',has_paywall='False',mid='x.mid',mxl='x.mxl',composer_name='C',title='Jazz Waltz',license='publicdomain',license_url='https://creativecommons.org/publicdomain/mark/1.0/',tracks='Piano',complexity='1.2',**{'song_length.seconds':'94'},n_tracks='1',genres='Jazz',n_favorites='12',**{'subset:deduplicated':'True'})
            with (d/'PDMX.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fields);w.writeheader();w.writerow(good);w.writerow({**good,'title':'Rights conflict','license_conflict':'True'});w.writerow({**good,'title':'Not piano','tracks':'Flute'})
            pdmx_shortlist(d/'PDMX.csv',d/'out')
            with (d/'out/v5_discovery/pdmx_candidate_shortlist.csv').open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['genre'],'Jazz')

if __name__=='__main__':unittest.main()
