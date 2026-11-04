"""Explicit user-run local Skuld workspace initialization and serving (Linux and macOS).

This is the only launch path. Importing this module creates nothing. Initialization
creates only local access, with no provider, no external accounts, no model budget
and no running worker.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
from skuld.app.runtime import Runtime
from skuld.app.server import create_app
from skuld.sim import analysis as sim_analysis
from skuld.sim.providers import Paraphraser, TemplateProvider
from skuld.sim.run import run_episode
from skuld.sim.world import WorldSpec


def _private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def initialize_workspace(root, name):
    """Run only after the owner explicitly chooses local initialization."""
    if not isinstance(name, str) or not name.strip() or len(name) > 200: raise ValueError('Choose a workspace owner name (1–200 characters).')
    root = Path(root).expanduser().resolve()
    if root.exists() and any(root.iterdir()): raise ValueError('The target directory is not empty. Existing workspaces are never overwritten.')
    root.parent.mkdir(parents=True, exist_ok=True)
    # Stage the whole new workspace. An interrupted or failed initialization must
    # not replace existing data or leave a half-initialized target blocking retry.
    with tempfile.TemporaryDirectory(prefix='.skuld-initializing-', dir=root.parent) as temporary:
        staging = Path(temporary)
        os.chmod(staging, 0o700)
        config = {'gateway_key': secrets.token_hex(32), 'default_route': 'unconfigured', 'routes': {},
                  'simulation_only': True, 'max_model_calls': 0, 'product_provider_enabled': False}
        _private_json(staging / 'config.json', config)
        runtime = Runtime(staging, config)
        try:
            access = runtime.provision(name.strip(), True)
            _private_json(staging / 'access.json', access)
        finally:
            runtime.close()
        # rename(2) refuses to replace a nonempty target if another process
        # created data there after the initial check.
        os.rename(staging, root)
    return access


def make_local_app(root):
    root = Path(root).expanduser().resolve()
    config_path = root / 'config.json'
    if not config_path.is_file() or not (root / 'access.json').is_file():
        raise ValueError('Workspace not initialized. Run the explicit init command first; serve never creates credentials.')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    return create_app(Runtime(root, config), start_worker=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run a private, loopback-only Skuld workspace. No provider calls or worker in the default configuration.')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Explicitly create a new private local workspace and local access credentials. No external account.')
    init.add_argument('--root', required=True, help='Empty directory for your local graph, configuration and access credentials')
    init.add_argument('--name', required=True, help='Name displayed only inside this local workspace')
    serve = commands.add_parser('serve', help='Open an already initialized workspace. Never initializes credentials; worker stays off.')
    serve.add_argument('--root', required=True)
    serve.add_argument('--port', type=int, default=8765)
    sim = commands.add_parser('sim-episode', help='S5: run one simulated episode and append its ledger line. The real route needs DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL.')
    sim.add_argument('--arm', required=True)
    sim.add_argument('--family', required=True)
    sim.add_argument('--absence', required=True)
    sim.add_argument('--seed', type=int, required=True)
    sim.add_argument('--hours', type=float, default=168)
    sim.add_argument('--provider', choices=('real', 'template'), default='real')
    sim.add_argument('--paraphrase-cache', help='JSON cache of the user paraphrases (real provider only)')
    sim.add_argument('--ledger', required=True)
    sim.add_argument('--root', help='keep the episode workspace here (default: a temporary directory)')
    analyze = commands.add_parser('sim-analyze', help='S5: the pre-registered analysis of a ledger (JSON lines).')
    analyze.add_argument('--ledger', required=True)
    analyze.add_argument('--what', choices=('qualify', 'power', 'analyze'), required=True)
    analyze.add_argument('--dev-b', type=float)
    analyze.add_argument('--dev-c', type=float)
    args = parser.parse_args(argv)
    try:
        if args.command == 'sim-episode':
            provider = TemplateProvider() if args.provider == 'template' else None
            words = Paraphraser(args.paraphrase_cache) if args.provider == 'real' and args.paraphrase_cache else None
            line = run_episode(args.arm, WorldSpec(args.family, args.absence, args.seed), provider=provider, hours=args.hours,
                               root=args.root, paraphrase=words)
            with open(args.ledger, 'a', encoding='utf-8') as out:
                out.write(json.dumps(line, ensure_ascii=False) + '\n')
            print(json.dumps({k: line[k] for k in ('arm', 'family', 'absence', 'seed', 'regret', 'calls', 'wall_s')}))
            return
        if args.command == 'sim-analyze':
            rows = [json.loads(l) for l in Path(args.ledger).read_text(encoding='utf-8').splitlines() if l.strip()]
            result = {'qualify': lambda: sim_analysis.qualify(rows), 'power': lambda: sim_analysis.power(rows),
                      'analyze': lambda: sim_analysis.analyze(rows, {'B': args.dev_b, 'C': args.dev_c})}[args.what]()
            print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
            return
        if args.command == 'init':
            initialize_workspace(args.root, args.name)
            root = Path(args.root).expanduser().resolve()
            print('Local workspace initialized: ' + str(root))
            print('No model is configured. Background worker and paid model calls are disabled.')
            print('Open access.json locally for your user ID and access token. Do not share that file.')
            print('Next: python -m skuld.app.local serve --root ' + repr(str(root)))
        else:
            if not 1024 <= args.port <= 65535: raise ValueError('Choose a local port between 1024 and 65535.')
            app = make_local_app(args.root)
            print(f'Local UI: http://127.0.0.1:{args.port}')
            print('Background worker is OFF. Close this process with Ctrl+C. Data stays in your selected workspace.')
            import uvicorn
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__': main()
