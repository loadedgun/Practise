# -*- coding: utf-8 -*-
"""Pytest 公共配置：把 scripts/ 加入 sys.path，使被测模块以「源码目录为包根」的方式导入，
与生产代码中 `import normalize` / `from parsers.base import ...` 的导入语义保持一致。

每个测试通过被测模块返回的句柄操作数据，不依赖外部网络 / 文件系统（除 tmp_path 临时文件外）。
"""
import os
import sys

# 指向项目根下的 scripts/ 目录（与本文件相对位置：../scripts）
_SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, 'scripts'))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
