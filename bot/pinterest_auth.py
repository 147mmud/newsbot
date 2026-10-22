"""One-time Pinterest OAuth code -> encrypted refresh token (data/pinterest_auth.enc).

Run by .github/workflows/pinterest_auth.yml. Nothing secret is ever printed:
the repo is public, so the token is stored encrypted with the app secret as key
(Fernet, key = sha256(PINTEREST_APP_SECRET)). bot.pinterest_api decrypts it.
"""
import base64, hashlib, json, os, sys
import requests
from cryptography.fernet import Fernet

APP_ID = os.environ["PINTEREST_APP_ID"]
SECRET = os.environ["PINTEREST_APP_SECRET"]
CODE = os.environ["PINTEREST_CODE"].strip()
REDIRECT = os.getenv("PINTEREST_REDIRECT", "https://thepressandscore.com/")
OUT = "data/pinterest_auth.enc"


def fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest()))


def main():
    r = requests.post("https://api.pinterest.com/v5/oauth/token", auth=(APP_ID, SECRET), timeout=30,
                      data={"grant_type": "authorization_code", "code": CODE,
                            "redirect_uri": REDIRECT, "continuous_refresh": "true"})
    body = r.json() if r.content else {}
    if r.status_code != 200 or "refresh_token" not in body:
        print("Exchange failed:", r.status_code, {k: body.get(k) for k in ("code", "message", "error", "error_description")})
        return 1
    os.makedirs("data", exist_ok=True)
    open(OUT, "wb").write(fernet().encrypt(json.dumps({"refresh_token": body["refresh_token"]}).encode()))
    print("OK. scope:", body.get("scope"), "| refresh valid (days):", round(body.get("refresh_token_expires_in", 0) / 86400))
    return 0


if __name__ == "__main__":
    sys.exit(main())
