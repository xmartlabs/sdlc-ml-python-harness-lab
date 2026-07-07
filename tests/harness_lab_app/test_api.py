def test_api_module_imports():
    from harness_lab_app import __version__

    assert __version__ == "0.1.0"
