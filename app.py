import os
import time
import tempfile
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from git import Repo
from dotenv import load_dotenv
from datetime import datetime
from google import genai  # Gemini API

load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY")

client = genai.Client()  # Gemini client
app = FastAPI()


class Attachment(BaseModel):
    name: str
    url: str


class TaskRequest(BaseModel):
    secret: str
    email: str
    task: str
    nonce: str
    brief: str
    round: int = 1
    attachments: list[Attachment] = []
    evaluation_url: str = None  # optional
    checks: list = []  # optional


# ---------------- LLM CALL ----------------
async def call_llm(brief: str) -> str:
    prompt = (
        "You are a code generator. "
        "Return only a single HTML/JS/CSS code block for the app requested. "
        "Do NOT add any explanations, comments, or extra text.\n\n"
        f"Task brief: {brief}\nReturn ONLY the code block, nothing else."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    code = response.text

    # Strip ``` if present
    if code.startswith("```"):
        code = "\n".join(code.split("\n")[1:])
    if code.endswith("```"):
        code = "\n".join(code.split("\n")[:-1])
    return code.strip()


# ---------------- GITHUB HANDLING ----------------
def ensure_repo_exists(repo_name: str):
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}"
    resp = requests.get(url, auth=(GITHUB_USERNAME, GITHUB_TOKEN))
    if resp.status_code == 404:
        # Create repo
        data = {"name": repo_name, "private": False, "auto_init": False}
        resp = requests.post("https://api.github.com/user/repos", json=data,
                             auth=(GITHUB_USERNAME, GITHUB_TOKEN))
        resp.raise_for_status()
    elif resp.status_code >= 400:
        resp.raise_for_status()


def save_attachments(attachments: list, repo_dir: str):
    for att in attachments:
        file_path = os.path.join(repo_dir, att.name)
        if att.url.startswith("data:"):  # data URI
            header, encoded = att.url.split(",", 1)
            import base64
            with open(file_path, "wb") as f:
                f.write(base64.b64decode(encoded))


def create_or_update_repo(task_id: str, html_code: str, round_number: int, attachments=[]):
    repo_name = f"{task_id}"
    ensure_repo_exists(repo_name)

    local_dir = os.path.join(tempfile.gettempdir(), repo_name)
    os.makedirs(local_dir, exist_ok=True)

    # Save attachments
    save_attachments(attachments, local_dir)

    # Write index.html
    with open(os.path.join(local_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_code)

    # Write LICENSE
    license_path = os.path.join(local_dir, "LICENSE")
    if not os.path.exists(license_path):
        with open(license_path, "w", encoding="utf-8") as f:
            f.write(f"""MIT License

Copyright (c) {datetime.now().year} {GITHUB_USERNAME}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
""")

    # README
    with open(os.path.join(local_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"# {task_id}\n\nGenerated for round {round_number}\n\nBrief:\n{html_code[:500]}...\n\n## License\nMIT")

    # Git
    if not os.path.exists(os.path.join(local_dir, ".git")):
        repo = Repo.init(local_dir)
    else:
        repo = Repo(local_dir)

    repo.git.add(A=True)
    repo.index.commit(f"Round {round_number} update")
    repo.git.branch("-M", "main")

    remote_url = f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{repo_name}.git"
    if "origin" not in [r.name for r in repo.remotes]:
        repo.create_remote("origin", remote_url)
    else:
        repo.remotes.origin.set_url(remote_url)

    repo.remotes.origin.push("main", force=True)

    # Enable GitHub Pages
    pages_url_api = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/pages"
    data = {"source": {"branch": "main", "path": "/"}}
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    resp = requests.post(pages_url_api, json=data, headers=headers)
    if resp.status_code not in [201, 204]:
        # Already enabled? Try PUT
        resp = requests.put(pages_url_api, json=data, headers=headers)
        resp.raise_for_status()

    # Wait until Pages is live
    live_url = f"https://{GITHUB_USERNAME}.github.io/{repo_name}/"
    for _ in range(20):
        r = requests.get(live_url)
        if r.status_code == 200:
            break
        time.sleep(3)

    commit_sha = repo.head.commit.hexsha
    repo_url = f"https://github.com/{GITHUB_USERNAME}/{repo_name}"
    return repo_url, live_url, commit_sha


# ---------------- API ENDPOINT ----------------
@app.post("/api-endpoint")
async def handle_request(payload: TaskRequest):
    if payload.secret != SECRET_KEY:
        return {"status": "error", "message": "Invalid secret"}

    html_code = await call_llm(payload.brief)

    repo_url, pages_url, commit_sha = create_or_update_repo(
        payload.task, html_code, payload.round, attachments=payload.attachments
    )

    # ---------------- POST TO EVALUATION (optional) ----------------
    if payload.evaluation_url:
        eval_data = {
            "email": payload.email,
            "task": payload.task,
            "round": payload.round,
            "nonce": payload.nonce,
            "repo_url": repo_url,
            "commit_sha": commit_sha,
            "pages_url": pages_url
        }
        try:
            requests.post(payload.evaluation_url, json=eval_data, timeout=10)
        except Exception as e:
            print("Evaluation post failed:", e)

    # ---------------- RETURN RESPONSE ----------------
    # If you don't want to return anything, comment this
    return {"status": "ok", "repo": repo_url, "pages_url": pages_url, "commit_sha": commit_sha}
