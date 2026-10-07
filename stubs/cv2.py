# -*- coding: utf-8 -*-
"""★ 极简 cv2 替身（stub）—— 只为让 windows-capture 能 import

为什么需要：windows-capture 的 METADATA 把 opencv-python 列为**硬依赖**，
它的 __init__.py 第 7 行就 `import cv2`；但全库对 cv2 的**唯一用法**是
  cv2.imwrite(path, frame)
—— 也就是"把帧存成图"。抓屏本身完全不需要 OpenCV。

所以这里只实现 imwrite（转交给 PIL），不引入 40+ MB 的 OpenCV。
⛔ 若将来发现别的代码路径也调用 cv2，必须立刻改成装真的 opencv-python。
"""
import numpy as _np
from PIL import Image as _Image


def imwrite(path, img, *a, **kw):
    arr = _np.asarray(img)
    if arr.ndim == 3 and arr.shape[2] == 3:
        arr = arr[..., ::-1]          # BGR -> RGB
    _Image.fromarray(arr).save(path)
    return True
