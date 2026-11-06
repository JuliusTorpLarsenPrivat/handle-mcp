import pytest


@pytest.fixture(autouse=True)
def egen_datamappe(tmp_path, monkeypatch):
    """Ingen test må røre de rigtige filer i LOCALAPPDATA/handle-mcp eller logge ind hos butikkerne."""
    monkeypatch.setenv("HANDLE_MCP_DATA", str(tmp_path / "handle-mcp-data"))
    from handle_mcp import ugeplan

    monkeypatch.setattr(ugeplan, "HENT_ORDRER", False)
