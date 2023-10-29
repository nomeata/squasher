import requests
from flask import Flask, redirect, request, session, url_for, render_template
from flask_wtf.csrf import CSRFProtect
from authlib.integrations.flask_client import OAuth # Import the OAuth class
import json
import html
import re
from markupsafe import Markup

app = Flask(__name__)
app.config.from_file("config.json", load=json.load, silent=True)
csrf = CSRFProtect(app)

oauth = OAuth(app)
github = oauth.register(
    name="github",
    client_id=app.config["CLIENT_ID"],
    client_secret=app.config["CLIENT_SECRET"],
    access_token_url="https://github.com/login/oauth/access_token",
    access_token_params=None,
    authorize_url="https://github.com/login/oauth/authorize",
    authorize_params=None,
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "repo,user"},
)

@app.route("/")
def index():
    user = get_user()
    return render_template('base.html', user=user)

@app.route("/login", methods=["POST"])
def login():
    session.clear()
    return github.authorize_redirect(url_for("callback", _external=True))

pr_re = re.compile(r"^https://github.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pr>[0-9]+)")

@app.route("/", methods=["POST"])
def squash():
    steps = []

    user = get_user()
    try:
        pull_url = request.form.get("pull_url")
        match = pr_re.match(pull_url)
        if not match:
            raise Exception(f"Does not look like a pull request url: “{pull_url}”")
        owner=match.group("owner")
        repo=match.group("repo")
        pr=match.group("pr")

        steps += [f"Will attempt to squash PR {pr} at {owner}/{repo}"]

        code = request.args.get("code")
        steps += ["Aquiring access token…"]
        access_token = get_access_token(code)
        steps += ["Got access token."]

        steps += ["Getting PR information…"]

        pull = get_api(f"repos/{owner}/{repo}/pulls/{pr}")
        pull_url = pull["html_url"]
        head_label = pull["head"]["label"]
        head_ref = pull["head"]["ref"]
        head_sha = pull["head"]["sha"]
        head_repo_owner = pull["head"]["repo"]["owner"]["login"]
        head_repo_name = pull["head"]["repo"]["name"]
        base_label = pull["base"]["label"]
        base_repo_owner = pull["base"]["repo"]["owner"]["login"]
        base_repo_name = pull["base"]["repo"]["name"]
        title = pull["title"]
        body = pull["body"]
        if not body:
            body = ""

        steps += [Markup(f"Pull request <a href='{html.escape(pull_url)}'>“{html.escape(title)}”</a> wants to merge branch <code>{html.escape(head_label)}</code> at commit <a href='https://github.com/{html.escape(head_repo_owner)}/{html.escape(head_repo_name)}/commit/{html.escape(head_sha)}'><code>{html.escape(head_sha[:7])}</code></a> into <code>{html.escape(base_label)}</code>.")]

        steps += ["Finding merge base…"]
        compare = get_api(f"repos/{head_repo_owner}/{head_repo_name}/compare/{base_label}...{head_label}")
        base_sha = compare["merge_base_commit"]["sha"]
        ahead_by = compare["ahead_by"]
        if ahead_by == 0:
            raise ValueError(f"Branch does not seem to contain new commits: {compare['url']}")

        steps += [Markup(f"Branch contains {ahead_by} commits on top of merge base <a href='https://github.com/{html.escape(base_repo_owner)}/{html.escape(base_repo_name)}/commit/{html.escape(base_sha)}'><code>{html.escape(base_sha[:7])}</code></a>.")]

        steps += ["Fetching commit content…"]
        head_commit = get_api(f"repos/{head_repo_owner}/{head_repo_name}/git/commits/{head_sha}")
        head_tree = head_commit["tree"]["sha"]
        steps += [Markup(f"The head commit refers to the tree object <code>{html.escape(head_tree[:7])}</code>.")]

        steps += ["Creating squashed commit"]
        new_commit = post_api(f"repos/{head_repo_owner}/{head_repo_name}/git/commits",{
          "message": title + "\r\n\r\n" + body,
          "tree": head_tree,
          "parents": [base_sha],
          "committer": { "name": "Squasher bot", "email": "squasher@joachim-breitner.de" },
        })
        new_commit_sha = new_commit["sha"]
        steps += [Markup(f"Squashed to commit <a href='{html.escape(new_commit['html_url'])}'<code>{html.escape(new_commit_sha[:7])}</code></a>.")]

        steps += ["Force pushing head branch…"]
        ref_update = post_api(f"repos/{head_repo_owner}/{head_repo_name}/git/refs/heads/{head_ref}",{
           "sha": new_commit_sha,
           "force": True,
        })
        steps += [Markup(f"Successfully updated <code>{html.escape(head_label)}</code>.")]
        steps += ["All done!"]
    except Exception as e:
        steps += [f"Failure: {e}"]

    return render_template('base.html', user=user, steps=steps, pull_url=pull_url)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/callback")
def callback():
    if "access_token" in session:
        return redirect(url_for("index"))
    code = request.args.get("code")
    access_token = get_access_token(code)
    session["access_token"] = access_token
    return redirect(url_for("index"))

def get_user():
    if "access_token" in session:
        try:
            user = get_api("user")
            if user:
                return user["login"]
            else:
                session.clear()
        except Exception as e:
            print(e)
            session.clear()
            return

def get_access_token(code):
    payload = {
        "client_id": app.config["CLIENT_ID"],
        "client_secret": app.config["CLIENT_SECRET"],
        "code": code,
    }

    headers = {
        "Accept": "application/json",
    }

    response = requests.post(
        "https://github.com/login/oauth/access_token",
        json=payload,
        headers=headers
    )

    if response.status_code in [200, 201]:
        resp = response.json()
        if "error" in resp:
            print("access_token", resp)
            return None
        else:
            access_token = resp["access_token"]
            return access_token
    return None

def post_api(path, data):
    access_token = session["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    response = requests.post(f"https://api.github.com/{path}", headers=headers, json=data)
    resp = response.json()
    if response.status_code in [200, 201]:
        return resp
    else:
        print(path, resp)
        if "error" in resp:
            raise Exception(f"{resp['error']}: {resp['error_description']}")
        elif "message" in resp:
            raise Exception(f"{resp['message']}")
        else:
            raise Exception(f"Request failed with code {response.status_code}")

def get_api(path):
    access_token = session["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    response = requests.get(f"https://api.github.com/{path}", headers=headers)
    resp = response.json()
    if response.status_code == 200:
        return resp
    else:
        print(path, resp)
        if "error" in resp:
            raise Exception(f"{resp['error']}: {resp['error_description']}")
        elif "message" in resp:
            raise Exception(f"{resp['message']}")
        else:
            raise Exception(f"Request failed with code {response.status_code}")

if __name__ == "__main__":
    app.run(debug=True)


