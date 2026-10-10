"""Independent labeled-image evaluation. No accuracy claim is inferred without a dataset."""
import argparse
import csv
import io
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from truthlens.analysis import detector_prediction, load_image


def metrics(labels, probabilities):
    y,p=np.asarray(labels),np.asarray(probabilities,dtype=float)
    if not len(y) or set(y.tolist()) != {0,1}:
        raise ValueError('Evaluation requires eligible images from both classes.')
    pred=p>=.5
    tp=int(((pred==1)&(y==1)).sum()); tn=int(((pred==0)&(y==0)).sum())
    fp=int(((pred==1)&(y==0)).sum()); fn=int(((pred==0)&(y==1)).sum())
    order=np.argsort(p); sorted_p=p[order]; ranks=np.empty(len(p),dtype=float)
    i=0
    while i<len(p):
        j=i+1
        while j<len(p) and sorted_p[j]==sorted_p[i]: j+=1
        ranks[order[i:j]]=(i+j-1)/2+1; i=j
    positives=int((y==1).sum()); negatives=len(y)-positives
    auc=(ranks[y==1].sum()-positives*(positives+1)/2)/(positives*negatives)
    ece=0.
    for index in range(10):
        mask=(p>=index/10)&((p<(index+1)/10) if index<9 else (p<=1))
        if mask.any(): ece+=float(mask.mean())*abs(float(p[mask].mean())-float(y[mask].mean()))
    return {'count':len(y),'threshold':.5,'true_positive':tp,'true_negative':tn,'false_positive':fp,'false_negative':fn,
            'accuracy':float((pred==y).mean()),'precision':tp/max(1,tp+fp),'recall':tp/(tp+fn),
            'false_positive_rate':fp/(fp+tn),'roc_auc':float(auc),'brier_score':float(np.mean((p-y)**2)),
            'expected_calibration_error':ece,'note':'Metrics apply only to this provided dataset and eligible detected inputs. No calibration is applied.'}


def evaluate(manifest, include_policy=False):
    from PIL import Image
    with manifest.open(encoding='utf-8',newline='') as handle: rows=list(csv.DictReader(handle))
    seen=set()
    for row in rows:
        digest=hashlib.sha256((manifest.parent/row['path']).read_bytes()).hexdigest()
        if digest in seen: raise ValueError('Duplicate image bytes are not allowed in an independent evaluation.')
        seen.add(digest)
    results={variant:{'labels':[],'probabilities':[],'skipped':0} for variant in ('original','jpeg_quality_60','half_resolution')}
    observations=[]
    for row in rows:
        label=int(row['label'])
        if label not in (0,1): raise ValueError('Labels must be 0 or 1.')
        image,_=load_image((manifest.parent/row['path']).read_bytes())
        scores=[]; supports_policy=False
        for variant,values in results.items():
            candidate=image
            if variant=='jpeg_quality_60':
                buffer=io.BytesIO(); image.save(buffer,format='JPEG',quality=60)
                candidate=Image.open(io.BytesIO(buffer.getvalue())).convert('RGB')
            elif variant=='half_resolution': candidate=image.resize((max(3,image.width//2),max(3,image.height//2)),Image.Resampling.BILINEAR)
            prediction=detector_prediction(candidate)
            if not prediction:
                values['skipped']+=1; scores.append(float('nan')); continue
            supports_policy = supports_policy or prediction.get('prediction_task')=='ai_generation'
            scores.append(prediction['manipulated_probability'])
            values['labels'].append(label); values['probabilities'].append(prediction['manipulated_probability'])
        observations.append({'scores':scores,'label':label,'size':image.size,'supports_policy':supports_policy})
    variants={variant:{'coverage':len(values['labels'])/max(1,len(rows)),'skipped':values['skipped'],
                     'metrics':metrics(values['labels'],values['probabilities']) if set(values['labels'])=={0,1} else None,
                     'warning':'Use independently labeled, source-separated data not used during training.'} for variant,values in results.items()}
    if not include_policy: return variants
    output={'variants':variants}
    if observations and all(item['supports_policy'] for item in observations):
        from truthlens.prediction import decide
        real=[];fake=[]
        for item in observations:
            code,_=decide(item['scores'],item['size'])
            if code=='likely_real': real.append(item['label']==0)
            elif code=='likely_ai': fake.append(item['label']==1)
        accepted=len(real)+len(fake)
        output['selective']={'count':accepted,'uncertain':len(rows)-accepted,'coverage':accepted/len(rows),
            'accuracy':(sum(real)+sum(fake))/max(1,accepted),'real_predictions':len(real),'fake_predictions':len(fake),
            'real_precision':sum(real)/max(1,len(real)),'fake_precision':sum(fake)/max(1,len(fake))}
    return output



if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('manifest',type=Path); parser.add_argument('--output',type=Path,default=Path('evaluation.json'))
    args=parser.parse_args()
    from truthlens.validation import model_hash, config_hash, pipeline_hash
    result={'model_sha256':model_hash(),'config_sha256':config_hash(),'dataset_manifest_sha256':hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            'pipeline_sha256':pipeline_hash(),'created_at':datetime.now(timezone.utc).isoformat(),**evaluate(args.manifest.resolve(),include_policy=True)}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result,indent=2))
