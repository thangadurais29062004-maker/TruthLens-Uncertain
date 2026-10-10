# Model and provenance resources

- Model: https://huggingface.co/onnx-community/Deep-Fake-Detector-v2-Model-ONNX
- Original fine-tune: https://huggingface.co/prithivMLmods/Deep-Fake-Detector-v2-Model
- Base: Google ViT base patch16, image size 224.
- Declared license: Apache-2.0 (see LICENSE-APACHE-2.0.txt).
- Revision: 4ea3d66dfb1bedca29727c6a0c6fa061d5f3f9c9.
- File: onnx/model_quantized.onnx, redistributed as detector.onnx.
- SHA-256: 3519c22b9695f99ddc00821228eeac91239065a90bfbdb4917858b3ec1dcfc42.
- Class mapping and normalization: this revision's config.json and preprocessor_config.json.

The card's evaluation is author-reported; it is not an independent TruthLens benchmark. Quantization and face cropping can change performance. A known real public NASA astronaut image yielded a misleading manipulated-class score in a software check. This is a research detector; raw outputs remain separate from verdicts. No independent labeled corpus was provided.

C2PA anchors: https://contentcredentials.org/trust/anchors.pem, retrieved 2026-10-09. This is a snapshot; update from the official source under a managed trust-update policy. A signer absent from the snapshot may be valid but untrusted.

Official C2PA API: https://opensource.contentauthenticity.org/docs/tasks/read/
Fact Check API: https://developers.google.com/fact-check/tools/api/reference/rest/v1alpha1/claims/search

The NASA software-check image came from scikit-image v0.19.3 test data and is not bundled with the application. The sample PDF includes a low-resolution verification excerpt, not a benchmark.
