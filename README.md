# LLM-Powered GitHub Pages Generator API

This FastAPI application uses a Gemini (Google) or OpenRouter LLM to generate HTML/JS/CSS apps, automatically creates a GitHub repository, adds a proper MIT license, commits the code, and optionally enables GitHub Pages. It can also handle attachments and send evaluation data to a provided URL.

---

## Features

- Generate app code from a brief using Gemini LLM.
- Automatically create or update a GitHub repository if it doesn't exist.
- Add a professional **MIT License** and **README.md**.
- Commit code and push to `main` branch.
- Enable GitHub Pages automatically and wait until the page is live.
- Handle optional attachments (e.g., CSV, JSON, images).
- Send repo & commit details to an evaluation URL (optional).

---

## Environment Variables

Set these in a `.env` file:

```env
GEMINI_API_KEY=your_gemini_api_key
GITHUB_USERNAME=your_github_username
GITHUB_TOKEN=your_github_personal_access_token
SECRET_KEY=your_api_secret

## Request Body

{
  "secret": "your_secret_key",
  "email": "user@example.com",
  "task": "task-id",
  "round": 1,
  "nonce": "unique-nonce",
  "brief": "Describe the app to generate",
  "attachments": [
    {
      "name": "file.csv",
      "url": "data:text/csv;base64,..."
    }
  ],
  "evaluation_url": "https://example.com/notify"
}

## To run locally

pip install fastapi uvicorn gitpython python-dotenv requests google-genai
uvicorn main:app --host 0.0.0.0 --port 8000
