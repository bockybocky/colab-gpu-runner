"""Two-step login for google-colab-cli so the auth code can be relayed from a phone.

Run with the colab-cli tool's own Python:
  PY=~/AppData/Roaming/uv/tools/google-colab-cli/Scripts/python.exe
  $PY colab_login.py url          # prints the Google sign-in URL, saves PKCE verifier
  $PY colab_login.py code <CODE>  # exchanges the code, writes ~/.config/colab-cli/token.json

Same client, scopes and redirect as colab_cli.auth._run_remote_flow; only the
wait-for-stdin step is split in two.
"""
import json
import os
import sys
from importlib import resources

from google_auth_oauthlib.flow import InstalledAppFlow
from colab_cli import auth

STATE = os.path.expanduser("~/.config/colab-cli/pending_login.json")


def _flow(verifier=None):
    cfg = json.loads(resources.files("colab_cli").joinpath("oauth_config.json").read_text())
    flow = InstalledAppFlow.from_client_config(cfg, auth.PUBLIC_SCOPES, code_verifier=verifier)
    flow.redirect_uri = auth.REMOTE_REDIRECT_URI
    return flow


def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    if sys.argv[1:2] == ["url"]:
        flow = _flow()
        url, _ = flow.authorization_url(prompt="consent", token_usage="remote")
        with open(STATE, "w") as f:
            json.dump({"code_verifier": flow.code_verifier}, f)
        print(url)
    elif sys.argv[1:2] == ["code"] and len(sys.argv) == 3:
        with open(STATE) as f:
            verifier = json.load(f)["code_verifier"]
        flow = _flow(verifier)
        flow.fetch_token(code=sys.argv[2].strip())
        with open(auth.TOKEN_CONFIG_PATH, "w") as f:
            f.write(flow.credentials.to_json())
        os.remove(STATE)
        print("token saved:", auth.TOKEN_CONFIG_PATH)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
