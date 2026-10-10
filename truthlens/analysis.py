from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import warnings
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
from .evidence import (visual_evidence, detect_faces, face_crop, annotate_faces,
                       associate_faces, compare_images, provenance, extract_claims, lookup_claims)

ROOT = Path(__file__).resolve().parent.parent
Image.MAX_IMAGE_PIXELS = 20_000_000
FEATURE_NAMES = ['ela_mean', 'ela_p95', 'laplacian_variance', 'noise_std', 'saturation_mean']


class AnalysisError(ValueError):
    pass


class Cancelled(AnalysisError):
    pass


def model_config_path(kind='image'):
    default = ROOT / 'models/onnx.json'
    if kind == 'image' and (ROOT / 'models/image-detector.json').is_file():
        default = ROOT / 'models/image-detector.json'
    override = 'TRUTHLENS_VIDEO_ONNX_CONFIG' if kind == 'video' and 'TRUTHLENS_VIDEO_ONNX_CONFIG' in os.environ else 'TRUTHLENS_ONNX_CONFIG'
    return os.environ.get(override, str(default) if default.is_file() else '')


def notify(progress, value, stage):
    if progress:
        progress(value, stage)


def finding(name, status, detail, value=None):
    return dict(name=name, status=status, detail=detail, value=value)


def base_report(kind, name, data):
    return dict(kind=kind, name=name[:160], created_at=datetime.now(timezone.utc).isoformat(),
                sha256=hashlib.sha256(data).hexdigest(), findings=[], metrics={},
                model=None, verdict='Inconclusive',
                summary='These observations cannot establish whether the content is authentic.',
                limitations=[], next_steps=[])


def load_image(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise AnalysisError('Use a JPEG, PNG, or WebP image.')
            image.load()
            meta = dict(format=image.format, width=image.width, height=image.height,
                        exif_present=bool(image.getexif()), software=str(image.getexif().get(305, ''))[:160])
            return ImageOps.exif_transpose(image).convert('RGB'), meta
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AnalysisError('The image is invalid or exceeds the 20-megapixel safety limit.') from exc


def features(image):
    image = image.copy()
    image.thumbnail((768, 768))
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=90)
    with Image.open(io.BytesIO(buffer.getvalue())) as recompressed:
        diff = np.asarray(ImageChops.difference(image, recompressed.convert('RGB')), dtype=np.float32)
    gray = np.asarray(image.convert('L'), dtype=np.float32)
    if min(gray.shape) < 3:
        raise AnalysisError('Image dimensions must be at least 3 × 3 pixels.')
    lap = -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    hsv = np.asarray(image.convert('HSV'), dtype=np.float32)
    return np.array([diff.mean(), np.percentile(diff, 95), lap.var(), lap.std(), hsv[:, :, 1].mean()], dtype=np.float64)


def feature_prediction(image):
    path = Path(os.environ.get('TRUTHLENS_FEATURE_MODEL', ROOT / 'models/feature-model.json'))
    if not path.is_file():
        return None
    model = json.loads(path.read_text(encoding='utf-8'))
    if model.get('features') != FEATURE_NAMES:
        raise AnalysisError('The feature model uses an unsupported feature schema.')
    vec = features(image)
    mean, scale, weights = [np.asarray(model[key], dtype=float) for key in ('mean', 'scale', 'weights')]
    if any(x.shape != (5,) or not np.isfinite(x).all() for x in (mean, scale, weights)) or np.any(scale <= 0):
        raise AnalysisError('Invalid feature-model parameters.')
    z = float(((vec - mean) / scale) @ weights + float(model['bias']))
    probability = float(1 / (1 + np.exp(-np.clip(z, -40, 40))))
    return dict(name=model.get('name', 'Feature classifier'), method='Logistic regression on forensic features',
                manipulated_probability=round(probability, 4), scope='Whole image; domain-dependent and uncalibrated',
                evaluation=model.get('evaluation'), note='A model output is not proof of manipulation.')


