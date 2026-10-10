# TruthLens 2.4: image prediction benchmark

Updated 10 October 2026 (Asia/Calcutta).

The app now predicts **Likely real photograph**, **Likely AI-generated**, or **Uncertain**. This addresses AI-generated imagery versus photographs. It does not cover every Photoshop edit, face swap or misleading caption.

## What changed

The image classifier now uses the original Community Forensics ViT-Small 224 checkpoint from the research authors. The old face-only detector remains separate for video inspection. The authors' checkpoint was converted locally to ONNX, with pinned SHA-256 checks and a numerical comparison against an independent NumPy implementation. Maximum logit discrepancy on two conversion-check inputs was below 0.00002.

For each image, inference runs on its original decoded pixels, a JPEG-quality-60 version and a half-resolution version. Likely real requires all scores at or below 0.2. Likely AI-generated requires all scores at or above 0.8. Other cases remain uncertain. Images smaller than 128 pixels on either axis or with aspect ratio above 3 also remain uncertain. The model resizes and center-crops; borders may be excluded. These are repeated checks of one model, not three independently trained models. Scores are uncalibrated.

## Measured results

The test contains 600 cached JPEG previews: 306 labeled real and 294 labeled AI-generated. A fixed seed selected 60 disjoint random 100-row blocks, with ten random rows from each. It is a clustered subset, not the full benchmark. No model training or threshold fitting was performed on this sample. An initial third-party converted candidate was rejected after a separate poor pilot.

| Measurement | Result |
|---|---:|
| Original-input binary accuracy at threshold 0.5 | 89.5% |
| JPEG-quality-60 binary accuracy | 80.0% |
| Half-resolution binary accuracy | 88.3% |
| Original false positives / false negatives | 9 / 54 |
| Original AI-class precision / recall | 96.4% / 81.6% |
| Three-view decided / uncertain | 448 / 152 |
| Decision coverage | 74.7% |
| Accuracy among decided predictions | 92.4% |
| Likely-real prediction precision | 89.1% (270/303) |
| Likely-AI prediction precision | 99.3% (144/145) |

The entire benchmark was rerun through the application's production inference/evaluation code. It reproduced the prototype's per-variant and three-view metrics exactly.

## Interpretation

92.4% does **not** mean 92.4% correct on every upload: it describes only the 448 decided images in this sample. The other 152 were uncertain. The likely-real label still incorrectly included 33 AI-generated samples. The likely-AI label incorrectly included one real sample. Dataset measurements are not confidence in a particular upload.

Performance differs by generator. Only 8 of 32 sampled Midjourney images were correctly classified at threshold 0.5. Some generator groups have very few examples and cannot support dependable estimates. JPEG recompression materially reduced accuracy.

Training overlap, label correctness and source-image independence have not been independently audited. Cached JPEG previews differ from original source files. This benchmark does not establish universal, forensic or production-wide accuracy, and it does not evaluate videos or news.

## How to use

Open the local app, select New investigation > Image, upload an image and click Analyze content. Read the predicted label, three scores, benchmark scope and evidence. Existing reports retain their original model outputs; submit the original image again for the upgraded classifier.

Model & methods displays binary accuracy, false positives, false negatives, precision, recall, decision coverage and generator weaknesses. PDF and JSON reports include the image prediction and its limitations.

## Reproducibility

The project includes models/image-detector.json, models/COMMUNITY-FORENSICS-NOTICE.md, tools/export_community_model.py, benchmarks/image-benchmark.json, the public-sample manifest and per-image scores. Benchmark validity is bound to the model bytes, configuration and prediction-pipeline checksum. Benchmark images remain in the workspace's work/public-image-benchmark folder and are not redistributed in the ZIP.

With the workspace dataset present, run from the project folder:

```powershell
.venv/Scripts/python.exe evaluate.py ../../work/public-image-benchmark/manifest.csv --output ../../work/reproduced-benchmark.json
```

Sources: [Official checkpoint](https://huggingface.co/OwensLab/commfor-model-224), [authors' code and paper](https://github.com/JeongsooP/Community-Forensics), [public benchmark mirror](https://huggingface.co/datasets/TheKernel01/AIGC-Detection-Benchmark).
