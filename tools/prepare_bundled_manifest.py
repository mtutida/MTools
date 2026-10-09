"""Create the candidate's local manifest after PyInstaller copies binaries."""
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "dist" / "CompactMe" / "_internal" / "bin"

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def main():
    files=[]
    for name in ('ffmpeg.exe','ffprobe.exe'):
        p=BIN/name
        if not p.is_file(): raise SystemExit(f'missing bundled binary: {p}')
        files.append({'path':f'bin/{name}','size':p.stat().st_size,'sha256':digest(p)})
    manifest={'schemaVersion':1,'runtimeContractMajor':1,'runtimeId':'compactme-bundled-candidate','packageVersion':'0.1.0','platform':{'os':'windows','arch':'x64'},'profile':'bundled-full-candidate','files':files,'capabilities':{'features':['ffprobe.json'],'protocols':['file']},'provenance':{'publisher':'MTools candidate','authenticated':False}}
    (BIN.parent/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(BIN.parent/'manifest.json')
if __name__=='__main__': main()
