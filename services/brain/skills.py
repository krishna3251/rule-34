"""Small exact local operations; open conversation belongs to language models."""
import ast
from collections import OrderedDict
import math
import operator
import re
import time


class LocalSkills:
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                  ast.Mod: operator.mod, ast.Pow: operator.pow}

    def __init__(self):
        self.cache = OrderedDict()
        self.hits = self.misses = 0

    def calculate(self, prompt):
        match = re.fullmatch(r'(?:calculate|calc|what is)\s+([\d\s.+*/%()\-]+)\??', prompt.strip(), re.I)
        if not match:
            return None
        expression = match[1].strip()
        cached = self.cache.get(expression)
        if cached and time.monotonic() - cached[0] < 300:
            self.hits += 1
            self.cache.move_to_end(expression)
            return cached[1]
        self.misses += 1
        try:
            if len(expression) > 180:
                raise ValueError
            tree = ast.parse(expression, mode='eval')
            if len(list(ast.walk(tree))) > 50:
                raise ValueError

            def evaluate(node):
                if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                    result = node.value
                elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                    result = evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
                elif isinstance(node, ast.BinOp) and type(node.op) in self.operations:
                    left, right = evaluate(node.left), evaluate(node.right)
                    if isinstance(node.op, ast.Pow) and abs(right) > 12:
                        raise ValueError
                    result = self.operations[type(node.op)](left, right)
                else:
                    raise ValueError
                if not isinstance(result, (int, float)) or not math.isfinite(result) or abs(result) > 1e15:
                    raise ValueError
                return result

            result = f'{evaluate(tree.body):g}'
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
            return 'That calculation is invalid or exceeds the calculator limits.'
        self.cache[expression] = (time.monotonic(), result)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        return result


STYLES = {
    'adaptive': 'Match the task and language; let the existing character voice guide casual chat.',
    'casual': 'Use relaxed everyday language.',
    'friendly': 'Be warm without flattery.',
    'playful': 'Use light wit only when the conversation welcomes it.',
    'sarcastic': 'Use mild dry humor; never mock distress or the person asking for help.',
    'serious': 'Be calm, considerate and direct.',
    'technical': 'Explain precisely, with examples when useful.',
    'helpful': 'Lead with the useful answer and concrete next steps.',
    'concise': 'Use the fewest words that answer the request well.',
}
