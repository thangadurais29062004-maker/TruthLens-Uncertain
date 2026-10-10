import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from evaluate import metrics
from truthlens.evidence import visual_evidence, compare_images, extract_claims, lookup_claims, associate_faces, provenance
from truthlens.pdf_report import export_pdf
from truthlens.analysis import news_report


class UpgradeTests(unittest.TestCase):
    def test_explorer_coverage_and_measurement_explanations(self):
        from truthlens.explorer import build_explorer
        value=build_explorer({'kind':'image','metrics':{},'findings':[{'name':'Recompression residual','value':12.3,'detail':'Measured difference'}],'provenance':{'status':'verification_error'}})
        self.assertEqual(value['cards'][0]['measurement'],12.3)
        self.assertIn('Earlier JPEG compression',value['cards'][0]['alternatives'])
        checks={item['name']:item['status'] for item in value['coverage']}
        self.assertEqual(checks['Image-model inference'],'skipped')
        self.assertEqual(checks['Embedded Content Credentials'],'failed')
        self.assertEqual(checks['Reference comparison'],'skipped')
        self.assertIn('not proof',value['cards'][0]['result'])

    def test_explorer_video_gaps_and_article_lookup_limits(self):
        from truthlens.explorer import build_explorer
        value=build_explorer({'kind':'video','metrics':{'samples':[{'time':0},{'time':2.5},{'time':5}],'frames_sampled':3,'duration_seconds':5}})
        self.assertEqual(value['sample_gaps'][0]['gap_seconds'],2.5)
        self.assertEqual(next(c for c in value['coverage'] if c['name'].startswith('Audio'))['status'],'skipped')
        article=build_explorer({'kind':'news','lookup':{'status':'partial'}})
        self.assertEqual(next(c for c in article['coverage'] if c['name'].startswith('Online'))['status'],'partial')
        self.assertEqual(next(c for c in article['coverage'] if c['name'].startswith('Primary'))['status'],'skipped')

    def test_reference_preview_uses_matching_dimensions(self):
        import base64, io
        result=compare_images(Image.new('RGB',(400,100),'red'),Image.new('RGB',(100,300),'blue'))
        def size(key): return Image.open(io.BytesIO(base64.b64decode(result[key].split(',')[1]))).size
        self.assertEqual(size('aligned_input'),size('reference'))
        self.assertEqual(size('difference'),size('reference'))
    def test_benchmark_model_binding_and_invalid_values(self):
        import tempfile
        from pathlib import Path
        from truthlens import validation
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(validation.status(root)['status'],'awaiting_dataset')
            value={'model_sha256':'a'*64,'config_sha256':'b'*64,'variants':{name:{'coverage':1,'metrics':metrics([0,1],[.1,.9])} for name in ('original','jpeg_quality_60','half_resolution')}}
            path=root/'evaluation.json'; path.write_text(json.dumps(value),encoding='utf-8')
            with patch.object(validation,'model_hash',return_value='c'*64): self.assertEqual(validation.status(root)['status'],'model_mismatch')
            with patch.object(validation,'model_hash',return_value='a'*64), patch.object(validation,'config_hash',return_value='b'*64):
                self.assertEqual(validation.status(root)['status'],'benchmark_available')
                value['variants']['original']['coverage']=float('nan'); path.write_text(json.dumps(value),encoding='utf-8')
                self.assertEqual(validation.status(root)['status'],'invalid_benchmark')

    def test_duplicate_evaluation_samples_are_rejected(self):
        import tempfile
        from pathlib import Path
        from evaluate import evaluate
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'a.bin').write_bytes(b'fixture'); (root/'b.bin').write_bytes(b'fixture')
            manifest=root/'manifest.csv'; manifest.write_text('path,label\na.bin,0\nb.bin,1\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Duplicate'): evaluate(manifest)

    def test_summary_separates_observations_and_conclusions(self):
        from truthlens.summaries import evidence_summary
        article=evidence_summary({'kind':'news','claims':[{'text':'Claim','sources':[{'url':'example'}]}]})
        self.assertEqual(article['result'],'Not verified')
        self.assertIn('1 candidates',article['noticed'][1])
        video=evidence_summary({'kind':'video','metrics':{'frames_sampled':24}})
        self.assertIn('24',video['noticed'][0])
        self.assertIn('audio and lip-sync have not been assessed',video['why'])
    def test_progress_file_lock_does_not_fail_worker(self):
        import worker
        from unittest.mock import patch
        import tempfile, json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            (directory / 'task.json').write_text(json.dumps({'kind':'news','text':'Researchers claim energy use will fall next year according to a new study.','name':'sample'}),encoding='utf-8')
            with patch('worker.sys.argv',['worker.py',folder]), patch('worker.Path.replace',side_effect=PermissionError('Windows reader lock')):
                worker.main()
            self.assertTrue(json.loads((directory / 'result.json').read_text())['ok'])

    def test_visual_overlay_and_comparison(self):
        a=Image.new('RGB',(100,80),'red'); b=Image.new('RGB',(100,80),'blue')
        visuals=visual_evidence(a)
        self.assertTrue(visuals['residual_overlay'].startswith('data:image/jpeg;base64,'))
        self.assertGreater(compare_images(a,b)['mean_absolute_difference'],0)
        self.assertEqual(compare_images(a,a)['mean_absolute_difference'],0)

    def test_geometric_face_tracks(self):
        boxes=[{'x':10,'y':10,'width':50,'height':50}]
        next_id=associate_faces(boxes,[],1)
        second=[{'x':12,'y':12,'width':50,'height':50}]
        self.assertEqual(associate_faces(second,boxes,next_id),2)
        self.assertEqual(second[0]['track_id'],1)

    def test_claim_lookup_is_candidate_not_verdict(self):
        claims=extract_claims('A published study claims that household energy costs will decrease by 90 percent next year.')
        feed=[{'title':claims[0]['text'],'publisher':'Test publisher','url':'https://example.org/review','date':'2026','rating':'False','source_type':'test fixture'}]
        with patch('truthlens.evidence.public_factchecks',return_value=(feed,[])):
            result=lookup_claims(claims)
        self.assertEqual(result['status'],'completed')
        self.assertIn('requires review',claims[0]['assessment'])
        self.assertEqual(claims[0]['sources'][0]['rating'],'False')

    def test_google_adapter_does_not_apply_unrelated_rating(self):
        claims=extract_claims('A specific public claim says every house will receive free electricity next month.')
        response={'claims':[{'text':'A different claim','claimReview':[{'publisher':{'name':'Publisher'},'url':'https://example.org/review','title':'Fact check','textualRating':'False'}]}]}
        with patch('truthlens.evidence.fetch_json',return_value=response):
            lookup_claims(claims,'fake-test-key')
        self.assertIn('requires review',claims[0]['assessment'])

    def test_no_lookup_without_opt_in(self):
        with patch('truthlens.analysis.lookup_claims') as lookup:
            report=news_report('This sufficiently long article describes a scientific claim that needs careful review.','test')
        lookup.assert_not_called(); self.assertEqual(report['lookup']['status'],'disabled')

    def test_evaluation_metrics_and_ties(self):
        result=metrics([0,0,1,1],[.1,.2,.8,.9])
        self.assertEqual(result['roc_auc'],1); self.assertEqual(result['false_positive_rate'],0)
        self.assertEqual(metrics([0,1],[.5,.5])['roc_auc'],.5)
        with self.assertRaises(ValueError): metrics([0],[.2])

    def test_unsigned_image_provenance(self):
        import io
        buffer=io.BytesIO(); Image.new('RGB',(40,40)).save(buffer,format='JPEG')
        result=provenance(buffer.getvalue(),'image/jpeg')
        self.assertEqual(result['status'],'absent',result)

    def test_signed_untrusted_and_tampered_credentials(self):
        from pathlib import Path
        raw=(Path(__file__).parent/'fixtures/c2pa-untrusted.jpg').read_bytes()
        self.assertEqual(provenance(raw,'image/jpeg')['status'],'valid_untrusted')
        modified=bytearray(raw); modified[-128]^=1
        self.assertEqual(provenance(bytes(modified),'image/jpeg')['status'],'invalid')

    def test_real_pdf_structure(self):
        report=news_report('This sufficiently long article according to a researcher claims temperatures changed by 2 degrees.','PDF test')
        report['id']='test'
        result=export_pdf(report)
        self.assertTrue(result.startswith(b'%PDF-')); self.assertIn(b'%%EOF',result)
