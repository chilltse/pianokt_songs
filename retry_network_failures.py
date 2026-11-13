#!/usr/bin/env python3
"""Requeue only transient network failures from PianoKT 300 v3 checkpoint.

Run from project root: python retry_network_failures.py --out output
Does not modify accepted/ or input/. Makes timestamped backups of audit+manifest.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

NETWORK_SIGNATURES = (
    'HTTPSConnectionPool(', 'HTTPConnectionPool(',
    'Max retries exceeded', 'NameResolutionError', 'Temporary failure in name resolution',
    'ConnectionError', 'ConnectTimeout', 'ReadTimeout', 'Read timed out',
    'Connection reset', 'Connection aborted', 'RemoteDisconnected',
    'SSLError', 'ProxyError', 'Network is unreachable',
)


def is_transient(record):
    reason = str(record.get('reason') or '')
    return record.get('status') == 'rejected' and any(x.lower() in reason.lower() for x in NETWORK_SIGNATURES)


def atomic_write(path: Path, content: str):
    fd, temp_path = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='output', help='The same --out as used with run_pipeline.py')
    parser.add_argument('--dry-run', action='store_true', help='Inspect only; make no changes')
    args = parser.parse_args()
    out = Path(args.out)
    audit_file, manifest_file = out / 'audit.json', out / 'manifest.csv'
    if not audit_file.is_file() or not manifest_file.is_file():
        parser.error('Expected existing output/audit.json AND output/manifest.csv. Run from the project root or set --out.')

    data = json.loads(audit_file.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        parser.error('Unexpected audit.json format: expected list')
    with manifest_file.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        rows = list(reader)
    if not columns or 'piece_id' not in columns:
        parser.error('manifest.csv must have piece_id column; refusing changes')
    affected = [r for r in data if is_transient(r)]
    ids = {str(r.get('piece_id') or '') for r in affected}
    if not ids or '' in ids:
        if '' in ids:
            parser.error('Network failure record missing piece_id; refusing changes')
        print(f'No transient network failures to retry. Existing records={len(data)}. Nothing changed.')
        return
    if any(r.get('status') == 'accepted' and str(r.get('piece_id')) in ids for r in data):
        parser.error('Retry IDs collide with accepted records; refusing changes')
    retained = [r for r in data if str(r.get('piece_id') or '') not in ids]
    retained_rows = [r for r in rows if str(r.get('piece_id') or '') not in ids]
    kept_accepted = sum(r.get('status') == 'accepted' for r in retained)
    print(f'Previous audit records: {len(data)}')
    print(f'Accepted songs preserved: {kept_accepted}')
    print(f'Requeue network failures: {len(affected)} records, {len(ids)} distinct piece IDs')
    print(f'Unchanged prior records: {len(retained)}')
    print(f'Manifest rows after removal: {len(retained_rows)}')
    if args.dry_run:
        print('DRY RUN: nothing changed.')
        return
    backup_dir = out / 'backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if backup_dir.exists():
        parser.error(f'Backup folder already exists: {backup_dir}; retry later')
    backup_dir.mkdir(parents=True)
    shutil.copy2(audit_file, backup_dir / 'audit.json')
    shutil.copy2(manifest_file, backup_dir / 'manifest.csv')
    new_audit = json.dumps(retained, ensure_ascii=False, indent=2)
    mem = io.StringIO(newline='')
    writer = csv.DictWriter(mem, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(retained_rows)
    atomic_write(audit_file, new_audit)
    atomic_write(manifest_file, mem.getvalue())
    print(f'Checkpoint edited safely. Backups: {backup_dir}')
    print('Next: python run_pipeline.py --target 300 --max-pages 240 --max-attempts 1500 --resume')

if __name__ == '__main__':
    main()
