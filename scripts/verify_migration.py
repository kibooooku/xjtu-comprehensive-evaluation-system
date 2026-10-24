"""Read-only check that the pre-V2 backup's applications and rounds still exist."""
import argparse
import json
import sqlite3
from pathlib import Path


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--storage', type=Path, default=Path('instance'))
    args = parser.parse_args()
    backups = sorted((args.storage / 'backups').glob('pre-v2-*.db'))
    if not backups:
        print('No pre-V2 data backup exists (fresh installations do not need one).')
        return
    with readonly(backups[-1]) as old, readonly(args.storage / 'evaluation.db') as current:
        application_ids = [r[0] for r in old.execute('select id from applications')]
        round_ids = [r[0] for r in old.execute('select id from submission_rounds')]
        report = {'backup_count': len(backups), 'original_applications': len(application_ids),
                  'applications_preserved': all(current.execute('select 1 from applications where id=?', (i,)).fetchone() for i in application_ids),
                  'rounds_preserved': all(current.execute('select 1 from submission_rounds where id=?', (i,)).fetchone() for i in round_ids),
                  'foreign_keys_ok': not current.execute('pragma foreign_key_check').fetchall(),
                  'active_jobs': current.execute("select count(*) from background_jobs where state in ('QUEUED','RUNNING')").fetchone()[0]}
        print(json.dumps(report))
        if not all(report[k] for k in ('applications_preserved', 'rounds_preserved', 'foreign_keys_ok')):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
