"""只修CLI nargs导致的port类型装配；官方main/公式原样执行。"""
import importlib.util
import sys
from types import SimpleNamespace
from common import SIM

spec=importlib.util.spec_from_file_location('installed_official_ability',SIM/'Bench2Drive/tools/ability_benchmark.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.main(SimpleNamespace(file=sys.argv[1],result_file=sys.argv[2],host='localhost',port=int(sys.argv[3])))
