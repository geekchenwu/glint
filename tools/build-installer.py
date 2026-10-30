#!/usr/bin/python3
"""Create a single-file installer carrying the verified Debian package."""
import base64
import hashlib
from pathlib import Path
import sys

template, package, version, target = sys.argv[1:]
payload = Path(package).read_bytes()
script = Path(template).read_text().replace('@VERSION@', version).replace(
    '@SHA256@', hashlib.sha256(payload).hexdigest())
if not script.endswith('__LUMEN_DEB_PAYLOAD__\n'):
    raise SystemExit('Installer template is missing its payload marker')
output = Path(target)
output.write_bytes(script.encode() + base64.encodebytes(payload))
output.chmod(0o755)
print(output)
