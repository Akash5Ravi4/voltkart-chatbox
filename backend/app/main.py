from fastapi import FastAPI
from dotenv import load_dotenv

# Reads GROQ_API_KEY etc. from the .env file
load_dotenv()

app = FastAPI(title="VOLTKART CHATBOX")


@app.get("/health")
def health():
    return {"status": "ok"}