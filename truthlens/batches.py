"""Server-owned batches. Staged media is temporary and never served as an asset."""
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

LOCK = threading.RLock()
TERMINAL = {'complete', 'failed', 'cancelled'}
THREADS = []
MAINTENANCE_STOP = threading.Event()


def start_maintenance(s):
    def maintain():
        while not MAINTENANCE_STOP.wait(30):
            try:
                with LOCK: expire_uploads(s)
            except Exception: s.LOG.exception('Expired batch upload cleanup failed')
    MAINTENANCE_STOP.clear()
    thread=threading.Thread(target=maintain,daemon=True)
    THREADS.append(thread); thread.start()


def shutdown(s):
    MAINTENANCE_STOP.set()
    with LOCK:
        with s.database() as db:
            rows=db.execute("SELECT id FROM batches WHERE status='processing'").fetchall()
        for (batch_id,) in rows:
            batch=read(s,batch_id); batch['status']='cancelled'; write(s,batch)
            with s.JOB_LOCK:
                for item in batch['items']:
                    event=s.CANCELLATIONS.get(item.get('id'))
                    if event: event.set()
    for thread in THREADS: thread.join(timeout=190)


def read(s, batch_id, owner=None):
    with s.database() as db:
        row = db.execute('SELECT owner,payload FROM batches WHERE id=?', (batch_id,)).fetchone()
    return json.loads(row[1]) if row and (owner is None or row[0] == owner) else None


def write(s, batch):
    with s.database() as db:
        db.execute('UPDATE batches SET status=?,payload=? WHERE id=?',
                   (batch['status'], json.dumps(batch), batch['id']))


def directory(s, batch_id):
    if not re.fullmatch('[a-f0-9]{32}', batch_id):
        raise ValueError('Invalid batch ID.')
    return s.DATA / 'batch-media' / batch_id


def clean(s, batch_id):
    target = directory(s, batch_id).resolve()
    parent = (s.DATA / 'batch-media').resolve()
    if target.parent == parent and target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)


def recover(s):
    """A server restart interrupts jobs; do not silently resubmit a partial batch."""
    with LOCK:
        with s.database() as db:
            rows = db.execute("SELECT id,owner FROM batches WHERE status NOT IN ('complete','failed','cancelled')").fetchall()
        for batch_id, owner in rows:
            batch = read(s, batch_id, owner)
            batch.update(status='failed', error='Server restarted. Completed reports remain in your library; resubmit unfinished files.')
            write(s, batch)
            clean(s, batch_id)
        media_root=s.DATA/'batch-media'
        if media_root.is_dir():
            for leftover in media_root.iterdir():
                if re.fullmatch('[a-f0-9]{32}',leftover.name): clean(s,leftover.name)


def expire_uploads(s):
    with s.database() as db:
        rows = db.execute("SELECT id,owner FROM batches WHERE status='uploading' AND created<?", (time.time()-900,)).fetchall()
    for batch_id, owner in rows:
        batch = read(s, batch_id, owner)
        batch.update(status='failed', error='Upload expired after 15 minutes. Choose your files again.')
        write(s, batch)
        clean(s, batch_id)


def snapshot(s, batch):
    result = dict(batch)
    result['items'] = [dict(item) for item in batch['items']]
    with s.database() as db:
        for item in result['items']:
            if item.get('id'):
                row = db.execute('SELECT status,payload FROM reports WHERE id=? AND owner=?', (item['id'],batch['owner'])).fetchone()
                if row:
                    payload = json.loads(row[1])
                    item.update(status=row[0], progress=100 if row[0]=='complete' else payload.get('progress',0),
                                stage=payload.get('stage',''), error=payload.get('error',''))
                else: item.update(status='unavailable',stage='Report deleted or expired')
    result.pop('owner', None)
    return result


def run(s, batch_id):
    reserved=False
    try:
        batch = read(s, batch_id)
        for index in range(len(batch['items'])):
            while not s.SLOTS.acquire(timeout=.25):
                if read(s, batch_id)['status']=='cancelled': break
            else:
                reserved=True
                with LOCK:
                    batch = read(s, batch_id)
                    if batch['status']=='cancelled':
                        s.SLOTS.release()
                        reserved=False
                        break
                    item = batch['items'][index]
                    item['id'] = uuid.uuid4().hex
                    data = (directory(s, batch_id) / str(index)).read_bytes()
                    s.save(item['id'], 'queued', {'name':item['name'],'kind':item['kind'],'progress':0,'stage':'Waiting for a worker'}, create=True, owner=batch['owner'])
                    with s.JOB_LOCK: s.CANCELLATIONS[item['id']] = threading.Event()
                    write(s, batch)
                future=s.POOL.submit(s.process, item['id'], item['kind'], item['name'], data, batch['owner'])
                reserved=False
                future.result()
                del data
                (directory(s, batch_id) / str(index)).unlink(missing_ok=True)
                continue
            break
        with LOCK:
            batch = read(s, batch_id)
            if batch['status']!='cancelled': batch['status']='complete'
            for item in batch['items']:
                if not item.get('id'): item['status']='skipped'
            write(s, batch)
    except Exception:
        s.LOG.exception('Batch processing failed')
        with LOCK:
            batch = read(s, batch_id)
            batch.update(status='failed', error='Batch interrupted. Check the report library and resubmit unfinished files.')
            write(s, batch)
    finally:
        if reserved: s.SLOTS.release()
        clean(s, batch_id)


