"""Source-only dependency inventory; never opens runtime databases or vendor data."""
import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = [root/name for name in ('main.py','database.py','keep_alive.py')]
paths += sorted((root/'cogs').rglob('*.py')) + sorted((root/'services').rglob('*.py'))
modules = {}
for path in paths:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add('.'*node.level + node.module)
    modules[str(path.relative_to(root))] = sorted(imports)
output = {'entry_point':'main.py', 'extension_discovery':'cogs/*.py with a setup function, loaded dynamically by MikoBot',
          'dependencies': modules,
          'tests': [str(p.relative_to(root)) for p in sorted((root/'tests').rglob('test_*.py'))]}
(root/'docs').mkdir(exist_ok=True)
(root/'docs/module_dependencies.json').write_text(json.dumps(output,indent=2)+'\n')
print(f'Inventoried {len(modules)} application modules')
