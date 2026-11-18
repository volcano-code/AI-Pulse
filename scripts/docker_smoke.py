"""CI-only Docker smoke on fresh project-scoped replay volumes, never live data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', choices=['sqlite', 'postgres'], required=True)
    args = parser.parse_args()
    # This harness tears down its volumes. Refuse ordinary local environments.
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('Requires an ephemeral GitHub-hosted Actions runner; do not run against local Docker.')
    project = 'pulse-ci-' + uuid4().hex[:16]
    token = uuid4().hex
    env = {**os.environ, 'ADMIN_TOKEN': token, 'POSTGRES_PASSWORD': uuid4().hex}
    # No root .env, external credentials, providers or outgoing mail enter runtime.
    runtime = {'DATA_MODE': 'replay', 'LLM_MODE': 'extractive', 'LLM_API_KEY': '',
               'EMBEDDING_API_KEY': '', 'EMBEDDING_QUERY_ALLOW_PAID': 'false',
               'RETRIEVAL_MODE': 'lexical', 'DELIVERY_TRANSPORT': 'file',
               'SMTP_HOST': '', 'SMTP_USERNAME': '', 'SMTP_PASSWORD': '',
               'OUTBOX_DIR': '/app/data/outbox/replay', 'TIMEZONE': 'UTC'}
    if args.database == 'sqlite':
        runtime['DATABASE_URL'] = 'sqlite:////app/data/replay.db'
    with tempfile.TemporaryDirectory(prefix=project) as tmp:
        empty_env = Path(tmp) / '.env'
        empty_env.write_text('')
        override = Path(tmp) / 'override.json'
        override.write_text(json.dumps({'services': {'api': {'environment': runtime},
                                                    'worker': {'environment': runtime}},
                                        'networks': {'default': {'internal': True}}}))
        base = ['docker', 'compose', '--env-file', str(empty_env), '-p', project,
                '-f', str(ROOT / 'compose.yaml')]
        if args.database == 'postgres':
            base += ['-f', str(ROOT / 'compose.postgres.yaml')]
        base += ['-f', str(override)]

        def compose(*cmd, check=True):
            return subprocess.run(base + list(cmd), cwd=ROOT, env=env, check=check, timeout=900)

        def request(path, port=8000):
            # Docker internal networks do not publish host ports on all engines.
            # Probe real HTTP from API container across the private Compose network.
            host = 'web' if port == 3000 else 'api'
            probe = (
                "import os,sys,urllib.request; "
                "req=urllib.request.Request(sys.argv[1], headers={'Authorization': "
                "'Bearer '+os.environ['ADMIN_TOKEN']}); "
                "sys.stdout.buffer.write(urllib.request.urlopen(req,timeout=5).read())"
            )
            result = subprocess.run(base + ['exec', '-T', 'api', 'python', '-c', probe,
                                           f'http://{host}:{port}{path}'],
                                    cwd=ROOT, env=env, capture_output=True, timeout=15)
            if result.returncode:
                raise OSError('Container HTTP probe failed: ' + result.stderr.decode(errors='replace')[-500:])
            return result.stdout

        def ready(previous_workers=frozenset()):
            deadline = time.monotonic() + 90
            last_error = None
            while time.monotonic() < deadline:
                try:
                    status = json.loads(request('/api/v1/status', 3000))
                    automation = json.loads(request('/api/v1/automation', 3000))
                    assert status['data_mode'] == 'replay'
                    assert status['generation_mode'] == 'extractive'
                    assert automation['worker_online'] and not automation['schedule']['enabled']
                    workers = {w['id'] for w in automation['workers']}
                    assert workers - previous_workers, 'No fresh worker heartbeat after restart'
                    assert automation['delivery_transport'] == 'file'
                    assert b'<html' in request('/', 3000).lower()
                    return status, workers
                except (OSError, ValueError, AssertionError) as exc:
                    last_error = repr(exc)
                    time.sleep(2)
            raise RuntimeError('Readiness failed: ' + str(last_error))

        try:
            compose('config', '--quiet')
            compose('build')
            # Seed offline before long-lived API/Worker start, using migrated throwaway data.
            compose('run', '--rm', 'api', 'sh', '-c',
                    'alembic upgrade head && python -m app.cli demo')
            compose('up', '-d', '--wait', '--wait-timeout', '120')
            first, workers = ready()
            assert first['articles'] > 0 and first['snapshots'] > 0 and first['briefs'] > 0
            compose('restart', 'api', 'worker', 'web')
            second, _ = ready(workers)
            for field in ('articles', 'snapshots', 'events', 'briefs'):
                assert first[field] == second[field], f'Restart changed {field}'
            print(json.dumps({'database': args.database, 'docker_build': 'passed',
                              'api_web_proxy_worker': 'passed', 'restart_counts': 'preserved',
                              'scope': 'ephemeral replay only; not production acceptance'}))
        except Exception:
            compose('logs', '--tail', '60', 'api', 'worker', 'web', check=False)
            raise
        finally:
            # Exact random project selected above, never the user's ai-pulse project.
            compose('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
