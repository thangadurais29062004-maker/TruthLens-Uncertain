# Community Forensics image classifier

Authors: Jeongsoo Park and Andrew Owens, University of Michigan.
Paper: Community Forensics: Using Thousands of Generators to Train Fake Image Detectors, CVPR 2025.
Official source: https://github.com/JeongsooP/Community-Forensics
Checkpoint: https://huggingface.co/OwensLab/commfor-model-224/tree/26afc31e6b40c312c3fd42c05a758be62446215b

The bundled ONNX graph was built locally from the pinned original safetensors checkpoint, preserving its single-output classifier. It was numerically compared with an independent NumPy implementation on a known real image and a constant image; maximum absolute logit difference was below 0.00002. Preprocessing follows the authors' shorter-edge resize to 256, center crop to 224, bilinear interpolation and ImageNet normalization.

The third-party CommunityForensics ONNX candidate tested earlier was rejected after poor pilot results. It is not included or active.

Model scope: AI-generated imagery versus real photographs. This is not a universal Photoshop, face-swap or factual-truth detector. Predictions can be wrong. Scores are uncalibrated. The 600-image local benchmark uses dataset-viewer cached JPEG previews from TheKernel01/AIGC-Detection-Benchmark at revision c91d9024a5a77ef06e2ec681b53f9caf08675663. Training overlap and label accuracy have not been independently audited. Dataset images are not redistributed in this project package.

Reproduction: install onnx 1.19.1 in a separate build environment, download the checkpoint above, and run tools/export_community_model.py CHECKPOINT --output models/official-community-224.onnx. onnx is a build tool; the application needs only its existing onnxruntime dependency.

## License

MIT License

Copyright (c) 2025 Jeongsoo Park

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
