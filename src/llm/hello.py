"""
Stage 0 throwaway: prove one word can come back from a model, from this
machine, using nothing but three environment variables. Not used by the
real endpoint -- see src/llm/client.py for that.

Run: python -m dotenv run -- python src/llm/hello.py
(or just `python src/llm/hello.py` if the three LLM_* vars are already
exported in your shell)
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])

res = client.chat.completions.create(
    model=os.environ["LLM_MODEL"],
    messages=[{"role": "user", "content": "Reply with exactly the word: ready"}],
)
print(res.choices[0].message.content)
