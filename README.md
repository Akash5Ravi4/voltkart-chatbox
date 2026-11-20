# VOLTKART CHATBOX

A full-stack, evidence-based AI chatbot for the VoltKart electronics retailer assignment.

## Project status

Initial setup is in progress. The next milestone is a FastAPI health-check endpoint, followed by persistent chat history.

## Initial dependencies

The starter Python packages are listed in `backend/requirements.txt`.

Activate the virtual environment from the project root in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install or update the packages:

```powershell
python -m pip install -r backend/requirements.txt
```

## Environment variables

Copy `.env.example` to `.env` and replace the placeholder with your own Groq API key. Keep `.env` private; it is excluded from Git.