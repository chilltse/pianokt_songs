PianoKT phrase_v5 int64 JSON hotfix

1) Unzip this package. Copy phrase_v5.py to your pianokt_300_auto project folder (make a backup of the old file first).

cd ~/Downloads/pianokt_300_auto
cp phrase_v5.py phrase_v5.py.bak
cp ~/Downloads/pianokt_phrase_v5_fix/phrase_v5.py ./phrase_v5.py
python phrase_v5.py --out output

The script reruns the local segmentation from existing reference.mid files and writes output/phrase_v5_report.json. No new downloads. The older output/accepted files are left unchanged. It may overwrite phrase_v5 segment files with equivalent recomputed outputs.

Cause: numpy.int64 in cut_metrics.crossing_notes is not handled by stock json.dumps. The fix converts NumPy scalar numeric types for both per-piece metadata and aggregate report.
