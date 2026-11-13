#!/usr/bin/env python3
"""One-command orchestration of the existing pop_catalog.py pipeline.
Discovery -> enrichment -> public direct MIDI download -> track inspection ->
guarded import of independently rights-verified candidates.
Existing accepted songs are not overwritten.
"""
import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests
import pop_catalog as pop


def snapshot(path, directory):
    if path.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / path.name
        if not backup.exists():
            shutil.copy2(path, backup)


def stats(rows):
    c = Counter(r.get("status") or "unknown" for r in rows)
    return dict(total=len(rows), statuses=dict(sorted(c.items())),
                direct_midi_urls=sum(bool(r.get("midi_url")) for r in rows),
                downloaded=sum(bool(r.get("local_midi") and Path(r["local_midi"]).is_file()) for r in rows),
                verified_hands=sum(r.get("hand_proof") == "explicit_track_names" for r in rows),
                rights_ready=sum(
                    bool(r.get("composer") and r.get("license") and r.get("license_url"))
                    and r.get("rights_note","").lower() in ("verified-for-research","verified-for-publication")
                    for r in rows))


def run(args):
    catalog = Path(args.catalog)
    backup_dir = Path(args.out) / "pop_oneclick_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    snapshot(catalog, backup_dir)
    print("[1/5] Discovering (and preserving existing catalogue edits)...", flush=True)
    rows = pop.read_csv(catalog)
    if args.provider in ("midishow", "all"):
        rows = pop.add(rows, pop.midishow_seeds(args.query))
    if args.provider in ("tuneonmusic", "all"):
        session = requests.Session()
        session.headers["User-Agent"] = pop.UA
        try:
            found = pop.tuneonmusic_index(session,args.query,args.limit)
            rows = pop.add(rows,found)
            print(f"  {len(found)} Tuneonmusic catalogue candidates")
        except Exception as e:
            print(f"  [WARN] Could not fetch Tuneonmusic catalogue: {e}", file=sys.stderr)
    pop.write_csv(catalog,rows)
    if not rows:
        print("No candidates found. The website may have changed or network may be unavailable.")
        return 2
    print(f"[2/5] Enriching up to {args.enrich} detail pages...",flush=True)
    if args.enrich:
        session=requests.Session()
        session.headers["User-Agent"]=pop.UA
        pop.enrich(session,rows,args.enrich)
        pop.write_csv(catalog,rows)
    print(f"[3/5] Downloading up to {args.download_limit} publicly linked MIDI files...",flush=True)
    if args.download_limit:
        pop.download_rows(rows,args.midi_dir,args.download_limit)
        pop.write_csv(catalog,rows)
    print("[4/5] Inspecting downloaded MIDI hands...",flush=True)
    pop.inspect_rows(rows)
    pop.write_csv(catalog,rows)
    info=stats(rows)
    report_path=Path(args.out)/"pop_oneclick_summary.json"
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[5/5] Importing only candidates with verified hand labels AND documented rights...",flush=True)
    if args.no_ingest:
        print("  Import skipped (--no-ingest).")
    else:
        # A candidate must pass *all* original strict checks to be accepted.
        # 'Verified' is an externally supplied human attestation, never inferred here.
        pop.ingest(rows,args.root,args.out,args.target,args.max_new)
        pop.write_csv(catalog,rows)
    print("\n=== SUMMARY ===")
    print(json.dumps(info,ensure_ascii=False,indent=2))
    print(f"Catalogue: {catalog} | Summary: {report_path}")
    if not info["downloaded"]:
        print("No directly downloadable MIDI found; manually download from listed source_url "
              "and fill local_midi column, then re-run this same command.")
    if not info["rights_ready"]:
        print("No rights-verified rows yet; automatic discovery does not grant rights. "
              "Review composer, license/license_url and rights_note before public import.")
    return 0


def main():
    p=argparse.ArgumentParser(description="One command: discover -> detail -> download -> inspect -> guarded import")
    p.add_argument("--provider",choices=["all","tuneonmusic","midishow"],default="all")
    p.add_argument("--query",default="")
    p.add_argument("--catalog",default="pop_candidates.csv")
    p.add_argument("--limit",type=int,default=500)
    p.add_argument("--enrich",type=int,default=100)
    p.add_argument("--download-limit",type=int,default=200)
    p.add_argument("--midi-dir",default="input/pop")
    p.add_argument("--root",default="input")
    p.add_argument("--out",default="output")
    p.add_argument("--target",type=int,default=300)
    p.add_argument("--max-new",type=int,default=300)
    p.add_argument("--no-ingest",action="store_true")
    args=p.parse_args()
    return run(args)


if __name__=="__main__":
    raise SystemExit(main())
