# AI model notices

These TensorFlow.js graph models run in the visitor's browser (web/static/ai.js).
They were taken from the npm package `@plotdb/upscaler` 0.0.3 (ISC), which ships
models converted from PyTorch by xororz (https://github.com/xororz/web-realesrgan).

| Folder | Original model | Original license |
|---|---|---|
| `realesrgan/general_fast-64` | Real-ESRGAN realesr-general-x4v3 | BSD-3-Clause — https://github.com/xinntao/Real-ESRGAN |
| `realesrgan/anime_fast-64` | Real-ESRGAN realesr-animevideov3 | BSD-3-Clause — https://github.com/xinntao/Real-ESRGAN |
| `realcugan/*` | Real-CUGAN (bilibili) | MIT — https://github.com/bilibili/ailab/tree/main/Real-CUGAN |

The TensorFlow.js conversions come from web-realesrgan, which is GPL-licensed:
https://github.com/xororz/web-realesrgan

`../vendor/tf.min.js` and `../vendor/tf-backend-webgpu.min.js` are TensorFlow.js 4.22.0
(Apache-2.0, https://github.com/tensorflow/tfjs).
