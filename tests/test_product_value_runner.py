"""The pilot fixture makes real faults without model calls or business service changes."""
import importlib.util
from pathlib import Path
from contextlib import ExitStack

def test_value_pilot_uses_real_exceptions_and_restricted_checks():
    spec=importlib.util.spec_from_file_location('value_pilot',Path('scripts/validate_product_value.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with ExitStack() as stack:
        cases={c['id']:c for c in module.fixtures(stack)}
        expected={'api_500_configuration':'HTTP Error 500','dependency_wrong_port':'ConnectionRefusedError','startup_port_conflict':'OSError','startup_invalid_port':'ValueError'}
        for key,exception in expected.items():assert exception in cases[key]['log']
        assert '返回200' in cases['api_500_configuration']['check']()
        assert '端口' in cases['startup_port_conflict']['check']()
        assert '绑定成功' in cases['startup_invalid_port']['check']()
        assert cases['insufficient_500']['check'] is None
