import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from truthlens.analysis import AnalysisError, prepare_onnx_image, onnx_prediction, detector_prediction
from truthlens.prediction import decide, image_prediction


class PredictionTests(unittest.TestCase):
    def test_fixed_thresholds_and_abstention(self):
        self.assertEqual(decide([.05,.1,.2],(256,256))[0],'likely_real')
        self.assertEqual(decide([.8,.9,.99],(256,256))[0],'likely_ai')
        for scores in ([.1,.9,.1],[.5,.5,.5],[.01,.02,float('nan')],[.1,.2]):
            self.assertEqual(decide(scores,(256,256))[0],'uncertain')

    def test_quality_gate(self):
        self.assertEqual(decide([.99]*3,(64,64))[0],'uncertain')
        self.assertEqual(decide([.01]*3,(1200,256))[0],'uncertain')

    def test_preprocessing_preserves_aspect_and_center(self):
        pixels=np.zeros((256,512,3),np.uint8);pixels[:,144:368]=255
        im=Image.fromarray(pixels)
        result=prepare_onnx_image(im,{'size':224,'resize_shorter':256,'resample':'bilinear'})
        self.assertEqual(result.size,(224,224))
        self.assertTrue((np.asarray(result)==255).all())
        with self.assertRaises(AnalysisError):prepare_onnx_image(im,{'size':224,'resize_shorter':100})

    def test_three_views_and_exportable_prediction(self):
        calls=[]
        def predictor(image):
            calls.append(image.size)
            return {'manipulated_probability':.95}
        value=image_prediction(Image.new('RGB',(512,256)),{'manipulated_probability':.98},predictor)
        self.assertEqual(calls,[(512,256),(256,128)])
        self.assertEqual(value['label'],'Likely AI-generated')
        self.assertEqual(len(value['scores']),3)
        self.assertIn('not calibrated',value['notice'])
        json.dumps(value,allow_nan=False)

    def test_pinned_model_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'model.onnx').write_bytes(b'wrong')
            cfg=root/'config.json';cfg.write_text(json.dumps({'model_path':'model.onnx','sha256':'a'*64}))
            with self.assertRaisesRegex(AnalysisError,'checksum'):
                onnx_prediction(Image.new('RGB',(224,224)),str(cfg))

    def test_video_keeps_separate_model_config(self):
        with patch('truthlens.analysis.model_config_path',return_value=''),patch('truthlens.analysis.feature_prediction',return_value=None) as fallback:
            self.assertIsNone(detector_prediction(Image.new('RGB',(224,224)),kind='video'))
            fallback.assert_called_once()

    def test_evaluator_measures_uncertain_coverage(self):
        from evaluate import evaluate
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            Image.new('RGB',(256,256),'red').save(root/'real.png')
            Image.new('RGB',(256,256),'blue').save(root/'fake.png')
            manifest=root/'manifest.csv';manifest.write_text('path,label\nreal.png,0\nfake.png,1\n')
            scores=iter([.05,.1,.15,.95,.5,.9])
            with patch('evaluate.detector_prediction',side_effect=lambda image:{'prediction_task':'ai_generation','manipulated_probability':next(scores)}):
                result=evaluate(manifest,include_policy=True)
            self.assertEqual(result['selective']['count'],1)
            self.assertEqual(result['selective']['uncertain'],1)
            self.assertEqual(result['selective']['coverage'],.5)
            self.assertEqual(result['selective']['accuracy'],1)

    def test_benchmark_rejects_changed_prediction_pipeline(self):
        from evaluate import metrics
        from truthlens import validation
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            value={'model_sha256':'a'*64,'config_sha256':'b'*64,'pipeline_sha256':'old',
                'variants':{name:{'coverage':1,'metrics':metrics([0,1],[.1,.9])} for name in ('original','jpeg_quality_60','half_resolution')}}
            (root/'evaluation.json').write_text(json.dumps(value))
            with patch.object(validation,'model_hash',return_value='a'*64),patch.object(validation,'config_hash',return_value='b'*64),patch.object(validation,'pipeline_hash',return_value='new'):
                self.assertEqual(validation.status(root)['status'],'model_mismatch')

    def test_pdf_has_prediction_and_stability_scores(self):
        from truthlens.analysis import image_report
        from truthlens.pdf_report import export_pdf
        import io
        stream=io.BytesIO();Image.new('RGB',(128,128),'gray').save(stream,format='PNG')
        with patch.dict(os.environ,{'TRUTHLENS_ONNX_CONFIG':''}):report=image_report(stream.getvalue(),'test.png')
        report['prediction']=image_prediction(Image.new('RGB',(128,128)),{'manipulated_probability':.05},lambda image:{'manipulated_probability':.05})
        self.assertTrue(export_pdf(report).startswith(b'%PDF-'))
