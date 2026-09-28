"""Verify upgrade from an unpacked v0.1 baseline on disposable data only."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def snapshot(path):
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence') ORDER BY name")]
        return {name: {'columns': [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')],
                       'rows': sorted(list(db.execute(f'SELECT * FROM "{name}"')), key=repr)} for name in tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Unmodified unpacked ai-pulse v0.1.0 root')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not (args.baseline / 'backend/migrations/versions/0001_initial.py').exists():
        # Version filenames may vary; check the migration directory and CLI instead.
        if not (args.baseline / 'backend/app/cli.py').exists():
            raise SystemExit('A real, unpacked baseline is required')
    report = {'database': 'temporary SQLite only', 'commands': []}
    with tempfile.TemporaryDirectory(prefix='pulse-migration-') as tmp:
        path = Path(tmp) / 'upgrade.db'
        env = {**os.environ, 'DATABASE_URL': f'sqlite:///{path}', 'DATA_MODE': 'replay',
               'LLM_MODE': 'extractive', 'TASK_MODE': 'local', 'DELIVERY_TRANSPORT': 'file'}
        def run(root, *command):
            result = subprocess.run([sys.executable, *command], cwd=root / 'backend', env=env,
                                    capture_output=True, text=True, timeout=30)
            report['commands'].append({'codebase': 'baseline' if root == args.baseline else 'v0.2.0',
                'command': 'python ' + ' '.join(command), 'returncode': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr})
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
        run(args.baseline, '-m', 'alembic', 'upgrade', 'head')
        run(args.baseline, '-m', 'app.cli', 'demo')
        run(args.baseline, '-m', 'app.cli', 'brief')
        before = snapshot(path)
        assert before['articles']['rows'] and before['runs']['rows']
        run(ROOT, '-m', 'alembic', 'upgrade', 'head')
        with sqlite3.connect(path) as db:
            for name, data in before.items():
                columns = ','.join(f'"{c}"' for c in data['columns'])
                after = sorted(list(db.execute(f'SELECT {columns} FROM "{name}"')), key=repr)
                assert after == data['rows'], f'Original values changed in {name}'
            assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0002'
            assert db.execute('SELECT engine, attempts, checkpoint FROM runs LIMIT 1').fetchone() == ('local', 0, '{}')
        run(ROOT, '-m', 'alembic', 'check')
        # This downgrade is destructive to new tables and is ONLY done on this scratch copy.
        run(ROOT, '-m', 'alembic', 'downgrade', '0001')
        run(ROOT, '-m', 'alembic', 'upgrade', 'head')
        run(ROOT, '-m', 'alembic', 'check')
        report['preserved_rows'] = {name: len(data['rows']) for name, data in before.items()}
        report['original_values_preserved'] = True
        report['upgrade_downgrade_reupgrade'] = True
    report['passed'] = True
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)

if __name__ == '__main__':
    main()
