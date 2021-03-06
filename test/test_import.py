import hyper
import imp
import pytest
import sys


class TestImportPython2(object):
    def test_cannot_import_python_2(self, monkeypatch):
        monkeypatch.setattr(sys, 'version_info', (2, 7, 7, 'final', 0))
        with pytest.raises(ImportError):
            imp.reload(hyper)


class TestImportPython3(object):
    def test_cannot_import_python_32(self, monkeypatch):
        monkeypatch.setattr(sys, 'version_info', (3, 2, 3, 'final', 0))
        with pytest.raises(ImportError):
            imp.reload(hyper)
