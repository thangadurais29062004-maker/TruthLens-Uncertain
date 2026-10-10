"""Local-only HTTP API. Uploaded media is temporary; reports live in SQLite."""
from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from truthlens.analysis import AnalysisError, image_report, news_report, video_report
from truthlens import auth
from truthlens import batches
from truthlens.summaries import evidence_summary
from truthlens import validation
from truthlens.explorer import build_explorer
from truthlens.analysis import model_config_path
from truthlens.pdf_report import export_pdf

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('TRUTHLENS_DATA_DIR', ROOT / 'data'))
MAX_BYTES = 50 * 1024 * 1024
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
SLOTS = threading.BoundedSemaphore(4)
LOG = logging.getLogger('truthlens')
DB_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
CANCELLATIONS = {}
AUTH_ATTEMPTS = {}


def report_bundle(reports):
    """Export owned, completed reports; filenames use IDs, never uploaded names."""
    output = io.BytesIO()
    summary = io.StringIO(newline='')
    writer = csv.writer(summary)
    writer.writerow(['Report ID','Name','Content type','Created','Result'])
    def cell(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for report in reports:
            report_id = report['id']
            writer.writerow([report_id,cell(report['name']),cell(report['kind']),cell(report.get('created_at','')),cell(report['verdict'])])
            archive.writestr(f'reports/{report_id}.json',json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False))
            archive.writestr(f'reports/{report_id}.pdf',export_pdf(report))
        archive.writestr('summary.csv',summary.getvalue().encode('utf-8-sig'))
        archive.writestr('READ-ME.txt','TruthLens report bundle\n\nEach report is provided as PDF and JSON. summary.csv is the index.\nExperimental model scores are not verified probabilities of manipulation.\nThese reports support further review; they do not establish factual truth.\nThe bundle may contain personal filenames and visual excerpts. Share thoughtfully.\n')
    return output.getvalue()


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / 'reports.sqlite3', timeout=10)
    try:
        with connection:
            with DB_LOCK:
                connection.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
                auth.schema(connection)
                connection.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
                connection.execute('CREATE TABLE IF NOT EXISTS report_labels (id TEXT PRIMARY KEY, folder TEXT NOT NULL, tags TEXT NOT NULL, favourite INTEGER NOT NULL)')
            yield connection
    finally:
        connection.close()


def save(job_id, status, payload, create=False, owner=''):
    with database() as db:
        if create:
            db.execute('INSERT INTO reports (id,created,status,payload,owner) VALUES (?, ?, ?, ?, ?)', (job_id, time.time(), status, json.dumps(payload, allow_nan=False), owner))
            # Keep only the most recent 200 finished reports; active jobs are never removed.
            db.execute("DELETE FROM reports WHERE owner=? AND status IN ('complete','failed','cancelled') AND id NOT IN (SELECT id FROM reports WHERE owner=? ORDER BY created DESC LIMIT 200)", (owner,owner))
            retention=int(os.environ.get('TRUTHLENS_RETENTION_DAYS','0'))
            if retention>0:
                db.execute("DELETE FROM reports WHERE owner=? AND status IN ('complete','failed','cancelled') AND created<?",(owner,time.time()-retention*86400))
            db.execute('DELETE FROM report_labels WHERE id NOT IN (SELECT id FROM reports)')
        else:
            db.execute('UPDATE reports SET status=?, payload=? WHERE id=?', (status, json.dumps(payload, allow_nan=False), job_id))


