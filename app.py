import os
import time
import tempfile
import requests
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from git import Repo
from dotenv import load_dotenv
from datetime import datetime
from google import genai  # Gemini API
from urllib.parse import urlparse

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
    evaluation_url: str = None
    attachments: list[Attachment] = []

# ---------------- LLM CALL ----------------
def call_llm(brief: str) -> str:
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
    if code.startswith("```"):
        code = "\n".join(code.split("\n")[1:])
    if code.endswith("```"):
        code = "\n".join(code.split("\n")[:-1])
    return code.strip()

# ---------------- GITHUB HANDLING ----------------
def ensure_repo_exists(repo_name: str):
    """Create repo via GitHub API if not exists"""
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}"
    resp = requests.get(url, auth=(GITHUB_USERNAME, GITHUB_TOKEN))
    if resp.status_code == 404:
        # Create new repo
        data = {"name": repo_name, "private": False, "auto_init": False}
        resp = requests.post("https://api.github.com/user/repos", json=data,
                             auth=(GITHUB_USERNAME, GITHUB_TOKEN))
        resp.raise_for_status()
    elif resp.status_code >= 400:
        resp.raise_for_status()

def save_attachments(local_dir: str, attachments: list[Attachment]):
    for att in attachments:
        parsed = urlparse(att.url)
        if parsed.scheme.startswith("data"):  # Only support data URIs
            header, encoded = att.url.split(",", 1)
            data = encoded.encode()
            with open(os.path.join(local_dir, att.name), "wb") as f:
                f.write(base64.b64decode(data))

def create_or_update_repo(task_id: str, html_code: str, round_number: int, attachments: list[Attachment]):
    repo_name = f"{task_id}"
    ensure_repo_exists(repo_name)
    local_dir = os.path.join(tempfile.gettempdir(), repo_name)
    os.makedirs(local_dir, exist_ok=True)

    # Save attachments
    if attachments:
        import base64
        save_attachments(local_dir, attachments)

    # index.html
    with open(os.path.join(local_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_code)

    # LICENSE
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
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
""")

    # README
    with open(os.path.join(local_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"# {task_id}\n\nGenerated for round {round_number}\n\nBrief:\n{html_code[:500]}...\n\n## License\nMIT")

    # Git operations
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
    pages_api_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/pages"
    data = {"source": {"branch": "main", "path": "/"}}
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    resp = requests.post(pages_api_url, json=data, headers=headers)
    if resp.status_code not in [201, 204]:
        resp = requests.put(pages_api_url, json=data, headers=headers)
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

# ---------------- EVALUATION ----------------
def post_to_evaluation(data: dict, evaluation_url: str):
    if evaluation_url:
        try:
            requests.post(evaluation_url, json=data, timeout=10)
        except Exception as e:
            print("Eval submission failed:", e)

# ---------------- BACKGROUND TASK ----------------
def process_task(payload: TaskRequest):
    html_code = call_llm(payload.brief)
    repo_url, pages_url, commit_sha = create_or_update_repo(payload.task, html_code, payload.round, payload.attachments)

    eval_data = {
        "email": payload.email,
        "task": payload.task,
        "round": payload.round,
        "nonce": payload.nonce,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }
    post_to_evaluation(eval_data, payload.evaluation_url)

# ---------------- API ENDPOINT ----------------
@app.post("/api-endpoint")
async def handle_request(payload: TaskRequest, background_tasks: BackgroundTasks):
    if payload.secret != SECRET_KEY:
        return {"status": "error", "message": "Invalid secret"}

    # Run the full process in background
    background_tasks.add_task(process_task, payload)

    # Immediately return 200 OK
    return {"status": "ok"}
