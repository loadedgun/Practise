# -*- coding: utf-8 -*-
"""docx_extract.extract_docx_text 测试：删除线检测有多种等价形式
（自闭合 <w:strike/>、显式 true、显式 false/0 关闭），且需正确反转义 XML 实体。
删除线标记直接影响需求评审中「废弃范围」的识别，错判会污染范围演进结论。

docx 本质是 zip，这里用标准库 zipfile 在 tmp_path 内构造最小 docx 包，不依赖真实 Word 文件。
"""
import io
import zipfile

import pytest

import docx_extract


def _make_docx(tmp_path, document_xml, name='t.docx'):
    """构造最小 docx：仅包含 word/document.xml，返回路径。"""
    path = tmp_path / name
    with zipfile.ZipFile(str(path), 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml', document_xml)
    return str(path)


class TestExtractDocxText:
    def test_plain_paragraphs_extracted(self, tmp_path):
        xml = ('<w:p><w:r><w:t>需求一</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>需求二</w:t></w:r></w:p>')
        out = docx_extract.extract_docx_text(_make_docx(tmp_path, xml))
        assert '需求一' in out
        assert '需求二' in out
        assert '[删除线]' not in out

    def test_self_closing_strike_flagged(self, tmp_path):
        # <w:strike/> 自闭合标签 → 视为删除线
        xml = '<w:p><w:r><w:strike/></w:r><w:t>废弃需求</w:t></w:p>'
        out = docx_extract.extract_docx_text(_make_docx(tmp_path, xml))
        assert '[删除线] 废弃需求' in out

    def test_explicit_true_strike_flagged(self, tmp_path):
        xml = '<w:p><w:r><w:strike w:val="true"/></w:r><w:t>废弃</w:t></w:p>'
        out = docx_extract.extract_docx_text(_make_docx(tmp_path, xml))
        assert '[删除线] 废弃' in out

    def test_explicit_false_strike_not_flagged(self, tmp_path):
        # 显式 false / 0 → 关闭删除线，不加前缀
        for val in ('false', '0'):
            xml = '<w:p><w:r><w:strike w:val="%s"/></w:r><w:t>正常</w:t></w:p>' % val
            out = docx_extract.extract_docx_text(_make_docx(tmp_path, xml, 't_%s.docx' % val))
            assert '[删除线]' not in out
            assert '正常' in out

    def test_mixed_strikethrough_and_normal(self, tmp_path):
        xml = ('<w:p><w:r><w:strike/></w:r><w:t>废弃</w:t></w:p>'
               '<w:p><w:r><w:t>保留</w:t></w:r></w:p>')
        lines = docx_extract.extract_docx_text(_make_docx(tmp_path, xml)).split('\n')
        assert any(l.startswith('[删除线]') and l.endswith('废弃') for l in lines)
        assert any(l == '保留' for l in lines)

    def test_xml_entities_unescaped(self, tmp_path):
        xml = '<w:p><w:r><w:t>A &amp; B &lt; C &gt; D &quot;E&quot; &apos;F&apos;</w:t></w:r></w:p>'
        out = docx_extract.extract_docx_text(_make_docx(tmp_path, xml))
        assert 'A & B < C > D "E" \'F\'' in out

    def test_empty_paragraph_skipped(self, tmp_path):
        # 纯空白段落不输出
        xml = '<w:p><w:r><w:t>   </w:t></w:r></w:p><w:p><w:r><w:t>正文</w:t></w:r></w:p>'
        out = docx_extract.extract_docx_text(_make_docx(tmp_path, xml))
        assert out.strip() == '正文'

    def test_invalid_docx_missing_document_xml_raises(self, tmp_path):
        path = tmp_path / 'bad.docx'
        with zipfile.ZipFile(str(path), 'w') as z:
            z.writestr('other.txt', 'nope')
        with pytest.raises(RuntimeError, match='不是有效的 .docx'):
            docx_extract.extract_docx_text(str(path))
