"""Run exact-commit checks and compare new regressions with upstream code."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

CONFIG = json.loads(Path(__file__).with_name('config.json').read_text())
SOURCE = Path(sys.argv[1]).resolve()
EVIDENCE = Path(__file__).resolve().parents[1] / 'evidence'
EVIDENCE.mkdir(exist_ok=True)
ENV = os.environ.copy()
ENV['PYTHONDONTWRITEBYTECODE'] = '1'
if CONFIG['kind'] == 'pge':
    ENV['PYTHONPATH'] = str(SOURCE / 'src')
else:
    ENV.pop('PYTHONPATH', None)
PYTHON = sys.executable


def run(name, args, cwd=SOURCE, expected=0, marker=None, environment=None):
    result = subprocess.run(args, cwd=cwd, env=environment or ENV,
                            capture_output=True, text=True, timeout=240)
    text = result.stdout + result.stderr
    (EVIDENCE / (name + '.log')).write_text(text)
    assert result.returncode == expected, text[-12000:]
    if marker is not None:
        assert marker in text, text[-12000:]
    print(name, 'PASS' if expected == 0 else 'EXPECTED FAILURE', flush=True)
    return text


def clear_bytecode():
    for cache in SOURCE.rglob('__pycache__'):
        shutil.rmtree(cache)


assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() == CONFIG['sha']
clear_bytecode()
for group in CONFIG['groups']:
    cwd = SOURCE / group['cwd']
    name = group['name']
    args = [PYTHON, '-m', 'pytest', '-q'] + group['tests']
    run(name + '-submitted-suite', args, cwd, marker=group['summary'])
    if CONFIG['kind'] == 'pge':
        lint = [PYTHON, '-m', 'flake8'] + group['lint_files']
        lint += ['--output-file', str(EVIDENCE / 'flake8.log')]
        run(name + '-lint', lint, cwd)
    else:
        for label, command in [
            ('black', ['-m', 'black', '--check']),
            ('isort', ['-m', 'isort', '--check-only', '--profile', 'black']),
            ('flake8', ['-m', 'flake8', '--max-line-length=99']),
            ('bandit', ['-m', 'bandit', '-q', '-r']),
        ]:
            run(name + '-' + label, [PYTHON] + command + group['lint_files'], cwd)
    originals = {p: (SOURCE / p).read_bytes() for p in group['source_files']}
    try:
        for p in originals:
            (SOURCE / p).write_bytes(subprocess.check_output(
                ['git', 'show', CONFIG['base'] + ':' + p], cwd=SOURCE))
        clear_bytecode()
        log = run(name + '-original-regressions',
                  [PYTHON, '-m', 'pytest', '-q'] + group['new_tests'], cwd,
                  expected=1, marker=group['baseline_summary'])
        assert 'ERROR collecting' not in log
        assert 'ImportError while importing test module' not in log
    finally:
        for p, content in originals.items():
            (SOURCE / p).write_bytes(content)
        clear_bytecode()
    for p, content in originals.items():
        assert (SOURCE / p).read_bytes() == content
    run(name + '-restored-suite', args, cwd, marker=group['summary'])
    if CONFIG['kind'] == 'orca':
        coverage_env = dict(ENV, COVERAGE_FILE=str(EVIDENCE / (name + '.coverage')))
        run(name + '-coverage-tests', [PYTHON, '-m', 'coverage', 'run', '--branch',
            '--source=' + group['module'], '-m', 'pytest', '-q'] + group['tests'],
            cwd, marker=group['summary'], environment=coverage_env)
        run(name + '-coverage-report', [PYTHON, '-m', 'coverage', 'report', '--fail-under=80'],
            cwd, environment=coverage_env)
        run(name + '-coverage-json', [PYTHON, '-m', 'coverage', 'json', '-o',
            str(EVIDENCE / (name + '-coverage.json'))], cwd, environment=coverage_env)
run('patch-check', ['git', 'diff', '--check', CONFIG['base'] + '...HEAD'])
run('restored-worktree', ['git', 'diff', '--exit-code'])
run('dependency-check', [PYTHON, '-m', 'pip', 'check'])
run('dependency-versions', [PYTHON, '-m', 'pip', 'freeze'])
(EVIDENCE / 'submission.json').write_text(json.dumps(CONFIG, indent=2))
print(CONFIG['final_summary'], flush=True)
