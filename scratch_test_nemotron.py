import json
import os

import requests
from dotenv import load_dotenv

load_dotenv('/Users/amanbhaskar/Agentic & AI Agent Projects/ATLAS/atlas/.env')
api_key = os.getenv('OPENROUTER_API_KEY')

response = requests.post(
  url="https://openrouter.ai/api/v1/chat/completions",
  headers={
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
  },
  data=json.dumps({
    "model": "nvidia/nemotron-3.5-lightning",
    "messages": [
        {
          "role": "user",
          "content": "How many r's are in the word 'strawberry'?"
        }
      ],
    "reasoning": {"enabled": True}
  })
)

data = response.json()
if 'choices' in data:
    msg = data['choices'][0]['message']
    print("====== RESPONSE ======")
    print(msg.get('content'))
    print("====== REASONING ======")
    print(msg.get('reasoning_details'))
else:
    print("Error:", data)
