"""Generate current UML/ER in an isolated Visual Paradigm profile and seed copy."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vp', type=Path, default=Path('D:/Program Files 2/Visual Paradigm 18.1'))
    parser.add_argument('--javac', type=Path, default=Path('C:/Program Files/Java/jdk-11/bin/javac.exe'))
    parser.add_argument('--manifest', type=Path, default=root / '系统设计/源文件/当前版本/design-model.xml')
    parser.add_argument('--output', type=Path, default=root / '.tools/design/native-current')
    parser.add_argument('--limit', type=int, default=9999)
    parser.add_argument('--timeout', type=int, default=480)
    args = parser.parse_args()
    cache = root / '.tools/design'
    output = args.output.resolve()
    if not output.is_relative_to(root) or output == root:
        parser.error('Output must be a dedicated directory in this project workspace.')
    manifest = args.manifest.resolve()
    model = ET.parse(manifest).getroot()
    if any(item.get('kind') != 'class' for item in model.findall('diagram')):
        parser.error('Only class diagrams and schema ER views are supported; no DFD input.')
    classes = cache / 'current-plugin/classes'
    classes.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(args.javac), '-encoding', 'UTF-8', '--release', '11',
        '-cp', str(args.vp / 'lib/openapi.jar'), '-d', str(classes),
        str(Path(__file__).with_name('CurrentDesignPlugin.java')),
    ], check=True)
    profile = cache / 'vp-profile'
    plugin = profile / 'VisualParadigm/plugins/deyu.design.current.v2'
    plugin.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).with_name('plugin-current.xml'), plugin / 'plugin.xml')
    shutil.copytree(classes, plugin / 'classes', dirs_exist_ok=True)
    original_seed = args.vp / 'resources/BaggageSchemas.vpp'
    original_digest = hashlib.sha256(original_seed.read_bytes()).hexdigest()
    seed = cache / 'native-current-seed.vpp'
    shutil.copy2(original_seed, seed)
    if hashlib.sha256(seed.read_bytes()).hexdigest() != original_digest:
        raise RuntimeError('Seed copy verification failed.')
    command = [
        str(args.vp / 'jre/bin/java.exe'), '-Xmx1024m', '-Dfile.encoding=UTF-8',
        f'-Dvp.user.home={profile}', '-Djava.awt.headless=false',
        '-Ddeyu.seed.copy.confirmed=true', f'-Ddeyu.manifest={manifest}',
        f'-Ddeyu.output={output}', f'-Ddeyu.limit={args.limit}',
        '-cp', f'{args.vp / "bin"};{args.vp / "lib/*"};{args.vp / "ormlib/*"}',
        'com.vp.cmd.Plugin', '-project', str(seed), '-upgrade-project',
        '-pluginid', 'deyu.design.current.v2',
    ]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    with (cache / 'vp-current.stdout.txt').open('w', encoding='utf-8') as stdout, (cache / 'vp-current.stderr.txt').open('w', encoding='utf-8') as stderr:
        process = subprocess.Popen(command, cwd=args.vp / 'bin', stdout=stdout, stderr=stderr,
                                   startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
        print(f'VP current UML/ER process started: {process.pid}', flush=True)
        try:
            returncode = process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=15)
            raise RuntimeError('Isolated VP generation timed out; only the task process was stopped.') from None
    if hashlib.sha256(original_seed.read_bytes()).hexdigest() != original_digest:
        raise RuntimeError('The installed seed unexpectedly changed.')
    project = output / 'VisualParadigm/德育分统计系统-设计评审.vpp'
    report = output / '源文件/native-generation-report.txt'
    error = output / 'native-generation-error.txt'
    if returncode or error.exists() or not project.exists() or not report.exists():
        raise RuntimeError('VP did not finish the current project; inspect only this task cache output.')
    lines = report.read_text(encoding='utf-8').splitlines()
    print(f'VP_CURRENT_OK diagrams={len(lines)} project_bytes={project.stat().st_size}', flush=True)
    audit = output / '源文件/native-schema-audit.txt'
    if audit.exists():
        for line in audit.read_text(encoding='utf-8').splitlines():
            if line.startswith('TOTAL '):
                print(line, flush=True)


if __name__ == '__main__':
    main()
