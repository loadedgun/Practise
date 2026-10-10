# -*- coding: utf-8 -*-
"""pytest 全局配置：将 scripts/ 加入 sys.path，使 `import normalize` 等顶层导入可用。"""
import os
import sys

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