@lru_cache(maxsize=2)
def onnx_session(model_path, modified):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    return ort.InferenceSession(model_path, sess_options=options, providers=['CPUExecutionProvider'])


@lru_cache(maxsize=4)
def checked_model_hash(path, modified, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_onnx_image(image, config):
    size = int(config.get('size', 224))
    if not 16 <= size <= 1024:
        raise AnalysisError('ONNX input size must be between 16 and 1024.')
    method = config.get('resample', 'bicubic')
    resampling = Image.Resampling.BILINEAR if method == 'bilinear' else Image.Resampling.BICUBIC
    shorter = config.get('resize_shorter')
    if shorter is not None:
        shorter = int(shorter)
        if not size <= shorter <= 2048:
            raise AnalysisError('Invalid shorter-edge resize.')
        w, h = image.size
        target = (shorter, int(shorter*h/w)) if w < h else (int(shorter*w/h), shorter)
        if max(target) > 16384:
            raise AnalysisError('Image aspect ratio is too extreme for this model.')
        image = image.resize(target, resampling)
        left, top = round((image.width-size)/2), round((image.height-size)/2)
        return image.crop((left, top, left+size, top+size))
    return image.resize((size, size), resampling)


def onnx_prediction(image, config_path=None):
    config_path = model_config_path() if config_path is None else config_path
    if not config_path:
        return None
    config_file = Path(config_path).resolve()
    config = json.loads(config_file.read_text(encoding='utf-8'))
    model_path = (config_file.parent / config['model_path']).resolve()
    stat = model_path.stat()
    if config.get('sha256') and checked_model_hash(str(model_path), stat.st_mtime_ns, stat.st_size) != config['sha256']:
        raise AnalysisError('Model checksum does not match its pinned configuration.')
    tensor = np.asarray(prepare_onnx_image(image, config), dtype=np.float32) / 255.0
    mean = np.asarray(config.get('mean', [.485, .456, .406]), dtype=np.float32)
    std = np.asarray(config.get('std', [.229, .224, .225]), dtype=np.float32)
    if mean.shape != (3,) or std.shape != (3,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or np.any(std <= 0):
        raise AnalysisError('ONNX normalization requires three finite means and positive standard deviations.')
    tensor = ((tensor - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
    session = onnx_session(str(model_path), model_path.stat().st_mtime_ns)
    if len(session.get_inputs()) != 1:
        raise AnalysisError('The ONNX adapter supports a single RGB NCHW input.')
    output = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0], dtype=float).ravel()
    if not np.isfinite(output).all():
        raise AnalysisError('The ONNX model returned nonfinite output.')
    activation = config.get('activation', 'softmax')
    index = int(config.get('manipulated_index', 1))
    if activation == 'sigmoid' and output.size == 1 and index == 0:
        probability = float(1 / (1 + np.exp(-np.clip(output[0], -40, 40))))
    elif activation == 'softmax' and output.size == 2 and index in (0, 1):
        exps = np.exp(output - output.max())
        probability = float((exps / exps.sum())[index])
    elif activation == 'probabilities' and output.size in (1, 2) and 0 <= index < output.size and np.all((output >= 0) & (output <= 1)) and (output.size == 1 or np.isclose(output.sum(), 1, atol=.001)):
        probability = float(output[index])
    else:
        raise AnalysisError('ONNX output does not match the configured binary classification contract.')
    return dict(name=config.get('name', 'ONNX detector'), method='Deep learning / ONNX',
                manipulated_probability=probability if config.get('prediction_task') == 'ai_generation' else round(probability, 4),
                prediction_task=config.get('prediction_task'),
                scope=config.get('scope', 'Resized whole image, not face crops'),
                source=config.get('source'), revision=config.get('revision'), license=config.get('license'),
                evaluation_status=config.get('evaluation_status', 'No independent evaluation supplied'),
                note='Interpret only within the model’s documented training domain; this is not a calibrated confidence.')


def detector_prediction(image, boxes=None, kind='image'):
    config_path = model_config_path(kind)
    if config_path:
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
        if config.get('domain') == 'faces':
            boxes = detect_faces(image) if boxes is None else boxes
            predictions = []
            for box in boxes[:3]:
                prediction = onnx_prediction(face_crop(image, box), config_path)
                box['model_probability'] = prediction['manipulated_probability']
                predictions.append(prediction)
            if not predictions:
                return None
            return {**predictions[0], 'manipulated_probability':round(float(np.mean([p['manipulated_probability'] for p in predictions])), 4),
                    'scope':'Mean output across detected face crops; geometric face detection can miss faces or produce false detections',
                    'faces_analyzed':len(predictions), 'face_predictions':[dict(box) for box in boxes]}
        return onnx_prediction(image, config_path)
    return feature_prediction(image)


def image_report(data, name, progress=None, reference_data=None):
    notify(progress, 10, 'Decoding image')
    image, meta = load_image(data)
    values = features(image)
    report = base_report('image', name, data)
    report['metrics'] = {**meta, **{k: round(float(v), 3) for k, v in zip(FEATURE_NAMES, values)}}
    report['findings'] = [
        finding('File structure', 'observed', f"Decoded {meta['format']} image, {meta['width']} × {meta['height']} pixels."),
        finding('Editing metadata', 'review' if meta['software'] else 'observed',
                f"Software tag: {meta['software']}. Editing software does not establish deceptive manipulation." if meta['software'] else
                'No editing-software tag was found. Missing metadata is common and is not evidence of authenticity.'),
        finding('Recompression residual', 'observed', 'Mean pixel difference after JPEG recompression at quality 90. Texture, prior compression, and format conversion affect this value.', round(float(values[0]), 3)),
        finding('Fine-detail variation', 'observed', 'Laplacian variance measures image detail. Blur, texture, sharpening, and compression affect it; it cannot identify a deepfake.', round(float(values[2]), 3))]
    notify(progress, 30, 'Building visual evidence')
    report['visuals'] = visual_evidence(image)
    faces = detect_faces(image)
    report['metrics']['faces_detected'] = len(faces)
    notify(progress, 50, 'Running image detector')
    report['model'] = detector_prediction(image, faces)
    if report['model'] and report['model'].get('prediction_task') == 'ai_generation':
        from .prediction import image_prediction
        report['prediction'] = image_prediction(image, report['model'], detector_prediction)
        from .validation import status as benchmark_status
        benchmark = benchmark_status(ROOT)
        report['prediction']['benchmark'] = {'status':benchmark['status'], 'message':benchmark['message'],
            'selective':benchmark.get('selective'), 'original':benchmark.get('variants',{}).get('original')}
        report['verdict'] = report['prediction']['label']
        report['summary'] = report['prediction']['explanation']
    if faces:
        report['visuals']['face_preview'] = annotate_faces(image, faces)
    report['faces'] = faces
    notify(progress, 75, 'Checking Content Credentials')
    report['provenance'] = provenance(data, {'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[meta['format']])
    if reference_data:
        reference, _ = load_image(reference_data)
        report['comparison'] = compare_images(image, reference)
        report['comparison']['reference_sha256'] = hashlib.sha256(reference_data).hexdigest()
    report['limitations'] = ['Residuals and metadata cannot reliably distinguish manipulation from ordinary editing.',
                             'The pretrained detector targets face images and is not independently validated or calibrated by TruthLens.',
                             'If no face is detected, the face-domain detector is skipped. Credentials establish provenance, not factual truth.']
    if report.get('prediction'):
        report['limitations'] = [
            'The classifier distinguishes AI-generated imagery from photographs; it does not detect every Photoshop edit, face swap, or misleading context.',
            'Scores are uncalibrated. Likely real does not establish an untouched original or factual truth.',
            'The center crop excludes image borders. New generators, screenshots, illustrations and distribution shifts can defeat the detector.',
            'Public benchmark measurements apply to sampled dataset-viewer JPEG previews; training overlap has not been independently audited.']
    report['next_steps'] = ['Find the earliest available original image and compare it.', 'Check the publisher and context; use a reverse-image search.', 'Request the original file or provenance credentials when possible.']
    return report


def video_report(path, name, digest, progress=None):
    try:
        import cv2
    except ImportError as exc:
        raise AnalysisError('Video analysis requires OpenCV. Install requirements.txt first.') from exc
    cap = cv2.VideoCapture(str(path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not cap.isOpened() or not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise AnalysisError('Cannot decode this video. Use a valid MP4, MOV, or WebM file.')
        if width * height > 8_300_000 or count / fps > 120:
            raise AnalysisError('Use a video up to 2 minutes and 4K resolution.')
        samples, previous, differences, blur, predictions = [], None, [], [], []
        previous_faces, next_id = [], 1
        indices = np.unique(np.linspace(0, count - 1, min(24, count), dtype=int))
        for sample_index, index in enumerate(indices):
            notify(progress, 10 + int(65 * sample_index / len(indices)), f'Inspecting frame {sample_index + 1} of {len(indices)}')
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, (256, 144))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            detail = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            delta = float(np.mean(np.abs(gray.astype(float) - previous))) if previous is not None else None
            if delta is not None:
                differences.append(delta)
            blur.append(detail)
            samples.append(dict(time=round(int(index) / fps, 2), detail=round(detail, 2), difference=round(delta, 2) if delta is not None else None))
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            boxes = detect_faces(image)
            next_id = associate_faces(boxes, previous_faces, next_id)
            prediction = detector_prediction(image, boxes, kind='video')
            samples[-1]['faces'] = boxes
            samples[-1]['thumbnail'] = annotate_faces(image, boxes)
            previous_faces = boxes
            if prediction:
                predictions.append(prediction)
                samples[-1]['model_probability'] = prediction['manipulated_probability']
            previous = gray.astype(float)
        if not samples:
            raise AnalysisError('No frames could be decoded from this video.')
        # Inspect short consecutive windows around the largest sampled visual changes.
        windows = []
        largest = sorted([sample for sample in samples if sample['difference'] is not None], key=lambda sample: sample['difference'], reverse=True)[:3]
        for location in largest:
            notify(progress, 78, 'Inspecting consecutive frames around visual changes')
            center = round(location['time'] * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, center - 2))
            prior, changes = None, []
            for _ in range(5):
                ok, frame = cap.read()
                if not ok:
                    break
                gray = cv2.cvtColor(cv2.resize(frame, (256, 144)), cv2.COLOR_BGR2GRAY).astype(float)
                if prior is not None:
                    changes.append(round(float(np.abs(gray - prior).mean()), 2))
                prior = gray
            windows.append({'center_seconds':location['time'], 'consecutive_frame_differences':changes,
                            'interpretation':'Motion or scene-cut signal; not a temporal deepfake prediction'})
        report = base_report('video', name, b'')
        report['sha256'] = digest
        report['metrics'] = dict(width=width, height=height, fps=round(fps, 2), duration_seconds=round(count / fps, 2), frames_sampled=len(samples), samples=samples,
                                face_tracks=max(0,next_id-1), temporal_windows=windows)
        report['findings'] = [finding('Video structure', 'observed', f'{len(samples)} frames sampled uniformly across {count / fps:.1f} seconds.'),
                              finding('Visual changes', 'observed', 'Mean grayscale change between widely spaced samples. Scene cuts and camera motion can cause large changes.', round(float(np.mean(differences)), 2) if differences else 0),
                              finding('Detail variation', 'observed', 'Changes in frame detail may reflect focus, lighting, resolution, or encoding.', round(float(np.std(blur)), 2))]
        if predictions:
            report['model'] = {**predictions[0], 'manipulated_probability': round(float(np.mean([p['manipulated_probability'] for p in predictions])), 4),
                               'scope': 'Mean image-detector output across sampled frames with eligible detections; not a temporal video classifier',
                               'frames_scored':len(predictions)}
        notify(progress, 88, 'Checking video provenance')
        mime = 'video/mp4' if path.suffix.lower() in ('.mp4','.mov') else 'video/webm' if path.suffix.lower() == '.webm' else 'video/x-msvideo'
        report['provenance'] = provenance(path.read_bytes(), mime)
        report['limitations'] = ['Sparse frame sampling can miss brief manipulations.', 'No audio, lip-sync, or facial-identity recognition is performed.',
                                 'Track IDs use geometric overlap only. They can change after motion or cuts and do not identify a person.',
                                 'Consecutive-frame measurements are not a trained temporal detector. Face-image models may be unsuitable for some video domains.']
        report['next_steps'] = ['Compare with an original recording from a reliable source.', 'Examine suspicious segments frame by frame and listen for audio edits.', 'Verify the statement with the speaker’s official channels.']
        return report
    finally:
        cap.release()


def news_report(text, name, source_url='', online=False, progress=None):
    notify(progress, 15, 'Extracting language cues and claim candidates')
    if len(text.strip()) < 40:
        raise AnalysisError('Paste at least 40 characters of article text.')
    if len(text) > 50_000:
        raise AnalysisError('Article text must be under 50,000 characters.')
    report = base_report('news', name or 'Article analysis', text.encode('utf-8'))
    words = re.findall(r"\b[\w’'-]+\b", text)
    phrases = ['shocking', 'you won’t believe', "you won't believe", 'they don’t want you to know', 'secret cure', 'miracle', 'guaranteed', 'everyone knows', 'wake up']
    matches = [phrase for phrase in phrases if phrase in text.lower()]
    attribution = re.findall(r'\b(?:according to|reported by|published in|said|researchers|study|data from)\b', text, re.I)
    links = re.findall(r'https?://[^\s<>]+', text)
    uppercase = [w for w in words if len(w) > 3 and w.isupper()]
    report['metrics'] = dict(word_count=len(words), sensational_phrases=matches, attribution_cues=len(attribution), links_in_text=len(links), uppercase_words=len(uppercase), source_url=source_url[:2048])
    report['findings'] = [
        finding('Emotionally loaded language', 'review' if matches else 'observed', 'Matched phrases: ' + ', '.join(matches) + '. These may appear in quotations or legitimate reporting.' if matches else 'No phrases in the small English-language lexicon were matched.'),
        finding('Attribution cues', 'observed' if attribution else 'review', f'{len(attribution)} attribution cues found. Attribution wording does not establish that a source exists or supports the claim.'),
        finding('Linked evidence', 'observed' if links else 'review', f'{len(links)} links found in the pasted text. Links have not been fetched or verified.'),
        finding('Emphasis', 'review' if len(uppercase) > 3 or text.count('!') > 3 else 'observed', f'{len(uppercase)} uppercase words and {text.count("!")} exclamation marks. Style is not a truth verdict.')]
    report['summary'] = 'Language cues for editorial review. The factual claims remain unverified.'
    report['claims'] = extract_claims(text)
    report['highlighted_passages'] = [{'text':m.group(), 'start':m.start(), 'end':m.end(), 'reason':'Loaded-language lexicon match'} for phrase in matches for m in re.finditer(re.escape(phrase), text, re.I)][:30]
    report['lookup'] = lookup_claims(report['claims'], os.environ.get('GOOGLE_FACT_CHECK_API_KEY',''), progress) if online else {'status':'disabled', 'note':'Online lookup was not enabled. No claim text was transmitted.'}
    report['limitations'] = ['English-language rule-based claim extraction and language cues; important claims may be missed.',
                             'Retrieved reviews are candidate sources. Their ratings are not applied automatically to a different claim.',
                             'No match means insufficient evidence. Language style cannot determine whether a claim is true.']
    report['next_steps'] = ['Identify the article’s specific claims and check their primary evidence.', 'Check the author, publication date, and cited sources.', 'Compare coverage from independent outlets and relevant fact-checkers.']
    return report
