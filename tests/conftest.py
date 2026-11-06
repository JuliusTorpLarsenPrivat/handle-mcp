import pytest


@pytest.fixture(autouse=True)
def egen_datamappe(tmp_path, monkeypatch):
    """Ingen test må røre de rigtige filer i %LOCALAPPDATA%/handle-mcp."""
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path / "handle-mcp-data"))
