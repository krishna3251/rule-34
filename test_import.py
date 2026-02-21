import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor'))
import google.generativeai as genai
print("google.generativeai OK:", genai.__version__ if hasattr(genai, '__version__') else "imported")
import aiosqlite
print("aiosqlite OK:", aiosqlite.__version__)
