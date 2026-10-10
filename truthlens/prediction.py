"""Conservative AI-generation classification; raw scores are not confidence."""
import io
import math
from PIL import Image

REAL_THRESHOLD = .2
FAKE_THRESHOLD = .8


def decide(scores, size):
    if len(scores) != 3 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in scores):
        return 'uncertain', 'Invalid or incomplete detector outputs.'
    width, height = size
    if min(width, height) < 128 or max(width, height)/min(width, height) > 3:
        return 'uncertain', 'This image is too small or too panoramic for the tested crop-based workflow.'
    if max(scores) <= REAL_THRESHOLD:
        return 'likely_real', 'All three image checks favour a real photograph over AI-generated imagery.'
    if min(scores) >= FAKE_THRESHOLD:
        return 'likely_ai', 'All three image checks favour AI-generated imagery over a real photograph.'
    return 'uncertain', 'The scores are ambiguous or change after compression/resizing. A dependable binary prediction is unavailable.'


def image_prediction(image, original, predictor):
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=60)
    with Image.open(io.BytesIO(buffer.getvalue())) as compressed:
        jpeg = compressed.convert('RGB')
    half = image.resize((max(3,image.width//2),max(3,image.height//2)), Image.Resampling.BILINEAR)
    results = [original, predictor(jpeg), predictor(half)]
    scores = [r['manipulated_probability'] if r else float('nan') for r in results]
    code, explanation = decide(scores, image.size)
    labels = {'likely_real':'Likely real photograph','likely_ai':'Likely AI-generated','uncertain':'Uncertain'}
    return {'code':code, 'label':labels[code], 'explanation':explanation,
            'task':'Real photograph versus AI-generated image',
            'scores':{name:float(p) if math.isfinite(p) else None for name,p in zip(('original','jpeg_quality_60','half_resolution'),scores)},
            'thresholds':{'real_max':REAL_THRESHOLD,'ai_min':FAKE_THRESHOLD},
            'policy':'All three outputs must agree beyond fixed thresholds; otherwise abstain. No threshold fitting on the benchmark.',
            'notice':'A model prediction can be wrong. Scores are not calibrated probabilities. This does not verify every edit or the story attached to an image.'}
