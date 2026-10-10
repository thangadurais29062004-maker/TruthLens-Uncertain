"""Re-download the pinned public model with SHA-256 validation."""
import hashlib
import json
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parent/'models'
config=json.loads((root/'onnx.json').read_text(encoding='utf-8'))
target=root/config['model_path']; temporary=target.with_suffix('.part')
url=f"{config['source']}/resolve/{config['revision']}/onnx/model_quantized.onnx"
try:
    digest=hashlib.sha256(); total=0
    with urllib.request.urlopen(url,timeout=30) as source, temporary.open('wb') as destination:
        while block:=source.read(1024*1024):
            total+=len(block)
            if total>150*1024*1024: raise ValueError('Model exceeds download limit')
            digest.update(block); destination.write(block)
    if digest.hexdigest()!=config['sha256']: raise ValueError('Model checksum mismatch; existing model was preserved')
    temporary.replace(target)
    print('Pinned detector installed and verified.')
finally:
    temporary.unlink(missing_ok=True)
