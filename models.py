from google import genai

client = genai.Client(api_key="Test you api")

for m in client.models.list():
    print(m.name)