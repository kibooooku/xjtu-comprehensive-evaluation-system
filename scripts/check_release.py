"""Validate the staged NEW tree, without inspecting remote/old source or secrets."""
from pathlib import Path
import re
import subprocess
import sys


def git(*args):
    return subprocess.check_output(['git', *args])


def main():
    paths = [p for p in git('diff', '--cached', '--name-only', '-z').decode('utf-8').split('\0') if p]
    forbidden = ('secrets/', 'instance/', '.venv/', '.test-data/', '.tools/', 'RBAC项目示例labreserve/', '某同学德育分有效文件示例/')
    key_pattern = re.compile(rb'(?:AKID[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
    issues = []
    total_bytes = 0
    for path in paths:
        if path.startswith(forbidden) or path == '.env' or (path.endswith(('.db', '.db-wal', '.db-shm', '.key', '.pem'))):
            issues.append((path, 'private runtime/source file'))
            continue
        payload = git('show', ':' + path)
        total_bytes += len(payload)
        if len(payload) > 90 * 1024 * 1024:
            issues.append((path, 'file exceeds release size limit'))
        if b'\0' not in payload[:8192] and key_pattern.search(payload):
            issues.append((path, 'possible credential (value intentionally hidden)'))
    for document in ('README.md', 'docs/acceptance-report.md'):
        text = Path(document).read_text(encoding='utf-8')
        for link in re.findall(r'\]\(([^)]+)\)', text):
            if '://' in link or link.startswith(('#', 'mailto:')):
                continue
            target = Path(document).parent / link.split('#')[0]
            if not target.exists():
                issues.append((document, 'missing linked file: ' + link))
    if issues:
        for path, reason in issues:
            print(path + ': ' + reason)
        return 1
    print(f'Release tree passed: {len(paths)} staged files, {total_bytes:,} bytes; no credential/runtime paths; document links valid.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
