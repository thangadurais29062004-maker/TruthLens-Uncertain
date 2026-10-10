"""Display a configured benchmark only when it identifies the active model bytes."""
import hashlib
import json
import math
import os
from pathlib import Path
from truthlens.analysis import model_config_path


def model_hash():
    config_path=model_config_path()
    if not config_path: return None
    config_path=Path(config_path)
    config=json.loads(config_path.read_text(encoding='utf-8'))
    model=(config_path.parent/config['model_path']).resolve()
    return hashlib.sha256(model.read_bytes()).hexdigest()


def config_hash():
    path=model_config_path()
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


def pipeline_hash():
    root=Path(__file__).resolve().parent
    return hashlib.sha256((root/'analysis.py').read_bytes()+b'\0'+(root/'prediction.py').read_bytes()).hexdigest()


def status(root):
    default=root/'evaluation.json'
    if not default.is_file() and (root/'benchmarks/image-benchmark.json').is_file():
        default=root/'benchmarks/image-benchmark.json'
    path=Path(os.environ.get('TRUTHLENS_EVALUATION_FILE',default))
    if not path.is_file(): return {'status':'awaiting_dataset','message':'No independent benchmark is configured. Scores remain experimental and uncalibrated.','variants':{}}
    try:
        if path.stat().st_size>1024*1024: raise ValueError('oversized')
        value=json.loads(path.read_text(encoding='utf-8'))
        active_hash=model_hash()
        if not active_hash or value.get('model_sha256')!=active_hash or value.get('config_sha256')!=config_hash():
            return {'status':'model_mismatch','message':'The benchmark does not identify the active model. Run evaluate.py again for this model.','variants':{}}
        variants={}
        if value.get('pipeline_sha256') and value['pipeline_sha256']!=pipeline_hash():
            return {'status':'model_mismatch','message':'The benchmark uses a different prediction pipeline. Run evaluate.py again.','variants':{}}
        for name in ('original','jpeg_quality_60','half_resolution'):
            item=value['variants'][name]; coverage=item['coverage']; metrics=item['metrics']
            if type(coverage) not in (float,int) or not math.isfinite(coverage) or not 0<=coverage<=1: raise ValueError('coverage')
            sanitized={}
            if metrics is not None:
                for key in ('count','false_positive','false_negative','accuracy','precision','recall','false_positive_rate','roc_auc','brier_score','expected_calibration_error'):
                    number=metrics[key]
                    if type(number) not in (float,int) or not math.isfinite(number) or number<0: raise ValueError('metrics')
                    if key not in ('count','false_positive','false_negative') and number>1: raise ValueError('range')
                    if key in ('count','false_positive','false_negative') and int(number)!=number: raise ValueError('count')
                    sanitized[key]=number
                if sanitized['count']<2 or sanitized['false_positive']+sanitized['false_negative']>sanitized['count']: raise ValueError('counts')
            variants[name]={'coverage':coverage,'metrics':sanitized or None}
        selective=None
        if value.get('selective') is not None:
            if not value.get('pipeline_sha256'): raise ValueError('selective pipeline identity')
            item=value['selective']; selective={}
            for key in ('count','uncertain','real_predictions','fake_predictions','coverage','accuracy','real_precision','fake_precision'):
                number=item[key]
                if type(number) not in (float,int) or not math.isfinite(number) or number<0: raise ValueError('selective')
                if key in ('coverage','accuracy','real_precision','fake_precision'):
                    if number>1: raise ValueError('selective range')
                elif int(number)!=number: raise ValueError('selective count')
                selective[key]=number
            if selective['real_predictions']+selective['fake_predictions']!=selective['count']: raise ValueError('selective classes')
            total=selective['count']+selective['uncertain']
            if total<=0 or not math.isclose(selective['coverage'],selective['count']/total): raise ValueError('selective coverage')
        dataset=value.get('dataset',{})
        groups={}
        for name,item in value.get('generators',{}).items():
            count, accuracy=item['count'],item['accuracy']
            if type(count) is not int or count<1 or type(accuracy) not in (float,int) or not math.isfinite(accuracy) or not 0<=accuracy<=1: raise ValueError('generator metrics')
            groups[str(name)[:80]]={'count':count,'accuracy':accuracy}
        message='Measurements apply only to the supplied dataset and detected inputs. Dataset independence is supplied by the operator; it is not verified automatically. No calibration is applied.'
        if dataset:
            message=f"Public-sample benchmark: {str(dataset.get('name',''))[:150]}. {str(dataset.get('representation',''))[:250]} Training overlap and labels have not been independently audited. These measurements do not establish accuracy on every uploaded image or every type of editing. Scores remain uncalibrated."
        return {'status':'benchmark_available','message':message,'model_sha256':active_hash,'created_at':str(value.get('created_at',''))[:100],'variants':variants,'selective':selective,'generators':groups}
    except (ValueError,KeyError,TypeError,OSError):
        return {'status':'invalid_benchmark','message':'The configured benchmark could not be validated. Run evaluate.py to generate a fresh result.','variants':{}}
