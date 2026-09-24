import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def fingerprint(path):
    p=Path(path).resolve();s=p.stat()
    return f'{p}:{s.st_size}:{s.st_mtime_ns}'

def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

_tool_config = {}

def configure_tools(config):
    global _tool_config
    _tool_config = {'tools': dict(config.get('tools', {}))}

def run(command,timeout=120):
    from script_config import tool
    command = list(command)
    if str(command[0]) in _tool_config.get('tools', {}):
        command[0] = tool(_tool_config, str(command[0]))
    p=subprocess.Popen([str(x) for x in command],stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:out,err=p.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired,KeyboardInterrupt):
        os.killpg(p.pid,signal.SIGTERM)
        try:p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.communicate()
        raise
    if p.returncode:raise RuntimeError(f'{Path(str(command[0])).name} exited {p.returncode}: {err[-600:]}')
    return out

def backup(path,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    sha=digest(path);dest=root/(sha+'.original')
    if not dest.exists():
        with Path(path).open('rb') as source,dest.open('xb') as target:
            shutil.copyfileobj(source,target);target.flush();os.fsync(target.fileno())
    if digest(dest)!=sha:raise ValueError('Backup checksum mismatch')
    return dest,sha

def install(source,target,root,expected,video,video_fp):
    target=Path(target)
    if target.is_symlink():raise ValueError('Symlink destination refused')
    if fingerprint(video)!=video_fp:raise ValueError('Video changed during verification')
    current=digest(target) if target.exists() else None
    if current!=expected:raise ValueError('Subtitle changed during verification')
    saved,original=(backup(target,root) if target.exists() else (None,None))
    new=digest(source)
    receipt=dict(target=str(target),backup=str(saved) if saved else None,
                 original_sha256=original,installed_sha256=new,status='PREPARED')
    journal=Path(root)/(hashlib.sha256(str(target).encode()).hexdigest()+'.receipt.json')
    atomic_json(journal,receipt)
    fd,name=tempfile.mkstemp(prefix='.'+target.stem+'.',suffix='.staging',dir=target.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'wb') as f,Path(source).open('rb') as source_file:
            shutil.copyfileobj(source_file,f);f.flush();os.fsync(f.fileno())
        os.chmod(temp,target.stat().st_mode&0o777 if target.exists() else 0o644)
        if (digest(target) if target.exists() else None)!=expected or fingerprint(video)!=video_fp:
            raise ValueError('Live files changed before commit')
        if digest(temp)!=new:raise ValueError('Staged checksum mismatch')
        os.replace(temp,target)
        if digest(target)!=new:raise ValueError('Installed checksum mismatch')
        receipt['status']='COMMITTED';atomic_json(journal,receipt)
        return receipt
    finally:
        if temp.exists():temp.unlink()
