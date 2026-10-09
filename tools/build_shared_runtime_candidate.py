"""Build an unsigned, isolated Shared Media Runtime candidate package."""
from __future__ import annotations
import hashlib, json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "candidate_output" / "shared-media-runtime" / "runtimes" / "media-0.1.0-win-x64-candidate"
SOURCE = Path(r"C:\Users\marcelot\Documents\Codex\MTools-Shared\tools\ffmpeg\ffmpeg-9.0.1-essentials_build\bin")

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def main():
    (OUT/'bin').mkdir(parents=True, exist_ok=True)
    files=[]
    for name in ('ffmpeg.exe','ffprobe.exe'):
        src=SOURCE/name; dst=OUT/'bin'/name
        if not src.is_file(): raise SystemExit(f'missing source: {src}')
        shutil.copy2(src,dst); files.append({'path':f'bin/{name}','size':dst.stat().st_size,'sha256':sha(dst)})
    ffmpeg_version=subprocess.run([str(OUT/'bin'/'ffmpeg.exe'),'-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    ffprobe_version=subprocess.run([str(OUT/'bin'/'ffprobe.exe'),'-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    manifest={'schemaVersion':1,'runtimeContractMajor':1,'runtimeId':'media-0.1.0-win-x64-candidate','packageVersion':'0.1.0','platform':{'os':'windows','arch':'x64'},'upstream':{'ffmpeg':ffmpeg_version,'ffprobe':ffprobe_version},'profile':'candidate-unsigned','files':files,'capabilities':{'features':['ffprobe.json'],'protocols':['file']},'provenance':{'publisher':'MTools candidate','source':'MTools-Shared local artifact','authenticated':False}}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    digest=sha(OUT/'manifest.json')
    catalog={'schemaVersion':1,'catalogVersion':1,'trust':'UNSIGNED_CANDIDATE','runtimes':[{'runtimeId':manifest['runtimeId'],'manifestSha256':digest,'state':'eligible'}]}
    base=OUT.parent.parent
    (base/'catalog.json').write_text(json.dumps(catalog,indent=2),encoding='utf-8')
    (base/'CANDIDATE_LIMITATIONS.md').write_text('# Candidate limitations\n\nUnsigned prototype; hashes are verified, but no authenticated trust chain, lease coordinator, ACL installer, or revocation service is included.\n',encoding='utf-8')
    print(OUT)
if __name__=='__main__': main()
