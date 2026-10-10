import concurrent.futures
import csv
import hashlib
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import server
from train import train
from truthlens.analysis import AnalysisError, FEATURE_NAMES, image_report, news_report, onnx_prediction, video_report


def image_bytes(seed=1):
    data = np.random.default_rng(seed).integers(0, 255, (40, 60, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(data).save(buffer, format='PNG')
    return buffer.getvalue()


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        patcher=patch.dict(os.environ,{'TRUTHLENS_ONNX_CONFIG':''})
        patcher.start(); self.addCleanup(patcher.stop)
    def test_image_observations_not_fake_verdict(self):
        report = image_report(image_bytes(), 'test.png')
        self.assertEqual(report['verdict'], 'Inconclusive')
        self.assertEqual(report['metrics']['width'], 60)
        self.assertEqual(len(report['sha256']), 64)
        self.assertIsNone(report['model'])
        json.dumps(report, allow_nan=False)

    def test_invalid_image(self):
        with self.assertRaises(AnalysisError):
            image_report(b'not an image', 'test.png')

    def test_news_does_not_verify_claims(self):
        text = 'SHOCKING! This miracle is guaranteed to work according to an unnamed expert. https://example.org'
        report = news_report(text, 'News')
        self.assertIn('miracle', report['metrics']['sensational_phrases'])
        self.assertEqual(report['metrics']['links_in_text'], 1)
        self.assertEqual(report['verdict'], 'Inconclusive')
        with self.assertRaises(AnalysisError):
            news_report('too short', 'News')

    def test_training_roundtrip_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, model = root / 'dataset.csv', root / 'model.json'
            with manifest.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle); writer.writerow(['path', 'label'])
                for index in range(24):
                    filename = f'{index}.png'; (root / filename).write_bytes(image_bytes(index))
                    writer.writerow([filename, index % 2])
            evaluation = train(manifest, model, epochs=20)
            self.assertEqual(evaluation['train_count'] + evaluation['test_count'], 24)
            with patch.dict(os.environ, {'TRUTHLENS_FEATURE_MODEL': str(model)}):
                report = image_report(image_bytes(80), 'new.png')
            self.assertTrue(0 <= report['model']['manipulated_probability'] <= 1)
            with manifest.open('a', encoding='utf-8') as handle:
                handle.write('0.png,0\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                train(manifest, model, epochs=1)

    def test_invalid_feature_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.json'
            path.write_text(json.dumps(dict(features=FEATURE_NAMES, mean=[0]*5, scale=[0]*5, weights=[1]*5, bias=0)))
            with patch.dict(os.environ, {'TRUTHLENS_FEATURE_MODEL': str(path)}), self.assertRaises(AnalysisError):
                image_report(image_bytes(), 'test.png')

    def test_video_and_corrupt_video(self):
        import cv2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 12, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(36):
                writer.write(np.full((48, 64, 3), i * 5, dtype=np.uint8))
            writer.release()
            report = video_report(path, 'test.avi', hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(report['metrics']['frames_sampled'], 24)
            self.assertEqual(report['verdict'], 'Inconclusive')
            self.assertAlmostEqual(report['metrics']['duration_seconds'], 3)
            path.write_bytes(b'corrupt')
            with self.assertRaises(AnalysisError):
                video_report(path, 'test.avi', '')

    def test_onnx_preprocessing_and_probability_contract(self):
        class Session:
            def get_inputs(self):
                return [type('Input', (), {'name':'image'})()]
            def run(self, outputs, feed):
                tensor = feed['image']
                self_shape = tensor.shape
                if self_shape != (1, 3, 224, 224) or tensor.dtype != np.float32:
                    raise AssertionError('Wrong ONNX preprocessing')
                return [np.array([[0., 2.]])]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.onnx').write_bytes(b'fixture')
            config = root / 'model.json'
            config.write_text(json.dumps({'model_path':'model.onnx','activation':'softmax','manipulated_index':1}))
            with patch.dict(os.environ, {'TRUTHLENS_ONNX_CONFIG':str(config)}), patch('truthlens.analysis.onnx_session', return_value=Session()):
                prediction = onnx_prediction(Image.new('RGB',(50,60)))
                self.assertAlmostEqual(prediction['manipulated_probability'], .8808, places=4)
                config.write_text(json.dumps({'model_path':'model.onnx','activation':'wrong'}))
                with self.assertRaises(AnalysisError):
                    onnx_prediction(Image.new('RGB',(50,60)))


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=patch.dict(os.environ,{'TRUTHLENS_ONNX_CONFIG':''}); cls.env.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_data, cls.old_pool = server.DATA, server.POOL
        server.DATA = Path(cls.tmp.name)
        server.POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        with server.database() as db:
            user=server.auth.create_user(db,'testowner','correct-test-password')
            cls.owner=user['id']; token=server.auth.start_session(db,user)
            cls.cookie='truthlens_session='+token
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join()
        server.POOL.shutdown(wait=True)
        server.DATA, server.POOL = cls.old_data, cls.old_pool
        cls.tmp.cleanup()
        cls.env.stop()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
        request_headers={'Cookie':self.cookie}; request_headers.update(headers or {})
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read(); status = response.status
        connection.close()
        return status, json.loads(raw)

    def wait_report(self, job_id):
        for _ in range(500):
            status, payload = self.request('GET', f'/api/reports/{job_id}')
            if payload['status'] in ('complete','failed','cancelled'):
                return payload
            time.sleep(.03)
        self.fail('Job did not finish')

    def make_batch(self, files=None):
        files=files or [{'name':'batch.png','size':len(image_bytes())}]
        status,result=self.request('POST','/api/batches',json.dumps({'files':files}),{'Content-Type':'application/json'})
        self.assertEqual(status,201)
        return result['id']

    def test_persistent_batch_completion_and_cleanup(self):
        batch_id=self.make_batch()
        self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/start')[0],400)
        self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/files/0',image_bytes(),{'Content-Type':'application/octet-stream'})[0],200)
        self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/start')[0],202)
        for _ in range(500):
            _,result=self.request('GET','/api/batches/latest')
            if result['status']=='complete': break
            time.sleep(.03)
        self.assertEqual(result['id'],batch_id)
        self.assertEqual(result['items'][0]['status'],'complete')
        self.assertIn('evidence_summary',self.wait_report(result['items'][0]['id'])['report'])
        for _ in range(50):
            if not server.batches.directory(server,batch_id).exists(): break
            time.sleep(.02)
        self.assertFalse(server.batches.directory(server,batch_id).exists())

    def test_batch_limits_ownership_and_upload_cancellation(self):
        self.assertEqual(self.request('POST','/api/batches','{}',{'Content-Type':'application/json','Cookie':''})[0],401)
        for files in ([],[{'name':'a.png','size':1}]*9,[{'name':'../bad.exe','size':1}],[{'name':'a.png','size':server.MAX_BYTES+1}],[{'name':'a.png','size':'1'}]):
            self.assertEqual(self.request('POST','/api/batches',json.dumps({'files':files}),{'Content-Type':'application/json'})[0],400)
        batch_id=self.make_batch()
        try:
            with server.database() as db:
                other=server.auth.create_user(db,'batch_other','batch-other-password')
                cookie='truthlens_session='+server.auth.start_session(db,other)
            self.assertEqual(self.request('GET',f'/api/batches/{batch_id}',headers={'Cookie':cookie})[0],404)
            self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/cancel',headers={'Cookie':cookie})[0],404)
            self.assertEqual(self.request('POST','/api/batches',json.dumps({'files':[{'name':'a.png','size':1}]}),{'Content-Type':'application/json'})[0],429)
            self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/files/0',b'x',{'Content-Type':'application/octet-stream'})[0],400)
        finally:
            self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/cancel')[0],200)
        self.assertFalse(server.batches.directory(server,batch_id).exists())

    def test_batch_cancel_stops_remaining_files(self):
        blocker=threading.Event()
        tasks=[server.POOL.submit(blocker.wait) for _ in range(2)]
        batch_id=self.make_batch([{'name':f'batch{i}.png','size':len(image_bytes())} for i in range(2)])
        try:
            for i in range(2): self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/files/{i}',image_bytes(),{'Content-Type':'application/octet-stream'})[0],200)
            self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/start')[0],202)
            for _ in range(100):
                _,result=self.request('GET',f'/api/batches/{batch_id}')
                if result['items'][0].get('id'): break
                time.sleep(.02)
            self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/cancel')[0],200)
        finally:
            blocker.set()
            for task in tasks: task.result(timeout=3)
        first=self.wait_report(result['items'][0]['id'])
        self.assertEqual(first['status'],'cancelled')
        for thread in server.batches.THREADS: thread.join(timeout=5)
        _,latest=self.request('GET',f'/api/batches/{batch_id}')
        self.assertEqual(latest['items'][1]['status'],'skipped')
        self.assertFalse(server.batches.directory(server,batch_id).exists())

    def test_report_labels_ownership_and_validation(self):
        _,job=self.request('POST','/api/analyze/image?name=labels.png',image_bytes(),{'Content-Type':'application/octet-stream'})
        self.wait_report(job['id'])
        path=f"/api/reports/{job['id']}/labels"
        headers={'Content-Type':'application/json','X-TruthLens-Action':'review'}
        labels={'folder':'Research','tags':['NASA','image'],'favourite':True}
        self.assertEqual(self.request('PATCH',path,json.dumps(labels),headers)[0],200)
        _,report=self.request('GET',f"/api/reports/{job['id']}")
        self.assertEqual(report['report']['labels'],labels)
        self.assertEqual(self.request('PATCH',path,json.dumps(dict(labels,tags=['x'*25])),headers)[0],400)
        self.assertEqual(self.request('PATCH',path,json.dumps(labels),dict(headers,Cookie=''))[0],401)
        with server.database() as db:
            other=server.auth.create_user(db,'labels_other','labels-other-password')
            cookie='truthlens_session='+server.auth.start_session(db,other)
        self.assertEqual(self.request('PATCH',path,json.dumps(labels),dict(headers,Cookie=cookie))[0],404)

    def test_batch_expiry_and_restart_cleanup(self):
        batch_id=self.make_batch()
        self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/files/0',image_bytes(),{'Content-Type':'application/octet-stream'})[0],200)
        with server.database() as db: db.execute('UPDATE batches SET created=? WHERE id=?',(time.time()-901,batch_id))
        _,result=self.request('GET',f'/api/batches/{batch_id}')
        self.assertEqual(result['status'],'failed')
        self.assertFalse(server.batches.directory(server,batch_id).exists())
        batch_id=self.make_batch()
        self.assertEqual(self.request('POST',f'/api/batches/{batch_id}/files/0',image_bytes(),{'Content-Type':'application/octet-stream'})[0],200)
        server.batches.recover(server)
        _,result=self.request('GET',f'/api/batches/{batch_id}')
        self.assertEqual(result['status'],'failed')
        self.assertIn('Server restarted',result['error'])
        self.assertFalse(server.batches.directory(server,batch_id).exists())

    def test_image_job_history_and_delete(self):
        status, payload = self.request('POST','/api/analyze/image?name=test.png',image_bytes(),{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,202)
        job_id = payload['id']; result = self.wait_report(job_id)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['report']['name'],'test.png')
        status, exported = self.request('GET', f'/api/reports/{job_id}/download')
        self.assertEqual(status, 200)
        self.assertEqual(exported, result['report'])
        status, reports = self.request('GET','/api/reports')
        self.assertTrue(any(report['id'] == job_id for report in reports))
        self.assertEqual(self.request('DELETE',f'/api/reports/{job_id}')[0],403)
        self.assertEqual(self.request('DELETE',f'/api/reports/{job_id}',headers={'X-TruthLens-Action':'delete'})[0],200)
        self.assertEqual(self.request('GET',f'/api/reports/{job_id}')[0],404)

    def test_news_job_and_invalid_body(self):
        status, payload = self.request('POST','/api/analyze/news',json.dumps({'text':'A detailed article according to researchers with claims requiring verification.'}),{'Content-Type':'application/json'})
        self.assertEqual(status,202)
        self.assertEqual(self.wait_report(payload['id'])['status'],'complete')
        self.assertEqual(self.request('POST','/api/analyze/news',b'{broken',{'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.request('POST','/api/analyze/news',b'{"text":42}',{'Content-Type':'application/json'})[0],400)

    def test_corrupt_file_job(self):
        status, payload = self.request('POST','/api/analyze/image',b'corrupt',{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,202)
        self.assertEqual(self.wait_report(payload['id'])['status'],'failed')

    def test_cross_origin_host_and_content_type(self):
        self.assertEqual(self.request('GET','/api/health',headers={'Origin':'https://untrusted.example'})[0],403)
        self.assertEqual(self.request('GET','/api/health',headers={'Host':'untrusted.example'})[0],403)
        self.assertEqual(self.request('POST','/api/analyze/news',b'text',{'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('GET','/api/reports/missing')[0],404)
        self.assertEqual(self.request('GET','/../server.py')[0],404)

    def test_anonymous_and_other_user_cannot_access_report(self):
        self.assertEqual(self.request('GET','/api/reports',headers={'Cookie':''})[0],401)
        status,payload=self.request('POST','/api/analyze/image?name=private.png',image_bytes(),{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,202); self.wait_report(payload['id'])
        with server.database() as db:
            other=server.auth.create_user(db,'otheruser','different-test-password')
            other_cookie='truthlens_session='+server.auth.start_session(db,other)
        self.assertEqual(self.request('GET',f"/api/reports/{payload['id']}",headers={'Cookie':other_cookie})[0],404)
        self.assertEqual(self.request('GET',f"/api/reports/{payload['id']}/download",headers={'Cookie':other_cookie})[0],404)
        self.assertEqual(self.request('GET',f"/api/reports/{payload['id']}/pdf",headers={'Cookie':other_cookie})[0],404)
        self.assertEqual(self.request('DELETE',f"/api/reports/{payload['id']}",headers={'Cookie':other_cookie,'X-TruthLens-Action':'delete'})[0],404)

    def test_login_and_invalid_credentials(self):
        status,result=self.request('POST','/api/auth/login',json.dumps({'username':'testowner','password':'correct-test-password'}),{'Content-Type':'application/json','Cookie':''})
        self.assertEqual(status,200); self.assertEqual(result['user']['username'],'testowner')
        self.assertEqual(self.request('POST','/api/auth/login',json.dumps({'username':'testowner','password':'wrong'}),{'Content-Type':'application/json','Cookie':''})[0],400)

    def test_cancel_queued_job(self):
        blocker=threading.Event()
        tasks=[server.POOL.submit(blocker.wait) for _ in range(2)]
        try:
            status,result=self.request('POST','/api/analyze/image?name=cancel.png',image_bytes(),{'Content-Type':'application/octet-stream'})
            self.assertEqual(status,202)
            self.assertEqual(self.request('POST',f"/api/reports/{result['id']}/cancel",headers={'X-TruthLens-Action':'cancel'})[0],202)
        finally:
            blocker.set()
            for task in tasks: task.result(timeout=3)
        self.assertEqual(self.wait_report(result['id'])['status'],'cancelled')

    def test_reference_comparison(self):
        data=image_bytes(31); reference=image_bytes(32)
        status,result=self.request('POST','/api/analyze/image?name=compare.png',data+reference,{'Content-Type':'application/octet-stream','X-TruthLens-Primary-Length':str(len(data))})
        self.assertEqual(status,202)
        report=self.wait_report(result['id'])
        self.assertEqual(report['status'],'complete'); self.assertIn('comparison',report['report'])
        self.assertEqual(report['report']['comparison']['reference_sha256'],hashlib.sha256(reference).hexdigest())

    def test_report_bundle_ownership_and_input_limits(self):
        from uuid import uuid4
        report=news_report('Researchers claim electricity use will fall by 90 percent according to an independent study.','=formula-name')
        report_id=uuid4().hex
        server.save(report_id,'complete',report,True,self.owner)
        connection=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=10)
        connection.request('GET',f'/api/reports/bundle?ids={report_id}',headers={'Cookie':self.cookie})
        response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(response.getheader('Content-Type'),'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(archive.read(f'reports/{report_id}.pdf').startswith(b'%PDF-'))
            self.assertEqual(json.loads(archive.read(f'reports/{report_id}.json'))['name'],'=formula-name')
            rows=list(csv.reader(io.StringIO(archive.read('summary.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[1][1],"'=formula-name")
        connection.close()
        self.assertEqual(self.request('GET',f'/api/reports/bundle?ids={report_id}',headers={'Cookie':''})[0],401)
        with server.database() as db:
            other=server.auth.create_user(db,'bundleother','different-bundle-password')
            cookie='truthlens_session='+server.auth.start_session(db,other)
        self.assertEqual(self.request('GET',f'/api/reports/bundle?ids={report_id}',headers={'Cookie':cookie})[0],404)
        for ids in ('',report_id+','+report_id,'../private',','.join(uuid4().hex for _ in range(21))):
            self.assertEqual(self.request('GET','/api/reports/bundle?ids='+ids)[0],400)
        self.assertEqual(self.request('GET','/api/reports/bundle?ids='+uuid4().hex)[0],404)

    def test_report_bundle_rejects_incomplete_and_mixed_ownership(self):
        from uuid import uuid4
        pending=uuid4().hex
        server.save(pending,'queued',{'name':'pending','kind':'image'},True,self.owner)
        self.assertEqual(self.request('GET',f'/api/reports/bundle?ids={pending}')[0],409)
        owned,foreign=uuid4().hex,uuid4().hex
        report=news_report('A study claims energy use will fall next year, but its measurements must be checked.','sample')
        server.save(owned,'complete',report,True,self.owner)
        server.save(foreign,'complete',report,True,'another-owner')
        self.assertEqual(self.request('GET',f'/api/reports/bundle?ids={owned},{foreign}')[0],404)

    def test_human_claim_assessment_and_pdf(self):
        status,result=self.request('POST','/api/analyze/news',json.dumps({'text':'A scientific report claims that energy consumption will fall by 90 percent next year.'}),{'Content-Type':'application/json'})
        self.assertEqual(status,202); self.assertEqual(self.wait_report(result['id'])['status'],'complete')
        status,reviewed=self.request('PATCH',f"/api/reports/{result['id']}/claims/0",json.dumps({'assessment':'Insufficient evidence','note':'No primary evidence provided.'}),{'Content-Type':'application/json','X-TruthLens-Action':'review'})
        self.assertEqual(status,200); self.assertEqual(reviewed['report']['claims'][0]['analyst_review']['reviewer'],'testowner')
        connection=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=5)
        connection.request('GET',f"/api/reports/{result['id']}/pdf",headers={'Cookie':self.cookie})
        response=connection.getresponse(); self.assertEqual(response.status,200); self.assertEqual(response.getheader('Content-Type'),'application/pdf'); self.assertTrue(response.read().startswith(b'%PDF-')); connection.close()


if __name__ == '__main__':
    unittest.main()