def handle(s, handler, user, method, path):
    """Return false for unrelated routes; ownership is checked before reading bodies."""
    if not path.startswith('/api/batches'): return False
    handler.close_connection = True
    with LOCK:
        expire_uploads(s)
        if method=='GET' and path=='/api/batches/latest':
            with s.database() as db:
                row = db.execute('SELECT payload FROM batches WHERE owner=? ORDER BY created DESC LIMIT 1', (user['id'],)).fetchone()
            handler.respond(200, snapshot(s,json.loads(row[0])) if row else {'items':[], 'status':'empty'})
            return True
        try:
            if method=='POST' and path=='/api/batches':
                length=int(handler.headers.get('Content-Length','0'))
                if handler.headers.get('Content-Type')!='application/json' or not 0<length<=8192: raise ValueError('Expected a small batch manifest.')
                value=json.loads(handler.rfile.read(length))
                files=value.get('files') if isinstance(value,dict) else None
                if not isinstance(files,list) or not 1<=len(files)<=8: raise ValueError('Choose between 1 and 8 files.')
                items=[]
                for entry in files:
                    if not isinstance(entry,dict): raise ValueError('Invalid file description.')
                    name=entry.get('name'); size=entry.get('size')
                    if not isinstance(name,str) or not 1<=len(name)<=200 or type(size) is not int or not 0<size<=s.MAX_BYTES: raise ValueError('Each file must be under 50 MB with a short name.')
                    ext=Path(name).suffix.lower()
                    if ext not in {'.jpg','.jpeg','.png','.webp','.mp4','.mov','.webm','.avi'}: raise ValueError('Unsupported media type.')
                    items.append({'name':name,'size':size,'kind':'image' if ext in {'.jpg','.jpeg','.png','.webp'} else 'video','status':'waiting','uploaded':False})
                if sum(i['size'] for i in items)>200*1024*1024: raise ValueError('Batch limit: 200 MB.')
                with s.database() as db:
                    if db.execute("SELECT 1 FROM batches WHERE status IN ('uploading','processing')").fetchone():
                        handler.respond(429, {'error':'Another batch is active. Try again after it finishes.'}); return True
                    batch={'id':uuid.uuid4().hex,'owner':user['id'],'created':time.time(),'status':'uploading','items':items}
                    db.execute('INSERT INTO batches VALUES (?,?,?,?,?)',(batch['id'],user['id'],batch['created'],batch['status'],json.dumps(batch)))
                    db.execute("DELETE FROM batches WHERE status IN ('complete','failed','cancelled') AND id NOT IN (SELECT id FROM batches ORDER BY created DESC LIMIT 100)")
                directory(s,batch['id']).mkdir(parents=True,exist_ok=True)
                handler.respond(201,snapshot(s,batch)); return True
            match=re.fullmatch(r'/api/batches/([a-f0-9]{32})(?:/(start|cancel|files/([0-7])))?',path)
            if not match: handler.respond(404,{'error':'Batch not found.'}); return True
            batch=read(s,match[1],user['id'])
            if not batch: handler.respond(404,{'error':'Batch not found.'}); return True
            action=match[2]
            if method=='GET' and action is None:
                handler.respond(200,snapshot(s,batch)); return True
            if method!='POST': handler.respond(405,{'error':'Unsupported batch operation.'}); return True
            if action=='cancel':
                if batch['status'] not in TERMINAL:
                    batch['status']='cancelled'
                    for item in batch['items']:
                        if not item.get('id'): item['status']='skipped'
                        with s.JOB_LOCK:
                            event=s.CANCELLATIONS.get(item.get('id'))
                            if event: event.set()
                    write(s,batch)
                    if not any(item.get('id') for item in batch['items']): clean(s,batch['id'])
                handler.respond(200,snapshot(s,batch)); return True
            if batch['status']!='uploading': handler.respond(409,{'error':'This batch is already started or finished.'}); return True
            if action and action.startswith('files/'):
                index=int(match[3])
                if index>=len(batch['items']): raise ValueError('Invalid file index.')
                item=batch['items'][index]
                length=int(handler.headers.get('Content-Length','0'))
                if handler.headers.get('Transfer-Encoding') or handler.headers.get('Content-Type')!='application/octet-stream' or length!=item['size']: raise ValueError('Upload size does not match the manifest.')
                data=handler.rfile.read(length)
                if len(data)!=length: raise ValueError('Upload was interrupted.')
                (directory(s,batch['id']) / str(index)).write_bytes(data)
                item.update(uploaded=True,stage='Uploaded; waiting to start')
                write(s,batch)
                handler.respond(200,snapshot(s,batch)); return True
            if action=='start':
                if not all(i['uploaded'] for i in batch['items']): raise ValueError('Upload all files before starting.')
                batch['status']='processing'; write(s,batch)
                THREADS[:]=[thread for thread in THREADS if thread.is_alive()]
                thread=threading.Thread(target=run,args=(s,batch['id']),daemon=True)
                THREADS.append(thread); thread.start()
                handler.respond(202,snapshot(s,batch)); return True
            handler.respond(404,{'error':'Batch operation not found.'})
        except (ValueError,TypeError): handler.respond(400,{'error':'Invalid batch input or upload. Check file count, size and format.'})
    return True