def process(job_id, kind, name, data, owner='', reference=None):
    cancel = CANCELLATIONS.get(job_id, threading.Event())
    child = None
    try:
        if cancel.is_set():
            save(job_id,'cancelled',{'name':name,'kind':kind,'error':'Analysis cancelled.'}); return
        save(job_id, 'processing', {'name': name, 'kind': kind, 'progress':5, 'stage':'Starting isolated analysis worker'})
        temporary_root = DATA / 'temporary'; temporary_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='job-',dir=temporary_root) as directory:
            directory = Path(directory)
            task = {'name':name,'kind':kind}
            if kind == 'news':
                task.update(data)
            else:
                suffix = Path(name).suffix.lower() if kind == 'video' else '.bin'
                if kind == 'video' and suffix not in {'.mp4','.mov','.webm','.avi'}:
                    raise AnalysisError('Use MP4, MOV, WebM, or AVI video.')
                task['filename'] = 'input' + suffix
                task['digest'] = hashlib.sha256(data).hexdigest()
                (directory / task['filename']).write_bytes(data)
                if reference:
                    (directory / 'reference.bin').write_bytes(reference)
            (directory / 'task.json').write_text(json.dumps(task),encoding='utf-8')
            with (directory / 'worker.log').open('wb') as diagnostic:
                child = subprocess.Popen([sys.executable,str(ROOT / 'worker.py'),str(directory)],stdout=diagnostic,stderr=diagnostic,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                started, last_progress = time.monotonic(), None
                while child.poll() is None:
                    if cancel.is_set() or time.monotonic()-started > 180:
                        child.kill(); child.wait()
                        if cancel.is_set():
                            save(job_id,'cancelled',{'name':name,'kind':kind,'error':'Analysis cancelled.'}); return
                        raise AnalysisError('Analysis exceeded the three-minute processing limit.')
                    progress_path = directory / 'progress.json'
                    if progress_path.is_file():
                        try:
                            update = json.loads(progress_path.read_text(encoding='utf-8'))
                            if update != last_progress:
                                save(job_id,'processing',{'name':name,'kind':kind,**update}); last_progress=update
                        except (OSError,ValueError):
                            pass
                    time.sleep(.12)
            result_path = directory / 'result.json'
            if child.returncode or not result_path.is_file():
                raise AnalysisError('The isolated analysis worker failed. Check dependencies and the model configuration.')
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if not result['ok']:
                LOG.error('Worker diagnostics for %s: %s', job_id, (directory / 'worker.log').read_text(encoding='utf-8',errors='replace')[-8000:])
                raise AnalysisError(result['error'])
            report = result['report']; report['id'] = job_id
            report['evidence_summary']=evidence_summary(report); report['explorer']=build_explorer(report)
            with JOB_LOCK:
                if cancel.is_set():
                    save(job_id,'cancelled',{'name':name,'kind':kind,'error':'Analysis cancelled.'})
                else:
                    save(job_id, 'complete', report)
    except AnalysisError as exc:
        save(job_id, 'failed', {'name': name, 'kind': kind, 'error': str(exc)})
    except Exception:
        LOG.exception('Analysis failed for %s', job_id)
        save(job_id, 'failed', {'name': name, 'kind': kind, 'error': 'Analysis failed. Check the server log and any configured model contract.'})
    finally:
        if child and child.poll() is None:
            child.kill(); child.wait()
        with JOB_LOCK:
            CANCELLATIONS.pop(job_id,None)
        SLOTS.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        LOG.info(format, *args)

    def respond(self, status, payload, mime='application/json', attachment=None, cookie=None):
        body = json.dumps(payload, allow_nan=False).encode() if mime == 'application/json' else payload
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            self.send_header('Set-Cookie',cookie)
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        public = os.environ.get('TRUTHLENS_PUBLIC_ORIGIN','').rstrip('/')
        if public:
            parsed = urlparse(public)
            if parsed.scheme != 'https' or not parsed.netloc:
                return False
            allowed.add(parsed.netloc)
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        origins = {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
        if public: origins.add(public)
        return host in allowed and (not origin or origin in origins)

    def user(self):
        with database() as db:
            return auth.current_user(db,self.headers.get('Cookie',''))

    def session_cookie(self,token):
        secure = '; Secure' if os.environ.get('TRUTHLENS_PUBLIC_ORIGIN') else ''
        return f'truthlens_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}'

    def authentication(self,action):
        self.close_connection = True
        try:
            length=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0 < length <= 16384:
                return self.respond(400,{'error':'Expected a small JSON request.'})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError('Invalid credentials.')
            if action == 'logout':
                with database() as db: db.execute('DELETE FROM sessions WHERE token_hash=?',(auth.session_hash(self.headers.get('Cookie','')),))
                return self.respond(200,{'signed_out':True},cookie='truthlens_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            address=self.client_address[0]
            with JOB_LOCK:
                attempts=[t for t in AUTH_ATTEMPTS.get(address,[]) if time.time()-t < 300]
                if len(attempts)>=10: return self.respond(429,{'error':'Too many authentication attempts. Wait five minutes.'})
                AUTH_ATTEMPTS[address]=attempts+[time.time()]
            if action=='register' and os.environ.get('TRUTHLENS_PUBLIC_ORIGIN') and os.environ.get('TRUTHLENS_ALLOW_REGISTRATION')!='1':
                return self.respond(403,{'error':'Account registration is disabled. Ask the administrator to create your account.'})
            with database() as db:
                user=auth.create_user(db,data.get('username'),data.get('password')) if action=='register' else auth.login(db,data.get('username'),data.get('password'))
                token=auth.start_session(db,user)
            with JOB_LOCK: AUTH_ATTEMPTS.pop(address,None)
            return self.respond(200,{'user':user},cookie=self.session_cookie(token))
        except (ValueError,TypeError):
            return self.respond(400,{'error':'Invalid credentials. Use a unique username and a password of at least 12 characters when registering.'})

    def do_GET(self):
        if not self.trusted_request():
            return self.respond(403, {'error': 'Local requests only.'})
        path = urlparse(self.path).path
        if path == '/api/health':
            import importlib.util
            try:
                image_task=json.loads(Path(model_config_path()).read_text(encoding='utf-8')).get('prediction_task') if model_config_path() else None
            except (OSError,ValueError): image_task=None
            return self.respond(200, {'status': 'ok', 'version':'2.4', 'image_prediction_task':image_task, 'video': bool(importlib.util.find_spec('cv2')), 'feature_model': Path(os.environ.get('TRUTHLENS_FEATURE_MODEL', ROOT / 'models/feature-model.json')).is_file(), 'onnx_configured': bool(model_config_path()),
                                     'provenance':bool(importlib.util.find_spec('c2pa')), 'google_factchecks':bool(os.environ.get('GOOGLE_FACT_CHECK_API_KEY')),
                                     'registration_allowed':not os.environ.get('TRUTHLENS_PUBLIC_ORIGIN') or os.environ.get('TRUTHLENS_ALLOW_REGISTRATION')=='1'})
        if path.startswith('/api/'):
            user=self.user()
            if not user: return self.respond(401,{'error':'Sign in to access your investigations.'})
            if path=='/api/me': return self.respond(200,{'user':user})
            if path=='/api/validation': return self.respond(200,validation.status(ROOT))
            if batches.handle(sys.modules[__name__],self,user,'GET',path): return
        if path == '/api/reports':
            with database() as db:
                rows = db.execute('SELECT reports.id, created, status, payload,folder,tags,favourite FROM reports LEFT JOIN report_labels ON reports.id=report_labels.id WHERE owner=? ORDER BY created DESC LIMIT 200',(user['id'],)).fetchall()
            return self.respond(200, [{'id': row[0], 'created': row[1], 'status': row[2], 'name': json.loads(row[3]).get('name', ''), 'kind': json.loads(row[3]).get('kind', ''),'folder':row[4] or '', 'tags':json.loads(row[5] or '[]'), 'favourite':bool(row[6])} for row in rows])
        if path == '/api/reports/bundle':
            ids = parse_qs(urlparse(self.path).query).get('ids',[''])[0].split(',')
            if not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids) or any(not re.fullmatch('[a-f0-9]{32}',item) for item in ids):
                return self.respond(400,{'error':'Select between 1 and 20 distinct completed reports.'})
            reports=[]
            with database() as db:
                for report_id in ids:
                    row=db.execute('SELECT status,payload FROM reports WHERE id=? AND owner=?',(report_id,user['id'])).fetchone()
                    if not row: return self.respond(404,{'error':'A selected report is unavailable.'})
                    if row[0] != 'complete': return self.respond(409,{'error':'Only completed reports can be bundled.'})
                    payload=json.loads(row[1]); payload['id']=report_id
                    labels=db.execute('SELECT folder,tags,favourite FROM report_labels WHERE id=?',(report_id,)).fetchone()
                    payload['labels']={'folder':labels[0],'tags':json.loads(labels[1]),'favourite':bool(labels[2])} if labels else {'folder':'','tags':[],'favourite':False}
                    payload['evidence_summary']=evidence_summary(payload); payload['explorer']=build_explorer(payload)
                    reports.append(payload)
            return self.respond(200,report_bundle(reports),'application/zip',attachment='truthlens-report-bundle.zip')
        if path.startswith('/api/reports/'):
            parts = path.split('/')
            download = len(parts) == 5 and parts[4] in ('download','pdf')
            job_id = parts[3] if len(parts) >= 4 else ''
            if not re.fullmatch('[a-f0-9]{32}', job_id) or (len(parts) != 4 and not download):
                return self.respond(404, {'error': 'Report not found.'})
            with database() as db:
                row = db.execute('SELECT status, payload FROM reports WHERE id=? AND owner=?', (job_id,user['id'])).fetchone()
            if download and row:
                if row[0] != 'complete':
                    return self.respond(409, {'error': 'Only completed reports can be exported.'})
                if parts[4]=='pdf':
                    return self.respond(200,export_pdf(json.loads(row[1])),'application/pdf',attachment=f'truthlens-{job_id}.pdf')
                report=json.loads(row[1]); report['evidence_summary']=evidence_summary(report); report['explorer']=build_explorer(report)
                with database() as db: labels=db.execute('SELECT folder,tags,favourite FROM report_labels WHERE id=?',(job_id,)).fetchone()
                report['labels']={'folder':labels[0],'tags':json.loads(labels[1]),'favourite':bool(labels[2])} if labels else {'folder':'','tags':[],'favourite':False}
                return self.respond(200, report, attachment=f'truthlens-{job_id}.json')
            if row:
                report=json.loads(row[1])
                if row[0]=='complete': report['evidence_summary']=evidence_summary(report); report['explorer']=build_explorer(report)
                with database() as db: labels=db.execute('SELECT folder,tags,favourite FROM report_labels WHERE id=?',(job_id,)).fetchone()
                report['labels']={'folder':labels[0],'tags':json.loads(labels[1]),'favourite':bool(labels[2])} if labels else {'folder':'','tags':[],'favourite':False}
                return self.respond(200, {'status':row[0], 'report':report})
            return self.respond(404, {'error':'Report not found.'})
        assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/upgrades.css':'upgrades.css', '/review.css':'review.css', '/favicon.svg': 'favicon.svg'}
        if path in assets:
            name = assets[path]
            mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8', 'svg': 'image/svg+xml'}[name.rsplit('.', 1)[-1]]
            return self.respond(200, (ROOT / 'web' / name).read_bytes(), mime)
        self.respond(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.trusted_request():
            return self.respond(403, {'error': 'Local requests only.'})
        parsed = urlparse(self.path)
        if parsed.path in ('/api/auth/login','/api/auth/register','/api/auth/logout'):
            return self.authentication(parsed.path.rsplit('/',1)[-1])
        user=self.user()
        if not user:
            self.close_connection=True
            return self.respond(401,{'error':'Sign in to analyze content.'})
        if batches.handle(sys.modules[__name__],self,user,'POST',parsed.path): return
        if re.fullmatch(r'/api/reports/[a-f0-9]{32}/cancel',parsed.path):
            job_id=parsed.path.split('/')[3]
            self.close_connection=True
            if self.headers.get('X-TruthLens-Action')!='cancel': return self.respond(403,{'error':'Request rejected.'})
            with database() as db: row=db.execute('SELECT status FROM reports WHERE id=? AND owner=?',(job_id,user['id'])).fetchone()
            if not row: return self.respond(404,{'error':'Report not found.'})
            with JOB_LOCK:
                cancel=CANCELLATIONS.get(job_id)
                if cancel: cancel.set()
            return self.respond(202 if cancel else 409,{'status':'cancellation_requested' if cancel else 'already_finished'})
        kind = parsed.path.rsplit('/', 1)[-1]
        if parsed.path not in {'/api/analyze/image', '/api/analyze/video', '/api/analyze/news'}:
            return self.respond(404, {'error': 'Not found.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.respond(400, {'error': 'Invalid content length.'})
        if self.headers.get('Transfer-Encoding') or size <= 0 or size > MAX_BYTES or (kind == 'news' and size > 220_000):
            self.close_connection = True
            return self.respond(413, {'error': 'Empty or oversized upload. Media limit: 50 MB.'})
        expected = 'application/json' if kind == 'news' else 'application/octet-stream'
        if self.headers.get('Content-Type', '').split(';')[0] != expected:
            self.close_connection = True
            return self.respond(415, {'error': f'Expected {expected}.'})
        if not SLOTS.acquire(blocking=False):
            self.close_connection = True
            return self.respond(429, {'error': 'The analysis queue is full. Try again shortly.'})
        handed_off = False
        try:
            data = self.rfile.read(size)
            if len(data) != size:
                raise AnalysisError('Upload was interrupted.')
            name = parse_qs(parsed.query).get('name', [f'{kind} upload'])[0][:160]
            reference=None
            split=self.headers.get('X-TruthLens-Primary-Length')
            if split:
                try: split=int(split)
                except ValueError: raise AnalysisError('Invalid comparison upload.')
                if kind!='image' or not 0 < split < len(data): raise AnalysisError('Invalid comparison upload.')
                data,reference=data[:split],data[split:]
            if kind == 'news':
                try:
                    data = json.loads(data)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise AnalysisError('Invalid JSON article.') from exc
                if not isinstance(data, dict) or not isinstance(data.get('text'), str) or not isinstance(data.get('source_url', ''), str):
                    raise AnalysisError('Article text and source URL must be strings.')
                if not 40 <= len(data['text'].strip()) <= 50_000:
                    raise AnalysisError('Paste between 40 and 50,000 characters of article text.')
                if not isinstance(data.get('online',False),bool): raise AnalysisError('Online lookup must be true or false.')
                data={key:data[key] for key in ('text','source_url','online') if key in data}
            job_id = uuid.uuid4().hex
            save(job_id, 'queued', {'name': name, 'kind': kind, 'progress':0,'stage':'Waiting for a worker'}, create=True,owner=user['id'])
            with JOB_LOCK: CANCELLATIONS[job_id]=threading.Event()
            POOL.submit(process, job_id, kind, name, data,user['id'],reference)
            handed_off = True
            self.respond(202, {'id': job_id, 'status': 'queued'})
        except AnalysisError as exc:
            self.respond(400, {'error': str(exc)})
        except (TimeoutError, OSError):
            self.close_connection = True
        except Exception:
            LOG.exception('Request failed')
            self.respond(500, {'error': 'Unable to start analysis.'})
        finally:
            if not handed_off:
                SLOTS.release()

    def do_DELETE(self):
        if not self.trusted_request() or self.headers.get('X-TruthLens-Action') != 'delete':
            return self.respond(403, {'error': 'Request rejected.'})
        user=self.user()
        if not user: return self.respond(401,{'error':'Sign in to delete reports.'})
        path = urlparse(self.path).path
        if not path.startswith('/api/reports/'):
            return self.respond(404, {'error': 'Not found.'})
        with database() as db:
            row = db.execute('SELECT status FROM reports WHERE id=? AND owner=?', (path.rsplit('/', 1)[-1],user['id'])).fetchone()
            if not row:
                return self.respond(404, {'error': 'Report not found.'})
            if row[0] in ('queued', 'processing'):
                return self.respond(409, {'error': 'Wait for analysis to finish before deleting.'})
            db.execute('DELETE FROM reports WHERE id=? AND owner=?', (path.rsplit('/', 1)[-1],user['id']))
            db.execute('DELETE FROM report_labels WHERE id=?',(path.rsplit('/',1)[-1],))
        self.respond(200, {'deleted': True})

    def do_PATCH(self):
        if not self.trusted_request() or self.headers.get('X-TruthLens-Action')!='review':
            return self.respond(403,{'error':'Request rejected.'})
        user=self.user()
        if not user: return self.respond(401,{'error':'Sign in to review a claim.'})
        labels_match=re.fullmatch(r'/api/reports/([a-f0-9]{32})/labels',urlparse(self.path).path)
        if labels_match:
            self.close_connection=True
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Content-Type')!='application/json' or not 0<length<=4096: raise ValueError('Invalid labels')
                labels=json.loads(self.rfile.read(length))
                if not isinstance(labels,dict): raise ValueError('Invalid labels')
                folder=labels.get('folder',''); tags=labels.get('tags',[]); favourite=labels.get('favourite',False)
                if not isinstance(folder,str) or len(folder)>64 or not isinstance(tags,list) or len(tags)>5 or any(not isinstance(tag,str) or not 1<=len(tag.strip())<=24 for tag in tags) or type(favourite) is not bool: raise ValueError('Invalid labels')
                tags=list(dict.fromkeys(tag.strip() for tag in tags))
                with database() as db:
                    if not db.execute('SELECT 1 FROM reports WHERE id=? AND owner=?',(labels_match[1],user['id'])).fetchone(): return self.respond(404,{'error':'Report not found.'})
                    db.execute('INSERT OR REPLACE INTO report_labels VALUES (?,?,?,?)',(labels_match[1],folder.strip(),json.dumps(tags),int(favourite)))
                return self.respond(200,{'saved':True})
            except (ValueError,TypeError): return self.respond(400,{'error':'Use a folder under 64 characters and up to 5 tags of 24 characters.'})
        match=re.fullmatch(r'/api/reports/([a-f0-9]{32})/claims/([0-4])',urlparse(self.path).path)
        if not match: return self.respond(404,{'error':'Claim not found.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<length<=12000:
                self.close_connection=True
                return self.respond(400,{'error':'Expected a small JSON review.'})
            review=json.loads(self.rfile.read(length))
            if not isinstance(review,dict) or review.get('assessment') not in ('Supported','Contradicted','Insufficient evidence') or not isinstance(review.get('note',''),str) or len(review.get('note',''))>2000:
                raise ValueError('Invalid review')
            with database() as db:
                row=db.execute('SELECT status,payload FROM reports WHERE id=? AND owner=?',(match[1],user['id'])).fetchone()
                if not row or row[0]!='complete': return self.respond(404,{'error':'Completed report not found.'})
                report=json.loads(row[1]); index=int(match[2])
                if index>=len(report.get('claims',[])): return self.respond(404,{'error':'Claim not found.'})
                claim=report['claims'][index]
                claim['analyst_review']={'assessment':review['assessment'],'note':review.get('note',''),'reviewer':user['username'],'reviewed_at':time.time(),
                                        'label':'Human assessment, not automated factual verification'}
                db.execute('UPDATE reports SET payload=? WHERE id=? AND owner=?',(json.dumps(report,allow_nan=False),match[1],user['id']))
                labels=db.execute('SELECT folder,tags,favourite FROM report_labels WHERE id=?',(match[1],)).fetchone()
                report['labels']={'folder':labels[0],'tags':json.loads(labels[1]),'favourite':bool(labels[2])} if labels else {'folder':'','tags':[],'favourite':False}
                report['evidence_summary']=evidence_summary(report); report['explorer']=build_explorer(report)
            return self.respond(200,{'report':report})
        except (ValueError,TypeError):
            return self.respond(400,{'error':'Choose an assessment and keep notes under 2,000 characters.'})


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    batches.recover(sys.modules[__name__])
    with database() as db:
        db.execute("UPDATE reports SET status='failed', payload=json_set(payload, '$.error', 'Server restarted before analysis completed.') WHERE status IN ('queued', 'processing')")
    temporary_root=(DATA/'temporary').resolve()
    if temporary_root.is_dir():
        import shutil
        for leftover in temporary_root.glob('job-*'):
            resolved=leftover.resolve()
            if resolved.parent==temporary_root and leftover.is_dir() and not leftover.is_symlink():
                shutil.rmtree(resolved)
    port = int(os.environ.get('PORT', '8000'))
    server = ThreadingHTTPServer((os.environ.get('TRUTHLENS_BIND','127.0.0.1'), port), Handler)
    batches.start_maintenance(sys.modules[__name__])
    LOG.info('TruthLens is ready at http://127.0.0.1:%s', port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with JOB_LOCK:
            for cancel in CANCELLATIONS.values(): cancel.set()
        batches.shutdown(sys.modules[__name__])
        POOL.shutdown(wait=True)


if __name__ == '__main__':
    main()
