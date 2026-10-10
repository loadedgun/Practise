# -*- coding: utf-8 -*-
"""parsers/__init__.py 注册表与平台自动探测测试。

detect_platform 按表头特征自动判定 Jira / 禅道，是 scan 模式路由的关键环节。
get_parser 负责按名取解析器并对未知平台报错。
"""
import pytest

from parsers import detect_platform, get_parser, PARSER_REGISTRY


class TestDetectPlatform:
    def test_jira_detected_by_issue_key(self):
        assert detect_platform(['Issue key', 'Summary', 'Status']) == 'jira'

    def test_jira_detected_by_chinese_key(self):
        assert detect_platform(['问题键', '摘要', '状态']) == 'jira'

    def test_zentao_needs_two_signatures(self):
        # 禅道特征需命中 2 个（字段名较通用）
        assert detect_platform(['Bug标题', '严重程度']) == 'zentao'

    def test_zentao_single_signature_not_enough(self):
        # 仅 1 个禅道特征表头不判定（避免误判通用字段）
        assert detect_platform(['Bug标题', '其他']) is None

    def test_unknown_returns_none(self):
        assert detect_platform(['自定义列A', '自定义列B']) is None

    def test_jira_takes_priority_over_zentao(self):
        # 同时含 Jira 与禅道特征时，Jira 在前先命中
        headers = ['Issue key', 'Bug标题', '严重程度']
        assert detect_platform(headers) == 'jira'


class TestGetParser:
    @pytest.mark.parametrize('name', ['jira', 'zentao', 'custom'])
    def test_known_platforms_registered(self, name):
        assert name in PARSER_REGISTRY

    def test_get_jira_parser(self):
        p = get_parser('jira')
        assert p.name == 'jira'

    def test_get_zentao_parser(self):
        p = get_parser('zentao')
        assert p.name == 'zentao'

    def test_unknown_platform_raises(self):
        with pytest.raises(RuntimeError, match='未知解析平台'):
            get_parser('nonexistent')
