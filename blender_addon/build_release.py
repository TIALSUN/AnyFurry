"""Build the current reviewed addon without creating a staging directory."""
import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'anyfurry_mouth'
MODULES = ('__init__.py', 'eye.py', 'mouth.py', 'mouth_opening.py',
           'privacy.py', 'project.py', 'recovery.py', 'runtime.py')
VALIDATION = ('release_record.json', 'source_scan.json', 'results.json',
              'runtime_results.json', 'asset_privacy_verification.json',
              'error_privacy_tests.json')
PROFILE = re.compile(rb'(?i)[a-z]:[\\/]+(?:users|documents and settings)[\\/]+|/home/[^/\s]+/')
CREDENTIAL = re.compile(rb'sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16}|-----BEGIN(?: RSA)? PRIVATE KEY-----')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build():
    record = json.loads((ROOT / 'validation/release_record.json').read_text(encoding='utf8'))
    tree = ast.parse((SOURCE / '__init__.py').read_text(encoding='utf-8-sig'))
    info = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in node.targets))
    version = '.'.join(map(str, info['version']))
    if record['release'] != version:
        raise ValueError('Release validation version does not match source')
    asset_name = record['asset']
    if asset_name != 'assets/mouth_eye_base_v0281.blend':
        raise ValueError('Asset is not on the reviewed release allowlist')
    entries = {}
    for name in (*MODULES, 'README.md', asset_name):
        data = (SOURCE / name).read_bytes()
        expected = record['asset_sha256'] if name == asset_name else record['source_sha256'][name]
        if digest(data) != expected:
            raise ValueError('Validation hashes do not match: ' + name)
        entries['anyfurry_mouth/' + name] = data
    if not entries['anyfurry_mouth/' + asset_name].startswith(b'BLENDER'):
        raise ValueError('Missing binary model; run git lfs pull before building')
    for name in VALIDATION:
        entries['anyfurry_mouth/validation/' + name] = (ROOT / 'validation' / name).read_bytes()
    for key, filename in (('report_sha256', 'security_best_practices_report.md'),
                          ('workflow_sha256', 'RELEASE_WORKFLOW.md')):
        data = (ROOT.parent / filename).read_bytes()
        if digest(data) != record[key]:
            raise ValueError('Validation document hash does not match: ' + filename)
        entries['anyfurry_mouth/' + filename] = data
    for name, data in entries.items():
        # privacy.py intentionally contains regexes, so check its string literals separately.
        if name.endswith('/privacy.py'):
            for node in ast.walk(ast.parse(data.decode('utf8'))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if CREDENTIAL.search(node.value.encode('utf8')):
                        raise ValueError('Credential pattern in ' + name)
            continue
        if PROFILE.search(data) or PROFILE.search(data.replace(b'\\\\', b'\\')):
            raise ValueError('Personal profile path detected in ' + name)
        if CREDENTIAL.search(data):
            raise ValueError('Credential pattern detected in ' + name)
    output_dir = ROOT / 'dist'
    output_dir.mkdir(exist_ok=True)
    output = output_dir / 'AnyFurry_Beta1.zip'
    # A random, exclusively created temporary file prevents clobbering neighbors.
    with tempfile.NamedTemporaryFile(dir=output_dir, suffix='.zip', delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in sorted(entries.items()):
                metadata = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                metadata.compress_type = zipfile.ZIP_DEFLATED
                metadata.external_attr = 0o100644 << 16
                archive.writestr(metadata, data, compresslevel=6)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP integrity check failed')
            for name, data in entries.items():
                if archive.read(name) != data:
                    raise ValueError('ZIP content mismatch: ' + name)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'release': version, 'archive': str(output.relative_to(ROOT.parent)),
                      'bytes': output.stat().st_size, 'files': len(entries),
                      'sha256': digest(output.read_bytes()), 'staging_directory': False}))


if __name__ == '__main__':
    build()
