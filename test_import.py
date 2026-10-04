from google import genai
from google.genai import types

print("google.genai OK:", genai.__name__)
print("GenerateContentConfig OK:", types.GenerateContentConfig.__name__)

try:
    import aiosqlite
    print("aiosqlite OK:", getattr(aiosqlite, "__version__", "imported"))
except ImportError as exc:
    print("aiosqlite import failed:", exc)
