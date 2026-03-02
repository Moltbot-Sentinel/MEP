import sys
import os
import google.generativeai as genai

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY not set")
    sys.exit(1)

genai.configure(api_key=api_key)
try:
    model = genai.GenerativeModel('gemini-3.1-pro-preview')
    prompt = sys.stdin.read()
    if not prompt:
        print("Hub Sentinel (Gemini): Ready.")
        sys.exit(0)
    
    response = model.generate_content(prompt)
    print(response.text)
except Exception as e:
    print(f"Gemini Error: {e}")
